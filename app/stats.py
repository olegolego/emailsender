"""Metrics: who replied, when, how fast, per pipeline / per step / per send hour."""
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db

REAL_REPLY = "m.direction='in' AND m.is_bounce=0 AND COALESCE(m.category,'') NOT IN ('auto_reply','out_of_office','bounce')"
POSITIVE = ("interested", "question", "referral")


def _tz():
    return ZoneInfo(db.get_setting("timezone") or "America/Detroit")


def _where(pipeline_id, alias="m"):
    if pipeline_id:
        return f" AND {alias}.pipeline_id={int(pipeline_id)}", ()
    return "", ()


def summary(pipeline_id=None, days=30):
    since = db.days_ago_iso(days)
    pw, _ = _where(pipeline_id)
    sent = db.scalar(f"SELECT COUNT(*) FROM messages m WHERE direction='out' AND is_test=0 AND ts>=?{pw}", (since,)) or 0
    contacted = db.scalar(f"SELECT COUNT(DISTINCT contact_id) FROM messages m WHERE direction='out' AND is_test=0 AND ts>=?{pw}",
                          (since,)) or 0
    replied = db.scalar(f"SELECT COUNT(DISTINCT m.contact_id) FROM messages m WHERE {REAL_REPLY} AND m.ts>=?{pw}", (since,)) or 0
    positive = db.scalar(f"SELECT COUNT(DISTINCT m.contact_id) FROM messages m WHERE {REAL_REPLY} AND m.ts>=? "
                         f"AND m.category IN ('interested','question','referral'){pw}", (since,)) or 0
    interested = db.scalar(f"SELECT COUNT(DISTINCT m.contact_id) FROM messages m WHERE m.direction='in' AND m.ts>=? "
                           f"AND m.category='interested'{pw}", (since,)) or 0
    bounced = db.scalar(f"SELECT COUNT(DISTINCT m.contact_id) FROM messages m WHERE m.direction='in' AND m.is_bounce=1 AND m.ts>=?{pw}",
                        (since,)) or 0
    unsub = db.scalar(f"SELECT COUNT(DISTINCT m.contact_id) FROM messages m WHERE m.direction='in' AND m.category='unsubscribe' "
                      f"AND m.ts>=?{pw}", (since,)) or 0
    cats = db.q(f"SELECT COALESCE(NULLIF(category,''),'unanalysed') AS category, COUNT(*) AS n FROM messages m "
                f"WHERE direction='in' AND ts>=?{pw} GROUP BY 1 ORDER BY n DESC", (since,))
    hours = reply_delays(pipeline_id, since)
    return {
        "days": days, "sent": sent, "contacted": contacted, "replied": replied, "positive": positive,
        "interested": interested, "bounced": bounced, "unsubscribed": unsub,
        "reply_rate": round(100 * replied / contacted, 1) if contacted else 0,
        "positive_rate": round(100 * positive / contacted, 1) if contacted else 0,
        "bounce_rate": round(100 * bounced / contacted, 1) if contacted else 0,
        "median_reply_hours": round(statistics.median(hours), 1) if hours else None,
        "avg_reply_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "categories": cats,
    }


def reply_delays(pipeline_id=None, since=None):
    """Hours from the first email to that contact to their first real reply."""
    pw, _ = _where(pipeline_id)
    rows = db.q(f"SELECT m.contact_id, MIN(m.ts) AS first_in FROM messages m WHERE {REAL_REPLY}"
                f"{' AND m.ts>=?' if since else ''}{pw} GROUP BY m.contact_id", (since,) if since else ())
    out = []
    for r in rows:
        first_out = db.scalar("SELECT MIN(ts) FROM messages WHERE direction='out' AND is_test=0 AND contact_id=? AND ts<=?",
                              (r["contact_id"], r["first_in"]))
        if first_out:
            out.append((db.parse_iso(r["first_in"]) - db.parse_iso(first_out)).total_seconds() / 3600)
    return out


def daily(pipeline_id=None, days=30):
    tz = _tz()
    since = db.days_ago_iso(days)
    pw, _ = _where(pipeline_id)
    buckets = defaultdict(lambda: {"sent": 0, "replies": 0, "positive": 0})
    for r in db.q(f"SELECT ts FROM messages m WHERE direction='out' AND is_test=0 AND ts>=?{pw}", (since,)):
        buckets[db.parse_iso(r["ts"]).astimezone(tz).strftime("%Y-%m-%d")]["sent"] += 1
    for r in db.q(f"SELECT ts, category FROM messages m WHERE {REAL_REPLY} AND m.ts>=?{pw}", (since,)):
        d = buckets[db.parse_iso(r["ts"]).astimezone(tz).strftime("%Y-%m-%d")]
        d["replies"] += 1
        if r["category"] in POSITIVE:
            d["positive"] += 1
    today = datetime.now(tz).date()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": day, **buckets[day]})
    return out


