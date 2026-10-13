"""Simple sequences: Email 1 → (reply: notify me & stop) → no reply after N days → Email 2 → ...

A sequence is stored as a list of steps and compiled into the pipeline engine's node format, so the
engine, queue, reply routing and statistics keep working unchanged."""
import csv
import io
import json
import re
import uuid

from . import db, engine, schedule, templating

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

DEFAULT_SETTINGS = {**schedule.DEFAULT_PIPELINE_SETTINGS, "days": [0, 1, 2, 3, 4], "window_start": "09:00",
                    "window_end": "17:00", "jitter_minutes": 20, "stop_on_reply": True, "stop_company_on_reply": True}


# ---------------------------------------------------------------- steps <-> engine nodes

def compile_steps(steps):
    nodes = []
    for i, s in enumerate(steps):
        nxt = steps[i + 1]["id"] if i + 1 < len(steps) else "end"
        same = i > 0 and s.get("same_thread", True)
        nodes.append({
            "id": s["id"], "type": "email", "name": "Email 1" if i == 0 else f"Follow-up {i}",
            "thread": "reply" if same else "new", "subject": "" if same else (s.get("subject") or ""),
            "body": s.get("body") or "", "delay": {"value": 0, "unit": "minutes"},
            "wait": {"value": float(s.get("wait_days") or 3), "unit": "days"},
            "signature": True, "approval": False, "branches": {"no_reply": nxt},
        })
    return {"version": 2, "start": steps[0]["id"] if steps else "", "steps": steps, "nodes": nodes}


def steps_of(definition):
    """Steps for a stored definition; older graph pipelines are read along their 'no reply' path."""
    if definition.get("version") == 2:
        return definition["steps"]
    nodes = {n["id"]: n for n in definition.get("nodes", [])}
    steps, cur, seen = [], definition.get("start"), set()
    while cur in nodes and cur not in seen:
        seen.add(cur)
        n = nodes[cur]
        if n.get("type") in ("email", "ai_email"):
            w = n.get("wait") or {"value": 3, "unit": "days"}
            days = schedule.duration(w).total_seconds() / 86400
            steps.append({"id": n["id"], "subject": n.get("subject") or "", "body": n.get("body") or n.get("prompt") or "",
                          "same_thread": n.get("thread") == "reply", "wait_days": round(days, 2) or 3})
            cur = (n.get("branches") or {}).get("no_reply")
        elif n.get("type") in ("wait", "notify"):
            cur = n.get("next")
        else:
            break
    return steps


def validate(steps):
    errs = []
    if not steps:
        errs.append("Add at least one email.")
    for i, s in enumerate(steps):
        label = "Email 1" if i == 0 else f"Follow-up {i}"
        if not (s.get("body") or "").strip():
            errs.append(f"{label} has no message.")
        if (i == 0 or not s.get("same_thread", True)) and not (s.get("subject") or "").strip():
            errs.append(f"{label} needs a subject.")
        if float(s.get("wait_days") or 0) <= 0:
            errs.append(f"{label}: days to wait must be more than 0.")
    return errs


def to_api(p_row):
    definition = json.loads(p_row["definition"]) if isinstance(p_row["definition"], str) else p_row["definition"]
    settings = json.loads(p_row["settings"]) if isinstance(p_row["settings"], str) else p_row["settings"]
    return {"id": p_row["id"], "name": p_row["name"], "steps": steps_of(definition),
            "settings": {**DEFAULT_SETTINGS, **(settings or {})}, "status": p_row["status"]}


def save(pid, name, steps, settings):
    for i, s in enumerate(steps):
        s["id"] = s.get("id") or "e" + uuid.uuid4().hex[:6]
        s["wait_days"] = float(s.get("wait_days") or 3)
        s["same_thread"] = bool(s.get("same_thread", True)) if i else False
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        for i, s in enumerate(steps):
            s["id"] = f"{s['id']}_{i}"
    errs = validate(steps)
    definition = compile_steps(steps)
    row = {"name": name or "Untitled sequence", "definition": json.dumps(definition),
           "settings": json.dumps({**DEFAULT_SETTINGS, **(settings or {})}), "updated_at": db.now_iso(),
           "status": "active" if not errs else "draft"}
    if pid:
        db.update("pipelines", pid, row)
        keep = set(ids)
        for e in db.q("SELECT * FROM enrollments WHERE pipeline_id=? AND state IN ('active','paused')", (pid,)):
            if e["current_node"] not in keep:
                engine.finish(e, "stop", "its email was removed from the sequence")
    else:
        row["created_at"] = db.now_iso()
        pid = db.insert("pipelines", {**row, "description": ""})
    return pid, errs


# ---------------------------------------------------------------- examples

