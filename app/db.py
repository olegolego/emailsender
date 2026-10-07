"""SQLite storage. One file on this computer: data/emailmanager.db"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("EMAILMANAGER_DATA") or BASE_DIR / "data")
DB_PATH = DATA_DIR / "emailmanager.db"

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT,
  email TEXT NOT NULL,
  from_name TEXT,
  username TEXT,
  smtp_host TEXT DEFAULT 'smtp.gmail.com',
  smtp_port INTEGER DEFAULT 587,
  smtp_security TEXT DEFAULT 'starttls',
  imap_host TEXT DEFAULT 'imap.gmail.com',
  imap_port INTEGER DEFAULT 993,
  imap_folders TEXT DEFAULT 'INBOX',
  daily_limit INTEGER DEFAULT 30,
  min_gap_minutes REAL DEFAULT 4,
  signature TEXT DEFAULT '',
  active INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  domain TEXT,
  website TEXT,
  category TEXT,
  notes TEXT DEFAULT '',
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_companies_domain ON companies(domain);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '',
  title TEXT DEFAULT '',
  company_id INTEGER REFERENCES companies(id),
  phone TEXT DEFAULT '',
  timezone TEXT DEFAULT '',
  tags TEXT DEFAULT '',
  custom_json TEXT DEFAULT '{}',
  status TEXT DEFAULT 'new',
  notes TEXT DEFAULT '',
  created_at TEXT,
  last_contacted_at TEXT,
  last_reply_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_contacts_company ON contacts(company_id);
CREATE TABLE IF NOT EXISTS pipelines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  definition TEXT NOT NULL,
  settings TEXT NOT NULL,
  status TEXT DEFAULT 'draft',
  account_id INTEGER REFERENCES accounts(id),
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS enrollments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id INTEGER NOT NULL REFERENCES pipelines(id),
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  account_id INTEGER REFERENCES accounts(id),
  state TEXT DEFAULT 'active',
  current_node TEXT,
  phase TEXT DEFAULT 'enter',
  node_entered_at TEXT,
  next_action_at TEXT,
  thread_root_id TEXT,
  last_message_id TEXT,
  last_subject TEXT,
  references_hdr TEXT DEFAULT '',
  outcome TEXT DEFAULT '',
  stopped_reason TEXT DEFAULT '',
  path TEXT DEFAULT '[]',
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_enr_due ON enrollments(state, next_action_at);
CREATE INDEX IF NOT EXISTS ix_enr_contact ON enrollments(contact_id);
CREATE TABLE IF NOT EXISTS queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT DEFAULT 'pipeline',
  enrollment_id INTEGER REFERENCES enrollments(id),
  pipeline_id INTEGER,
  node_id TEXT,
  contact_id INTEGER REFERENCES contacts(id),
  account_id INTEGER REFERENCES accounts(id),
  to_addr TEXT,
  subject TEXT,
  body TEXT,
  in_reply_to TEXT DEFAULT '',
  references_hdr TEXT DEFAULT '',
  scheduled_at TEXT,
  respect_window INTEGER DEFAULT 1,
  status TEXT DEFAULT 'pending',
  approval_reason TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  created_at TEXT,
  sent_at TEXT,
  message_row_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_queue_due ON queue(status, scheduled_at);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL,
  account_id INTEGER,
  contact_id INTEGER,
  company_id INTEGER,
  enrollment_id INTEGER,
  pipeline_id INTEGER,
  node_id TEXT,
  queue_id INTEGER,
  message_id TEXT,
  in_reply_to TEXT DEFAULT '',
  references_hdr TEXT DEFAULT '',
  from_addr TEXT,
  from_name TEXT DEFAULT '',
  to_addr TEXT,
  subject TEXT,
  body TEXT,
  reply_text TEXT DEFAULT '',
  ts TEXT,
  replied_to_id INTEGER,
  is_test INTEGER DEFAULT 0,
  is_bounce INTEGER DEFAULT 0,
  is_auto INTEGER DEFAULT 0,
  category TEXT DEFAULT '',
  sentiment TEXT DEFAULT '',
  interest_score INTEGER,
  summary TEXT DEFAULT '',
  analysis_json TEXT DEFAULT '',
  analyzed_at TEXT,
  analysis_source TEXT DEFAULT '',
  handled INTEGER DEFAULT 0,
  folder TEXT DEFAULT '',
  imap_uid INTEGER
);
CREATE INDEX IF NOT EXISTS ix_msg_mid ON messages(message_id);
CREATE INDEX IF NOT EXISTS ix_msg_contact ON messages(contact_id, ts);
CREATE INDEX IF NOT EXISTS ix_msg_dir_ts ON messages(direction, ts);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  kind TEXT,
  contact_id INTEGER,
  enrollment_id INTEGER,
  message_row_id INTEGER,
  detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_events_contact ON events(contact_id, ts);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  pipeline_id INTEGER REFERENCES pipelines(id),
  account_id INTEGER REFERENCES accounts(id),
  status TEXT DEFAULT 'running',
  start_at TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS batch_members (
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  added_at TEXT,
  PRIMARY KEY (batch_id, contact_id)
);
CREATE INDEX IF NOT EXISTS ix_bm_contact ON batch_members(contact_id);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  level TEXT DEFAULT 'info',
  title TEXT,
  body TEXT DEFAULT '',
  link TEXT DEFAULT '',
  read INTEGER DEFAULT 0
);
"""