def by_send_time(pipeline_id=None, days=90):
    """Reply rate by weekday x hour that the email was sent (local time)."""
    tz = _tz()
    since = db.days_ago_iso(days)
    pw, _ = _where(pipeline_id)
    rows = db.q(f"SELECT m.id, m.ts, EXISTS(SELECT 1 FROM messages r WHERE r.replied_to_id=m.id AND r.direction='in' "
                f"AND r.is_bounce=0 AND COALESCE(r.category,'') NOT IN ('auto_reply','out_of_office','bounce')) AS got "
                f"FROM messages m WHERE m.direction='out' AND m.is_test=0 AND m.ts>=?{pw}", (since,))
    grid = defaultdict(lambda: [0, 0])
    for r in rows:
        t = db.parse_iso(r["ts"]).astimezone(tz)
        g = grid[(t.weekday(), t.hour)]
        g[0] += 1
        g[1] += 1 if r["got"] else 0
    return [{"weekday": k[0], "hour": k[1], "sent": v[0], "replies": v[1]} for k, v in sorted(grid.items())]


def by_step(pipeline_id):
    p = db.q1("SELECT definition FROM pipelines WHERE id=?", (pipeline_id,))
    if not p:
        return []
    nodes = json.loads(p["definition"]).get("nodes", [])
    out = []
    for n in nodes:
        if n.get("type") not in ("email", "ai_email"):
            continue
        sent = db.scalar("SELECT COUNT(*) FROM messages WHERE direction='out' AND is_test=0 AND pipeline_id=? AND node_id=?",
                         (pipeline_id, n["id"])) or 0
        cats = db.q(f"SELECT m.category, COUNT(*) AS n FROM messages m WHERE {REAL_REPLY} AND m.pipeline_id=? "
                    f"AND m.node_id=? GROUP BY m.category", (pipeline_id, n["id"]))
        replies = sum(c["n"] for c in cats)
        queued = db.scalar("SELECT COUNT(*) FROM queue WHERE pipeline_id=? AND node_id=? AND status IN ('pending','needs_approval')",
                           (pipeline_id, n["id"])) or 0
        waiting = db.scalar("SELECT COUNT(*) FROM enrollments WHERE pipeline_id=? AND current_node=? AND state='active'",
                            (pipeline_id, n["id"])) or 0
        out.append({"node_id": n["id"], "name": n.get("name") or n["id"], "sent": sent, "replies": replies,
                    "reply_rate": round(100 * replies / sent, 1) if sent else 0, "queued": queued, "here_now": waiting,
                    "categories": {c["category"]: c["n"] for c in cats}})
    return out


def pipeline_overview():
    out = []
    for p in db.q("SELECT id, name, status, created_at FROM pipelines ORDER BY id DESC"):
        states = {r["state"]: r["n"] for r in db.q("SELECT state, COUNT(*) AS n FROM enrollments WHERE pipeline_id=? GROUP BY state",
                                                    (p["id"],))}
        s = summary(p["id"], days=3650)
        out.append({**p, "enrolled": sum(states.values()), "states": states, "sent": s["sent"],
                    "replied": s["replied"], "reply_rate": s["reply_rate"], "interested": s["interested"],
                    "positive_rate": s["positive_rate"], "bounce_rate": s["bounce_rate"],
                    "queued": db.scalar("SELECT COUNT(*) FROM queue WHERE pipeline_id=? AND status IN ('pending','needs_approval')",
                                        (p["id"],)) or 0})
    return out


def leaderboard(days=90, limit=15):
    """Companies with the most engagement."""
    since = db.days_ago_iso(days)
    return db.q(f"SELECT co.id, co.name, co.domain, COUNT(DISTINCT m.id) AS replies, MAX(m.interest_score) AS best_score, "
                f"MAX(m.ts) AS last_reply FROM messages m JOIN companies co ON co.id=m.company_id "
                f"WHERE {REAL_REPLY} AND m.ts>=? GROUP BY co.id ORDER BY best_score DESC, replies DESC LIMIT ?",
                (since, limit))