EXAMPLES = [
    {
        "name": "Example · 1 email + follow-up after 3 days",
        "steps": [
            {"subject": "Wholesale account - Eigenreach",
             "body": "Hi {{first_name|there}},\n\nI'm Oleg, founder of Eigenreach, an online retailer in Ann Arbor, MI. "
                     "We'd like to open a wholesale account with {{company|your company}} and see your current price list.\n\n"
                     "Could you share it, along with your account requirements?\n\nThank you,\nOleg",
             "wait_days": 3},
            {"same_thread": True,
             "body": "Hi {{first_name|there}},\n\nJust following up on my note above - is there a better person or an "
                     "online application I should use?\n\nThanks,\nOleg",
             "wait_days": 4},
        ],
    },
    {
        "name": "Example · intro + 2 follow-ups (3 and 5 days)",
        "steps": [
            {"subject": "Wholesale account inquiry - Eigenreach (Ann Arbor, MI)",
             "body": "Hi {{first_name|there}},\n\nI'm Oleg Presnyakov, founder of Eigenreach, an e-commerce retailer based in "
                     "Ann Arbor, Michigan. We've been selling consumer products online since 2021.\n\nWe'd like to open a "
                     "wholesale account with {{company|your company}} and review your catalog and price list.\n\n"
                     "We follow MAP and brand policies and reorder on a steady schedule. Could you share your price list "
                     "and account requirements (order minimums, payment terms)?\n\nThank you,",
             "wait_days": 3},
            {"same_thread": True,
             "body": "Hi {{first_name|there}},\n\nFollowing up on my note about opening a wholesale account. If there's a "
                     "better contact or an online application, a pointer would be much appreciated.\n\nThanks,\nOleg",
             "wait_days": 5},
            {"same_thread": True,
             "body": "Hi {{first_name|there}},\n\nI haven't heard back, so I'll assume the timing isn't right. If "
                     "{{company|you}} takes on new online retailers later, we'd be glad to reconnect.\n\nBest,\nOleg",
             "wait_days": 5},
        ],
    },
    {
        "name": "Example · one email only",
        "steps": [
            {"subject": "Quick question, {{first_name|there}}",
             "body": "Hi {{first_name|there}},\n\nDo you work with online retailers on wholesale accounts? If so, who "
                     "would be the right person to talk to?\n\nThanks,\nOleg",
             "wait_days": 5},
        ],
    },
]


def seed_examples():
    if db.get_setting("_examples_v2"):
        return
    for ex in EXAMPLES:
        if not db.q1("SELECT id FROM pipelines WHERE name=?", (ex["name"],)):
            steps = [dict(s, id=f"e{i + 1}") for i, s in enumerate(ex["steps"])]
            save(None, ex["name"], steps, {})
    db.set_setting("_examples_v2", True)


# ---------------------------------------------------------------- recipients

ALIASES = {
    "email": ["email", "e-mail", "email address", "mail"],
    "first_name": ["first_name", "first name", "firstname", "first"],
    "last_name": ["last_name", "last name", "lastname", "last", "surname"],
    "full_name": ["name", "full name", "contact", "contact name"],
    "company": ["company", "company name", "organization", "organisation", "distributor", "brand", "business"],
    "website": ["website", "url", "site", "domain"],
    "title": ["title", "job title", "position", "role"],
    "category": ["category", "industry", "niche"],
}


def _split_name(rec):
    if rec.get("full_name") and not rec.get("first_name"):
        parts = rec["full_name"].split()
        rec["first_name"], rec["last_name"] = parts[0], " ".join(parts[1:])
    rec.pop("full_name", None)
    return rec


def parse_recipients(text):
    """Accepts a CSV with a header row, or one person per line: 'email, name, company'."""
    text = (text or "").strip()
    if not text:
        return [], []
    first = text.splitlines()[0].lower()
    out, errors, seen = [], [], set()
    if "email" in first and any(d in first for d in ",;\t"):
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        mapping = {}
        for h in reader.fieldnames or []:
            for field, names in ALIASES.items():
                if h and h.strip().lower() in names and field not in mapping.values():
                    mapping[h] = field
        for i, row in enumerate(reader, start=2):
            rec, custom = {}, {}
            for h, v in row.items():
                if h is None:
                    continue
                v = (v or "").strip()
                if h in mapping:
                    rec[mapping[h]] = v
                elif v:
                    custom[h.strip().lower().replace(" ", "_")] = v
            m = EMAIL_RE.search(rec.get("email") or "")
            if not m:
                if any((v or "").strip() for v in row.values() if isinstance(v, str)):
                    errors.append(f"Line {i}: no email address")
                continue
            rec["email"] = m.group(0).lower()
            rec["custom"] = custom
            if rec["email"] in seen:
                continue
            seen.add(rec["email"])
            out.append(_split_name(rec))
    else:
        for i, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            m = EMAIL_RE.search(line)
            if not m:
                errors.append(f"Line {i}: no email address - “{line.strip()[:60]}”")
                continue
            email = m.group(0).lower()
            rest = [t.strip().strip('"<>') for t in re.split(r"[,;\t|]", line.replace(m.group(0), "")) if t.strip().strip('"<>')]
            rec = {"email": email, "custom": {}}
            if rest:
                rec["full_name"] = rest[0]
            if len(rest) > 1:
                rec["company"] = rest[1]
            if email in seen:
                continue
            seen.add(email)
            out.append(_split_name(rec))
    for r in out:
        c = db.q1("SELECT status, last_contacted_at FROM contacts WHERE email=?", (r["email"],))
        r["known_status"] = c["status"] if c else ""
        r["last_contacted_at"] = c["last_contacted_at"] if c else None
        busy = db.q1("SELECT p.name FROM enrollments e JOIN contacts c ON c.id=e.contact_id JOIN pipelines p ON p.id=e.pipeline_id "
                     "WHERE c.email=? AND e.state IN ('active','paused') LIMIT 1", (r["email"],))
        r["busy_in"] = busy["name"] if busy else ""
    return out, errors


