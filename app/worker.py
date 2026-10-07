"""Background loop: advance pipelines, send due emails, check the inbox, daily summary."""
import threading
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, engine, mailer
from .notifier import notify

TICK_SECONDS = 15

status = {
    "running": False, "started_at": None, "last_tick": None, "last_inbox_check": None,
    "last_inbox_result": "", "last_error": "", "inbox_checking": False,
}
_wake = threading.Event()
_inbox_requested = False
_inbox_lock = threading.Lock()


def check_inbox():
    if not _inbox_lock.acquire(blocking=False):
        return "already checking"
    status["inbox_checking"] = True
    try:
        found, errors = 0, []
        for acc in db.q("SELECT * FROM accounts WHERE active=1"):
            folders = [f.strip() for f in (acc["imap_folders"] or "INBOX").split(",") if f.strip()]
            spam = mailer.spam_folder(acc)  # supplier replies often land in Spam
            if spam and spam not in folders:
                folders.append(spam)
            for folder in folders:
                try:
                    items, mark = mailer.fetch_new(acc, folder, wanted=engine.wants_header)
                    for uid, raw in items:
                        try:
                            if raw is not None and engine.process_inbound(acc, raw, folder):
                                found += 1
                        except Exception as ex:
                            errors.append(f"{acc['email']} uid {uid}: {ex}")
                            traceback.print_exc()
                        mark(uid)
                except Exception as ex:
                    errors.append(f"{acc['email']}/{folder}: {ex}")
        status["last_inbox_check"] = db.now_iso()
        status["last_inbox_result"] = f"{found} new" + (f" · {len(errors)} error(s): {errors[0]}" if errors else "")
        if errors:
            status["last_error"] = errors[0]
        return status["last_inbox_result"]
    finally:
        status["inbox_checking"] = False
        _inbox_lock.release()


def _check_inbox_thread():
    try:
        check_inbox()
    except Exception as ex:
        status["last_error"] = f"{db.now_iso()} inbox: {ex}"
        traceback.print_exc()
    finally:
        db.close()


def daily_summary():
    hour = db.get_setting("daily_summary_hour")
    if hour in (None, "", -1):
        return
    tz = ZoneInfo(db.get_setting("timezone") or "America/Detroit")
    now = datetime.now(tz)
    key = now.strftime("%Y-%m-%d")
    if now.hour < int(hour) or db.get_setting("_last_summary") == key:
        return
    db.set_setting("_last_summary", key)
    since = db.days_ago_iso(1)
    sent = db.scalar("SELECT COUNT(*) FROM messages WHERE direction='out' AND is_test=0 AND ts>=?", (since,)) or 0
    replies = db.scalar("SELECT COUNT(*) FROM messages WHERE direction='in' AND is_bounce=0 AND ts>=? "
                        "AND category NOT IN ('auto_reply','out_of_office')", (since,)) or 0
    hot = db.scalar("SELECT COUNT(*) FROM messages WHERE direction='in' AND ts>=? AND category='interested'", (since,)) or 0
    pending = db.scalar("SELECT COUNT(*) FROM queue WHERE status='needs_approval'") or 0
    unhandled = db.scalar("SELECT COUNT(*) FROM messages WHERE direction='in' AND handled=0") or 0
    notify("summary", "Daily summary",
           f"Last 24h: {sent} sent, {replies} replies ({hot} interested). "
           f"{unhandled} replies to handle, {pending} emails awaiting approval.", "#/", "info")


def loop():
    global _inbox_requested
    status["running"] = True
    status["started_at"] = db.now_iso()
    db.init()
    last_inbox = 0.0
    while True:
        try:
            status["last_tick"] = db.now_iso()
            engine.process_due()
            engine.send_due()
            interval = float(db.get_setting("imap_interval_minutes") or 2) * 60
            if (_inbox_requested or time.time() - last_inbox >= interval) and not _inbox_lock.locked():
                _inbox_requested = False
                last_inbox = time.time()
                if db.q1("SELECT id FROM accounts WHERE active=1"):
                    # own thread: a slow mailbox must never hold up sending
                    threading.Thread(target=_check_inbox_thread, name="emailmanager-inbox", daemon=True).start()
            daily_summary()
        except Exception as ex:
            status["last_error"] = f"{db.now_iso()} {ex}"
            traceback.print_exc()
        _wake.wait(TICK_SECONDS)
        _wake.clear()


def wake():
    _wake.set()


def request_inbox_check():
    global _inbox_requested
    _inbox_requested = True
    _wake.set()


def start():
    t = threading.Thread(target=loop, name="emailmanager-worker", daemon=True)
    t.start()
    return t