DEFAULT_SETTINGS = {
    "timezone": "America/Detroit",
    "test_mode": True,
    "test_address": "",
    "sending_paused": False,
    "anthropic_model": "claude-sonnet-5-5",
    "ai_enabled": True,
    "business_context": (
        "I am Oleg Presnyakov, founder of Eigenreach (eigenreach.com), an e-commerce retailer in "
        "Ann Arbor, Michigan, selling consumer products online since 2021. Our team of mathematicians "
        "uses search-demand data to decide what to stock. We email distributors and brands to open "
        "wholesale accounts, get catalogs and price lists, and become an authorized retailer. "
        "We follow MAP and brand policies."
    ),
    "inbound_scope": "contacts",
    "imap_interval_minutes": 2,
    "imap_initial_days": 14,
    "notify_macos": True,
    "notify_ntfy_topic": "",
    "notify_on": ["reply", "interested", "bounce", "failure", "approval"],
    "daily_summary_hour": 18,
    "footer": "",
    "list_unsubscribe": True,
}


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(utcnow())


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


MIGRATIONS = [
    ("enrollments", "batch_id", "INTEGER"),
    ("companies", "source", "TEXT DEFAULT ''"),
    ("companies", "plan", "TEXT DEFAULT ''"),          # email | form | contacted_before | duplicate | no_contact
    ("companies", "form_url", "TEXT DEFAULT ''"),
    ("companies", "form_status", "TEXT DEFAULT ''"),   # '' | submitted | not_possible
    ("companies", "crm_note", "TEXT DEFAULT ''"),
    ("companies", "crm_row", "INTEGER"),
    ("companies", "brands", "TEXT DEFAULT ''"),
    ("companies", "updated_at", "TEXT"),
    ("batches", "source", "TEXT DEFAULT ''"),
    ("batches", "note", "TEXT DEFAULT ''"),
    ("batches", "priority", "INTEGER DEFAULT 9"),
    ("batches", "settings", "TEXT DEFAULT '{}'"),        # per-batch send days, hours, daily cap
]


def close():
    """Close this thread's connection (each web request runs in its own short-lived thread)."""
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        finally:
            _local.conn = None


def init():
    c = conn()
    c.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    c.execute("CREATE INDEX IF NOT EXISTS ix_companies_plan ON companies(plan, source)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_msg_company ON messages(company_id, direction)")
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def scalar(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return r[0] if r else None


def execute(sql, args=()):
    with _write_lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


def insert(table, data):
    cols = ",".join(data.keys())
    ph = ",".join("?" for _ in data)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", tuple(data.values()))


def update(table, row_id, data):
    if not data:
        return
    sets = ",".join(f"{k}=?" for k in data)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", tuple(data.values()) + (row_id,))


class transaction:
    """Serialises multi-statement writes across threads."""

    def __enter__(self):
        _write_lock.acquire()
        conn().execute("BEGIN IMMEDIATE")
        return conn()

    def __exit__(self, exc_type, exc, tb):
        try:
            conn().execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            _write_lock.release()
        return False


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    if r is None:
        return DEFAULT_SETTINGS.get(key, default)
    return json.loads(r["value"])


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))


def all_settings():
    out = dict(DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        if r["key"].startswith("_"):
            continue
        out[r["key"]] = json.loads(r["value"])
    return out


def log_event(kind, contact_id=None, enrollment_id=None, message_row_id=None, detail=""):
    insert("events", {"ts": now_iso(), "kind": kind, "contact_id": contact_id,
                      "enrollment_id": enrollment_id, "message_row_id": message_row_id,
                      "detail": detail if isinstance(detail, str) else json.dumps(detail)})


def days_ago_iso(days):
    return iso(utcnow() - timedelta(days=days))