def render_preview(pid, recipient, account_id):
    p = db.q1("SELECT * FROM pipelines WHERE id=?", (pid,))
    steps = steps_of(json.loads(p["definition"]))
    acc = db.q1("SELECT * FROM accounts WHERE id=?", (account_id,)) if account_id else None
    contact = {"email": recipient.get("email", ""), "first_name": recipient.get("first_name", ""),
               "last_name": recipient.get("last_name", ""), "title": recipient.get("title", ""),
               "custom_json": json.dumps(recipient.get("custom") or {})}
    company = {"name": recipient.get("company", ""), "website": recipient.get("website", ""),
               "category": recipient.get("category", "")}
    out, first_subject = [], ""
    for i, s in enumerate(steps):
        vars_ = templating.contact_vars(contact, company, acc)
        if i == 0 or not s.get("same_thread", True):
            subject, miss_s = templating.render(s.get("subject") or "", vars_)
            first_subject = first_subject or subject
        else:
            subject, miss_s = ("Re: " + first_subject) if first_subject else "Re: …", []
        body, miss_b = templating.render(s.get("body") or "", vars_)
        if acc and (acc.get("signature") or "").strip():
            body = body.rstrip() + "\n\n" + acc["signature"].strip()
        footer = (db.get_setting("footer") or "").strip()  # same as engine.render_step: preview = what is sent
        if footer:
            body = body.rstrip() + "\n\n" + templating.render(footer, vars_)[0]
        out.append({"subject": subject, "body": body, "missing": sorted(set(miss_s + miss_b)),
                    "wait_days": s.get("wait_days")})
    return out


# ---------------------------------------------------------------- batch progress in words

def recipient_status(e, steps, now_iso):
    ids = [s["id"] for s in steps]
    idx = ids.index(e["current_node"]) if e["current_node"] in ids else 0
    label = "Email 1" if idx == 0 else f"Follow-up {idx}"
    sent = db.scalar("SELECT COUNT(*) FROM messages WHERE enrollment_id=? AND direction='out'", (e["id"],)) or 0
    last_sent = db.scalar("SELECT MAX(ts) FROM messages WHERE enrollment_id=? AND direction='out'", (e["id"],))
    reply = db.q1("SELECT id, category, summary, ts FROM messages WHERE contact_id=? AND direction='in' AND ts>=? "
                  "ORDER BY ts DESC LIMIT 1", (e["contact_id"], e["created_at"]))
    q = db.q1("SELECT id, status, scheduled_at, approval_reason FROM queue WHERE enrollment_id=? "
              "AND status IN ('pending','needs_approval','sending') ORDER BY scheduled_at LIMIT 1", (e["id"],))
    st = e["state"]
    out = {"sent": sent, "last_sent": last_sent, "next_at": None, "reply_id": reply["id"] if reply else None,
           "reply_category": reply["category"] if reply else "", "reply_summary": reply["summary"] if reply else "",
           "queue_id": q["id"] if q else None}
    if st == "replied" or (reply and st in ("stopped", "completed") and reply["category"] not in ("out_of_office", "auto_reply")):
        out.update(kind="good" if reply and reply["category"] in ("interested", "question", "referral") else "info",
                   text="Replied" + (f" · {reply['category'].replace('_', ' ')}" if reply and reply["category"] else ""))
    elif st == "bounced":
        out.update(kind="bad", text="Bounced - address doesn't exist")
    elif st == "unsubscribed":
        out.update(kind="bad", text="Asked to unsubscribe")
    elif st == "completed":
        out.update(kind="muted", text=f"No reply after {sent} email{'s' if sent != 1 else ''} - finished")
    elif st == "stopped":
        out.update(kind="muted", text="Stopped" + (f" - {e['stopped_reason']}" if e["stopped_reason"] else ""))
    elif st == "paused":
        out.update(kind="warn", text="Paused")
    elif q:
        if q["status"] == "needs_approval":
            out.update(kind="warn", text=f"{label} waiting for your OK", next_at=q["scheduled_at"])
        else:
            out.update(kind="info", text=f"{label} scheduled", next_at=q["scheduled_at"])
    elif e["phase"] == "waiting":
        more = idx + 1 < len(steps)
        out.update(kind="info", text=f"{label} sent · {'follow-up' if more else 'finishes'} if no reply",
                   next_at=e["next_action_at"])
    else:
        # the worker gives it a real send slot within a few seconds - don't show the batch start time as if it were one
        out.update(kind="info", text=f"{label}: getting queued…", next_at=None)
    out["step"] = idx + 1
    out["steps"] = len(steps)
    return out
