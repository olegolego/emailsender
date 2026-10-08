"""Pipeline engine: enrollments move through steps, emails go into the send queue, replies pick the branch."""
import json
import random
from datetime import timedelta, datetime, timezone

from . import db, schedule, templating, mailer, analyzer
from .notifier import notify

SUPPRESSED = ("bounced", "unsubscribed", "do_not_contact", "invalid")  # contacted_before is handled in already_reached()
FREEMAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "aol.com",
            "icloud.com", "me.com", "proton.me", "protonmail.com", "msn.com", "comcast.net", "gmx.com", "mail.com"}
SEND_TYPES = ("email", "ai_email")
# default reaction to a reply when a step has no branch for that category
DEFAULT_REACTION = {"out_of_office": "ooo", "auto_reply": "ignore", "bounce": "stop", "unsubscribe": "stop"}


# ---------------------------------------------------------------- pipeline helpers

def load_pipeline(pid):
    p = db.q1("SELECT * FROM pipelines WHERE id=?", (pid,))
    if not p:
        return None
    p["definition"] = json.loads(p["definition"])
    p["settings"] = {**schedule.DEFAULT_PIPELINE_SETTINGS, **json.loads(p["settings"] or "{}")}
    p["nodes"] = {n["id"]: n for n in p["definition"].get("nodes", [])}
    return p


def validate_definition(defn):
    errors = []
    nodes = defn.get("nodes") or []
    ids = [n.get("id") for n in nodes]
    if not nodes:
        errors.append("Add at least one step.")
    if len(set(ids)) != len(ids):
        errors.append("Two steps have the same id.")
    idset = set(ids)
    if nodes and defn.get("start") not in idset:
        errors.append("Choose a valid first step.")
    for n in nodes:
        label = n.get("name") or n.get("id")
        if n.get("type") in SEND_TYPES:
            if n.get("type") == "email" and not (n.get("body") or "").strip():
                errors.append(f"Step '{label}' has no message body.")
            if n.get("type") == "ai_email" and not (n.get("prompt") or "").strip():
                errors.append(f"Step '{label}' needs instructions for Claude.")
            if n.get("thread") != "reply" and not (n.get("subject") or "").strip():
                errors.append(f"Step '{label}' starts a new thread but has no subject.")
        targets = list((n.get("branches") or {}).values()) + [n.get("next"), n.get("yes"), n.get("no")]
        for t in targets:
            if t and t not in idset and t not in ("stop", "end", "ignore", "ooo"):
                errors.append(f"Step '{label}' points to a missing step '{t}'.")
    return errors


def pick_account_id(pipeline=None, explicit=None):
    if explicit:
        return int(explicit)
    if pipeline and pipeline.get("account_id"):
        return pipeline["account_id"]
    r = db.q1("SELECT id FROM accounts WHERE active=1 ORDER BY id LIMIT 1")
    return r["id"] if r else None


def _path_add(e, entry):
    path = json.loads(e.get("path") or "[]")
    path.append({"ts": db.now_iso(), **entry})
    return json.dumps(path[-100:])


# ---------------------------------------------------------------- enrollment

def enroll(pipeline_id, contact_ids, start_at=None, stagger_minutes=0, account_id=None, allow_reenroll=False,
           batch_id=None):
    p = load_pipeline(pipeline_id)
    if not p:
        raise ValueError("Pipeline not found")
    errs = validate_definition(p["definition"])
    if errs:
        raise ValueError(" ".join(errs))
    acc = pick_account_id(p, account_id)
    if not acc:
        raise ValueError("Add a mailbox in Settings first.")
    start = db.parse_iso(start_at) if start_at else db.utcnow()
    skip_days = int(p["settings"].get("skip_if_contacted_days") or 0)
    result = {"enrolled": 0, "skipped": []}
    i = 0
    for cid in contact_ids:
        c = db.q1("SELECT * FROM contacts WHERE id=?", (cid,))
        if not c:
            continue
        if c["status"] in SUPPRESSED:
            result["skipped"].append({"email": c["email"], "reason": c["status"]})
            continue
        reason = "" if allow_reenroll else already_reached(c)
        if reason:
            result["skipped"].append({"email": c["email"], "reason": reason})
            continue
        if db.scalar("SELECT COUNT(*) FROM enrollments WHERE contact_id=? AND state IN ('active','paused')", (cid,)):
            result["skipped"].append({"email": c["email"], "reason": "already in another running batch"})
            continue
        if skip_days and c.get("last_contacted_at") and \
                db.parse_iso(c["last_contacted_at"]) > db.utcnow() - timedelta(days=skip_days):
            result["skipped"].append({"email": c["email"], "reason": f"contacted in last {skip_days} days"})
            continue
        t = start + timedelta(minutes=stagger_minutes * i)
        i += 1
        eid = db.insert("enrollments", {
            "pipeline_id": pipeline_id, "contact_id": cid, "account_id": acc, "state": "active", "batch_id": batch_id,
            "current_node": p["definition"]["start"], "phase": "enter", "node_entered_at": db.iso(t),
            "next_action_at": db.iso(t), "created_at": db.now_iso(), "updated_at": db.now_iso(),
            "path": json.dumps([{"ts": db.now_iso(), "node": p["definition"]["start"], "why": "enrolled"}]),
        })
        db.log_event("enrolled", cid, eid, detail=p["name"])
        result["enrolled"] += 1
    return result


def already_reached(c):
    """One email address (and one company) gets one strategy, ever. Returns why to skip, or ''."""
    if c["status"] == "contacted_before":
        return "contacted before by the team (CRM)"
    prev = db.q1("SELECT p.name FROM enrollments e JOIN pipelines p ON p.id=e.pipeline_id WHERE e.contact_id=? "
                 "ORDER BY e.id LIMIT 1", (c["id"],))
    if prev:
        return f"already got the sequence “{prev['name']}”"
    if db.scalar("SELECT COUNT(*) FROM messages WHERE contact_id=? AND direction='out' AND is_test=0", (c["id"],)):
        return "already emailed"
    if c.get("company_id"):
        co = db.q1("SELECT plan, form_status FROM companies WHERE id=?", (c["company_id"],))
        if co and co["plan"] == "form" and co["form_status"] != "not_possible":
            return "has a wholesale form - fill the form instead"
        if co and co["plan"] == "contacted_before":
            return "company contacted before by the team (CRM)"
        other = db.q1("SELECT c2.email FROM enrollments e JOIN contacts c2 ON c2.id=e.contact_id "
                      "WHERE c2.company_id=? AND c2.id<>? LIMIT 1", (c["company_id"], c["id"]))
        if other:
            return f"company already contacted via {other['email']}"
    return ""


def process_due(limit=200):
    rows = db.q("SELECT e.* FROM enrollments e JOIN pipelines p ON p.id=e.pipeline_id "
                "WHERE e.state='active' AND p.status='active' AND e.next_action_at IS NOT NULL "
                "AND e.next_action_at<=? ORDER BY e.next_action_at LIMIT ?", (db.now_iso(), limit))
    for e in rows:
        try:
            advance(e["id"])
        except Exception as ex:  # keep the loop alive; surface the problem
            db.update("enrollments", e["id"], {"state": "paused", "stopped_reason": f"error: {ex}"[:500],
                                               "updated_at": db.now_iso()})
            notify("failure", "Pipeline step failed", f"Enrollment #{e['id']}: {ex}", f"#/pipelines/{e['pipeline_id']}", "error")


def advance(eid):
    for _ in range(30):
        e = db.q1("SELECT * FROM enrollments WHERE id=?", (eid,))
        if not e or e["state"] != "active":
            return
        p = load_pipeline(e["pipeline_id"])
        node = p["nodes"].get(e["current_node"])
        if node is None:
            return finish(e, "end", "step no longer exists")
        due = e["next_action_at"] and e["next_action_at"] <= db.now_iso()
        if not due:
            return
        t = node.get("type")
        if e["phase"] == "enter":
            if t in SEND_TYPES:
                queue_step(e, p, node)
                return
            if t == "wait":
                until = db.parse_iso(e["node_entered_at"]) + schedule.duration(node.get("delay"))
                db.update("enrollments", eid, {"phase": "waiting", "next_action_at": db.iso(until), "updated_at": db.now_iso()})
                continue
            if t == "condition":
                ok = evaluate_condition(e, node)
                goto(e, node.get("yes") if ok else node.get("no"), f"condition {'yes' if ok else 'no'}")
                continue
            if t == "notify":
                c = db.q1("SELECT * FROM contacts WHERE id=?", (e["contact_id"],))
                co = db.q1("SELECT * FROM companies WHERE id=?", (c["company_id"],)) if c and c["company_id"] else None
                vars_ = templating.contact_vars(c or {}, co)
                title, _ = templating.render(node.get("title") or "Pipeline reminder", vars_)
                body, _ = templating.render(node.get("message") or "", vars_)
                notify("approval", title, body or f"{c['email'] if c else ''} reached '{node.get('name')}'",
                       f"#/contacts/{e['contact_id']}", "info")
                goto(e, node.get("next"), "notified")
                continue
            if t == "end":
                return finish(e, "end", "reached end step", outcome=node.get("outcome") or "")
            return finish(e, "end", f"unknown step type {t}")
        if e["phase"] == "waiting":
            if t in SEND_TYPES:
                goto(e, (node.get("branches") or {}).get("no_reply"), "no reply")
            else:
                goto(e, node.get("next"), "wait finished")
            continue
        return  # phase 'queued' - the sender takes it from here


def goto(e, target, why):
    if target in (None, "", "end"):
        return finish(e, "end", why)
    if target == "stop":
        return finish(e, "stop", why)
    if target in ("ignore", "ooo"):
        return
    db.update("enrollments", e["id"], {
        "current_node": target, "phase": "enter", "node_entered_at": db.now_iso(), "next_action_at": db.now_iso(),
        "state": "active", "updated_at": db.now_iso(), "path": _path_add(e, {"node": target, "why": why}),
    })


def finish(e, how, why, outcome="", state=None):
    state = state or ("completed" if how == "end" else "stopped")
    db.update("enrollments", e["id"], {"state": state, "phase": "done", "next_action_at": None,
                                       "stopped_reason": why[:300], "outcome": outcome or e.get("outcome") or "",
                                       "updated_at": db.now_iso(),
                                       "path": _path_add(e, {"node": state, "why": why})})
    cancel_pending(e["id"], f"enrollment {state}")
    db.log_event(f"enrollment_{state}", e["contact_id"], e["id"], detail=why)


def cancel_pending(eid, reason):
    db.execute("UPDATE queue SET status='cancelled', error=? WHERE enrollment_id=? AND status IN ('pending','needs_approval')",
               (reason, eid))


OPS = {
    "equals": lambda a, b: a.strip().lower() == b.strip().lower(),
    "not_equals": lambda a, b: a.strip().lower() != b.strip().lower(),
    "contains": lambda a, b: b.strip().lower() in a.lower(),
    "not_empty": lambda a, b: bool(a.strip()),
    "empty": lambda a, b: not a.strip(),
    "has_tag": lambda a, b: False,
}


def evaluate_condition(e, node):
    c = db.q1("SELECT * FROM contacts WHERE id=?", (e["contact_id"],)) or {}
    co = db.q1("SELECT * FROM companies WHERE id=?", (c.get("company_id"),)) if c.get("company_id") else None
    field, op, value = node.get("field") or "", node.get("op") or "equals", node.get("value") or ""
    if op == "has_tag":
        tags = [t.strip().lower() for t in (c.get("tags") or "").split(",")]
        return value.strip().lower() in tags
    if field == "last_reply_category":
        v = db.scalar("SELECT category FROM messages WHERE contact_id=? AND direction='in' ORDER BY ts DESC LIMIT 1",
                      (e["contact_id"],)) or ""
    elif field == "replied":
        v = "yes" if db.scalar("SELECT COUNT(*) FROM messages WHERE enrollment_id=? AND direction='in'", (e["id"],)) else "no"
    else:
        v = str(templating.contact_vars(c, co).get(field, "") or c.get(field, "") or "")
    return OPS.get(op, OPS["equals"])(v, value)


# ---------------------------------------------------------------- queueing a step

def _context_for(e, p=None):
    c = db.q1("SELECT * FROM contacts WHERE id=?", (e["contact_id"],))
    co = db.q1("SELECT * FROM companies WHERE id=?", (c["company_id"],)) if c and c["company_id"] else None
    acc = db.q1("SELECT * FROM accounts WHERE id=?", (e["account_id"],)) if e.get("account_id") else None
    return c, co, acc


def render_step(e, p, node, rng=None):
    """Returns dict(subject, body, in_reply_to, references, missing, error)."""
    c, co, acc = _context_for(e, p)
    vars_ = templating.contact_vars(c, co, acc, {"original_subject": e.get("last_subject") or ""})
    rng = rng or random.Random()
    reply = node.get("thread") == "reply" and e.get("last_message_id")
    subject_tpl = node.get("subject") or ""
    if reply and not subject_tpl.strip():
        base = e.get("last_subject") or ""
        subject = base if base.lower().startswith("re:") else f"Re: {base}"
        missing_s = []
    else:
        subject, missing_s = templating.render(subject_tpl, vars_, rng)
    error = ""
    if node.get("type") == "ai_email":
        thread = db.q("SELECT direction, subject, body, reply_text, ts FROM messages WHERE contact_id=? ORDER BY ts DESC LIMIT 4",
                      (e["contact_id"],))
        vars_["_thread"] = "\n\n".join(f"[{'ME' if r['direction']=='out' else 'THEM'} {r['ts']}] {r['subject']}\n"
                                       f"{(r['reply_text'] or r['body'] or '')[:2000]}" for r in reversed(thread)) or "(none)"
        try:
            body = analyzer.write_email(templating.render(node.get("prompt") or "", vars_, rng)[0], vars_)
        except Exception as ex:
            body, error = "", f"Claude could not write this email: {ex}"
        missing_b = []
    else:
        body, missing_b = templating.render(node.get("body") or "", vars_, rng)
    if node.get("signature", True) and acc and (acc.get("signature") or "").strip():
        body = body.rstrip() + "\n\n" + acc["signature"].strip()
    footer = (db.get_setting("footer") or "").strip()
    if footer:
        body = body.rstrip() + "\n\n" + templating.render(footer, vars_, rng)[0]
    in_reply_to = e.get("last_message_id") if reply else ""
    refs = (e.get("references_hdr") or "").strip() if reply else ""
    return {"subject": subject.strip(), "body": body, "in_reply_to": in_reply_to or "", "references": refs,
            "missing": sorted(set(missing_s + missing_b)), "error": error, "to": c["email"] if c else ""}


def batch_settings(enrollment_id):
    raw = db.scalar("SELECT b.settings FROM enrollments e JOIN batches b ON b.id=e.batch_id WHERE e.id=?",
                    (enrollment_id,)) if enrollment_id else None
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def window_settings(p, enrollment_id):
    """The style's send window, overridden by the batch's own days/hours when set."""
    s = dict(p["settings"])
    b = batch_settings(enrollment_id)
    s.update({k: b[k] for k in ("days", "window_start", "window_end") if b.get(k) not in (None, "", [])})
    return s


def batch_sent_today(batch_id):
    start, end = schedule.local_day_bounds()
    return db.scalar("SELECT COUNT(*) FROM messages m JOIN enrollments e ON e.id=m.enrollment_id "
                     "WHERE e.batch_id=? AND m.direction='out' AND m.ts>=? AND m.ts<?",
                     (batch_id, db.iso(start), db.iso(end))) or 0


def space_out(account_id):
    """Gives every pending email of a mailbox a realistic slot: 'gap ± 30 s' after the previous one, inside its batch's
    send window, and no more per day than the mailbox (and batch) daily limits. The Outbox then shows real times."""
    acc = db.q1("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not acc:
        return
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(db.get_setting("timezone") or "America/Detroit")
    day_of = lambda t: t.astimezone(tz).date()
    base_gap = float(acc["min_gap_minutes"] or 0) * 60
    limit = int(acc["daily_limit"] or 0) or 10 ** 6
    now = db.utcnow()
    sent_per_day, batch_sent_per_day = {}, {}
    for r in db.q("SELECT m.ts, e.batch_id FROM messages m LEFT JOIN enrollments e ON e.id=m.enrollment_id "
                  "WHERE m.direction='out' AND m.account_id=? AND m.ts>=?", (account_id, db.days_ago_iso(2))):
        d = day_of(db.parse_iso(r["ts"]))
        sent_per_day[d] = sent_per_day.get(d, 0) + 1
        if r["batch_id"]:
            batch_sent_per_day[(r["batch_id"], d)] = batch_sent_per_day.get((r["batch_id"], d), 0) + 1
    last = db.scalar("SELECT MAX(ts) FROM messages WHERE direction='out' AND account_id=?", (account_id,))
    prev = db.parse_iso(last) if last else None
    items = db.q("SELECT q.*, e.batch_id FROM queue q LEFT JOIN enrollments e ON e.id=q.enrollment_id "
                 "WHERE q.account_id=? AND q.status='pending' ORDER BY q.scheduled_at, q.id", (account_id,))
    pipelines, updates = {}, []
    for it in items:
        t = max(db.parse_iso(it["scheduled_at"]), now)
        if prev is not None:
            t = max(t, prev + timedelta(seconds=max(0.0, base_gap + random.uniform(-30, 30)) if base_gap else 0))
        ws, c, cap = None, None, 0
        if it["respect_window"] and it["pipeline_id"]:
            p = pipelines.get(it["pipeline_id"]) or pipelines.setdefault(it["pipeline_id"], load_pipeline(it["pipeline_id"]))
            ws = window_settings(p, it["enrollment_id"])
            c = db.q1("SELECT timezone FROM contacts WHERE id=?", (it["contact_id"],))
            cap = int(batch_settings(it["enrollment_id"]).get("daily_limit") or 0)
        for _ in range(60):
            if ws:
                t = schedule.next_window_time(t, ws, c)
            d = day_of(t)
            full = sent_per_day.get(d, 0) >= limit or (cap and it["batch_id"] and
                                                         batch_sent_per_day.get((it["batch_id"], d), 0) >= cap)
            if not full:
                break
            nxt = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
            t = nxt.astimezone(timezone.utc)
        d = day_of(t)
        sent_per_day[d] = sent_per_day.get(d, 0) + 1
        if it["batch_id"]:
            batch_sent_per_day[(it["batch_id"], d)] = batch_sent_per_day.get((it["batch_id"], d), 0) + 1
        prev = t.replace(microsecond=0)
        updates.append((db.iso(prev), it["id"]))
    with db.transaction() as cx:
        cx.executemany("UPDATE queue SET scheduled_at=? WHERE id=?", updates)
    return len(updates)


def queue_step(e, p, node):
    c = db.q1("SELECT * FROM contacts WHERE id=?", (e["contact_id"],))
    if not c or c["status"] in SUPPRESSED:
        return finish(e, "stop", f"contact is {c['status'] if c else 'deleted'}")
    r = render_step(e, p, node)
    send_at = schedule.plan_send_time(db.parse_iso(e["node_entered_at"]), node.get("delay"), window_settings(p, e["id"]), c)
    reasons = []
    if node.get("approval") or (node.get("type") == "ai_email" and node.get("approval", True)):
        reasons.append("approval required by this step")
    if r["missing"]:
        reasons.append("missing fields: " + ", ".join(r["missing"]))
    if r["error"]:
        reasons.append(r["error"])
    if not r["subject"]:
        reasons.append("empty subject")
    with db.transaction() as cx:
        cx.execute("INSERT INTO queue (kind, enrollment_id, pipeline_id, node_id, contact_id, account_id, to_addr, subject,"
                   " body, in_reply_to, references_hdr, scheduled_at, respect_window, status, approval_reason, created_at)"
                   " VALUES ('pipeline',?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                   (e["id"], p["id"], node["id"], c["id"], e["account_id"], r["to"], r["subject"], r["body"],
                    r["in_reply_to"], r["references"], db.iso(send_at),
                    "needs_approval" if reasons else "pending", "; ".join(reasons), db.now_iso()))
        cx.execute("UPDATE enrollments SET phase='queued', next_action_at=NULL, updated_at=? WHERE id=?",
                   (db.now_iso(), e["id"]))
    space_out(e["account_id"])
    if reasons:
        notify("approval", "Email waiting for your approval",
               f"{c['email']} · {node.get('name') or node['id']} · {reasons[0]}", "#/queue/needs_approval", "warning")


# ---------------------------------------------------------------- sending

def sent_today(account_id):
    start, end = schedule.local_day_bounds()
    return db.scalar("SELECT COUNT(*) FROM messages WHERE direction='out' AND account_id=? AND ts>=? AND ts<?",
                     (account_id, db.iso(start), db.iso(end))) or 0


_GAPS = {}  # (account id, last send ts) -> randomised gap in seconds


def _gap_seconds(acc, last_ts):
    """Minimum gap between two emails from a mailbox, randomised by ±30 s so sends don't look robotic."""
    key = (acc["id"], last_ts)
    if key not in _GAPS:
        base = float(acc["min_gap_minutes"] or 0) * 60
        _GAPS.clear() if len(_GAPS) > 200 else None
        _GAPS[key] = max(0.0, base + random.uniform(-30, 30)) if base else 0.0
    return _GAPS[key]


def send_due():
    if db.get_setting("sending_paused"):
        return 0
    sent = 0
    for acc in db.q("SELECT * FROM accounts WHERE active=1"):
        if sent_today(acc["id"]) >= int(acc["daily_limit"] or 0):
            continue
        last = db.scalar("SELECT MAX(ts) FROM messages WHERE direction='out' AND account_id=?", (acc["id"],))
        if last and db.parse_iso(last) + timedelta(seconds=_gap_seconds(acc, last)) > db.utcnow():
            continue
        items = db.q("SELECT q.* FROM queue q LEFT JOIN pipelines p ON p.id=q.pipeline_id "
                     "WHERE q.status='pending' AND q.account_id=? AND q.scheduled_at<=? "
                     "AND (q.pipeline_id IS NULL OR p.status='active') ORDER BY q.scheduled_at LIMIT 50",
                     (acc["id"], db.now_iso()))
        full_batches = set()
        for item in items:
            if item["enrollment_id"]:
                bid = db.scalar("SELECT batch_id FROM enrollments WHERE id=?", (item["enrollment_id"],))
                cap = int(batch_settings(item["enrollment_id"]).get("daily_limit") or 0)
                if bid and cap and (bid in full_batches or batch_sent_today(bid) >= cap):
                    full_batches.add(bid)
                    continue  # this batch reached its own daily cap; others can still send
            if item["respect_window"] and item["pipeline_id"]:
                p = load_pipeline(item["pipeline_id"])
                c = db.q1("SELECT * FROM contacts WHERE id=?", (item["contact_id"],))
                now = db.utcnow()
                ws = window_settings(p, item["enrollment_id"])
                if not schedule.in_window(now, ws, c):
                    nxt = schedule.next_window_time(now, ws, c)
                    db.update("queue", item["id"], {"scheduled_at": db.iso(nxt)})
                    continue
            if send_item(item["id"]):
                sent += 1
            break
    return sent


def send_item(qid, force=False):
    """Sends one queue item for real. Returns True on success."""
    with db.transaction() as cx:
        item = cx.execute("SELECT * FROM queue WHERE id=?", (qid,)).fetchone()
        if not item:
            return False
        item = dict(item)
        allowed = ("pending", "needs_approval", "failed") if force else ("pending",)
        if item["status"] not in allowed:
            return False
        cx.execute("UPDATE queue SET status='sending', attempts=attempts+1 WHERE id=?", (qid,))
    acc = db.q1("SELECT * FROM accounts WHERE id=?", (item["account_id"],))
    c = db.q1("SELECT * FROM contacts WHERE id=?", (item["contact_id"],)) if item["contact_id"] else None
    try:
        if not acc:
            raise mailer.MailError("Mailbox not found")
        if item["kind"] == "pipeline" and c and c["status"] in SUPPRESSED:
            db.update("queue", qid, {"status": "cancelled", "error": f"contact is {c['status']}"})
            return False
        if item["kind"] == "pipeline" and not db.get_setting("test_mode"):
            from . import verify
            ok, why = verify.check_domain(item["to_addr"].split("@")[-1])
            if ok is False:  # guaranteed bounce - don't send, protect eigenreach.com's reputation
                db.update("queue", qid, {"status": "cancelled", "error": f"not sent: {why}"})
                if c:
                    verify.mark_invalid(c["id"], why)
                return False
        to_addr = item["to_addr"]
        subject = item["subject"]
        test = bool(db.get_setting("test_mode"))
        if test:
            test_to = (db.get_setting("test_address") or "").strip()
            if not test_to:
                raise mailer.MailError("Test mode is on but no test address is set (Settings → Sending).")
            subject = f"[TEST → {to_addr}] {subject}"
            to_addr = test_to
        msg = mailer.build_message(acc, to_addr, subject, item["body"], item["in_reply_to"], item["references_hdr"],
                                   unsubscribe=bool(db.get_setting("list_unsubscribe", True)))
        mailer.smtp_send(acc, msg)
    except Exception as ex:
        attempts = item["attempts"] + 1
        if attempts < 3 and not force:
            db.update("queue", qid, {"status": "pending", "error": str(ex)[:500],
                                     "scheduled_at": db.iso(db.utcnow() + timedelta(minutes=10 * attempts))})
        else:
            db.update("queue", qid, {"status": "failed", "error": str(ex)[:500]})
            notify("failure", "Email failed to send", f"{item['to_addr']}: {ex}", "#/queue/failed", "error")
        db.log_event("send_failed", item["contact_id"], item["enrollment_id"], detail=str(ex)[:500])
        return False

    now = db.now_iso()
    mid = msg["Message-ID"]
    e = db.q1("SELECT * FROM enrollments WHERE id=?", (item["enrollment_id"],)) if item["enrollment_id"] else None
    company_id = c["company_id"] if c else None
    row = db.insert("messages", {
        "direction": "out", "account_id": acc["id"], "contact_id": item["contact_id"], "company_id": company_id,
        "enrollment_id": item["enrollment_id"], "pipeline_id": item["pipeline_id"], "node_id": item["node_id"],
        "queue_id": qid, "message_id": mid, "in_reply_to": item["in_reply_to"], "references_hdr": item["references_hdr"],
        "from_addr": acc["email"], "to_addr": to_addr, "subject": item["subject"], "body": item["body"], "ts": now,
        "is_test": 1 if test else 0, "handled": 1,
    })
    db.update("queue", qid, {"status": "sent", "sent_at": now, "message_row_id": row, "error": ""})
    if c:
        db.update("contacts", c["id"], {"last_contacted_at": now,
                                        "status": "active" if c["status"] == "new" else c["status"]})
    db.log_event("sent", item["contact_id"], item["enrollment_id"], row, item["subject"])
    if e:
        refs = ((e.get("references_hdr") or "") + " " + mid).strip()
        upd = {"last_message_id": mid, "last_subject": item["subject"], "references_hdr": refs[-4000:],
               "updated_at": now}
        if not e.get("thread_root_id"):
            upd["thread_root_id"] = mid
        if e["state"] == "active" and e["phase"] == "queued" and e["current_node"] == item["node_id"]:
            p = load_pipeline(e["pipeline_id"])
            node = p["nodes"].get(item["node_id"]) or {}
            wait = schedule.duration(node.get("wait") or {"value": 3, "unit": "days"})
            upd.update({"phase": "waiting", "next_action_at": db.iso(db.utcnow() + wait)})
        db.update("enrollments", e["id"], upd)
    return True


def compose(account_id, to_addr, subject, body, reply_to_message_row=None, send_at=None, contact_id=None):
    """A one-off email (new or a reply) that goes through the same queue and is recorded the same way."""
    to_addr = to_addr.strip().lower()
    acc = db.q1("SELECT * FROM accounts WHERE id=?", (account_id,)) if account_id else None
    acc = acc or db.q1("SELECT * FROM accounts WHERE active=1 ORDER BY id LIMIT 1")
    if not acc:
        raise ValueError("Add a mailbox in Settings first.")
    c = db.q1("SELECT * FROM contacts WHERE id=?", (contact_id,)) if contact_id else None
    c = c or db.q1("SELECT * FROM contacts WHERE email=?", (to_addr,))
    if not c:
        cid = upsert_contact({"email": to_addr})
        c = db.q1("SELECT * FROM contacts WHERE id=?", (cid,))
    in_reply_to, refs, enrollment_id = "", "", None
    if reply_to_message_row:
        m = db.q1("SELECT * FROM messages WHERE id=?", (reply_to_message_row,))
        if m:
            in_reply_to = m["message_id"] or ""
            refs = ((m["references_hdr"] or "") + " " + (m["message_id"] or "")).strip()
            enrollment_id = m["enrollment_id"]
            db.update("messages", m["id"], {"handled": 1})
    qid = db.insert("queue", {
        "kind": "manual", "enrollment_id": None, "contact_id": c["id"], "account_id": acc["id"], "to_addr": to_addr,
        "subject": subject, "body": body, "in_reply_to": in_reply_to, "references_hdr": refs,
        "scheduled_at": send_at or db.now_iso(), "respect_window": 0, "status": "pending", "created_at": db.now_iso(),
    })
    if enrollment_id:
        db.update("queue", qid, {"enrollment_id": enrollment_id})
    if not send_at:
        ok = send_item(qid, force=True)
        if not ok:
            err = db.scalar("SELECT error FROM queue WHERE id=?", (qid,))
            raise RuntimeError(err or "Sending failed")
    return qid


# ---------------------------------------------------------------- contacts

def upsert_company(name="", domain="", website="", category=""):
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain and website:
        domain = website.lower().split("//")[-1].split("/")[0].removeprefix("www.")
    row = None
    if domain:
        row = db.q1("SELECT * FROM companies WHERE domain=?", (domain,))
    if not row and name:
        row = db.q1("SELECT * FROM companies WHERE lower(name)=lower(?)", (name.strip(),))
    if row:
        upd = {}
        if name and not row["name"]:
            upd["name"] = name.strip()
        if domain and not row["domain"]:
            upd["domain"] = domain
        if website and not row["website"]:
            upd["website"] = website
        if category and not row["category"]:
            upd["category"] = category
        db.update("companies", row["id"], upd)
        return row["id"]
    if not (name or domain):
        return None
    return db.insert("companies", {"name": (name or "").strip(), "domain": domain, "website": website or "",
                                   "category": category or "", "created_at": db.now_iso()})


def upsert_contact(d, update_existing=True):
    email = (d.get("email") or "").strip().lower()
    if "@" not in email:
        raise ValueError(f"Invalid email: {email!r}")
    domain = email.split("@")[1]
    company_id = d.get("company_id")
    if not company_id and (d.get("company") or d.get("company_domain") or d.get("website")):
        company_id = upsert_company(d.get("company") or "", d.get("company_domain") or
                                    ("" if domain in FREEMAIL else domain), d.get("website") or "", d.get("category") or "")
    elif not company_id and domain not in FREEMAIL:
        company_id = upsert_company("", domain)
    base = ("first_name", "last_name", "title", "phone", "timezone", "tags", "notes")
    existing = db.q1("SELECT * FROM contacts WHERE email=?", (email,))
    custom = d.get("custom") or {}
    if existing:
        if not update_existing:
            return existing["id"]
        upd = {k: d[k] for k in base if d.get(k) not in (None, "")}
        if company_id and not existing["company_id"]:
            upd["company_id"] = company_id
        if custom:
            cur = json.loads(existing["custom_json"] or "{}")
            cur.update({k: v for k, v in custom.items() if v not in (None, "")})
            upd["custom_json"] = json.dumps(cur)
        if d.get("tags") and existing["tags"]:
            tags = {t.strip() for t in (existing["tags"] + "," + d["tags"]).split(",") if t.strip()}
            upd["tags"] = ",".join(sorted(tags))
        db.update("contacts", existing["id"], upd)
        return existing["id"]
    row = {k: (d.get(k) or "") for k in base}
    row.update({"email": email, "company_id": company_id, "custom_json": json.dumps(custom),
                "status": d.get("status") or "new", "created_at": db.now_iso()})
    return db.insert("contacts", row)


# ---------------------------------------------------------------- inbound

def _our_addresses():
    return {a["email"].lower() for a in db.q("SELECT email FROM accounts")} | \
           {(a["username"] or "").lower() for a in db.q("SELECT username FROM accounts")}


def _ignored(addr):
    addr = (addr or "").lower()
    lst = [x.lower() for x in (db.get_setting("ignored_senders") or [])]
    return addr in lst or addr.split("@")[-1] in lst


def purge_filtered_inbound():
    """Removes conversations that today's rules would not create (automated or Russian senders we never emailed)."""
    removed = 0
    for c in db.q("SELECT * FROM contacts WHERE tags LIKE '%inbound%'"):
        msgs = db.q("SELECT * FROM messages WHERE contact_id=? AND direction='in'", (c["id"],))
        if db.scalar("SELECT COUNT(*) FROM messages WHERE contact_id=? AND direction='out'", (c["id"],)):
            continue
        if msgs and all(mailer.is_automated_sender(m["from_addr"]) or m["is_auto"] and not m["replied_to_id"]
                        or mailer.is_russian(m["subject"], m["reply_text"] or m["body"]) for m in msgs):
            with db.transaction() as cx:
                for m in msgs:
                    cx.execute("UPDATE events SET message_row_id=NULL WHERE message_row_id=?", (m["id"],))
                    cx.execute("DELETE FROM messages WHERE id=?", (m["id"],))
                cx.execute("UPDATE events SET contact_id=NULL WHERE contact_id=?", (c["id"],))
                cx.execute("DELETE FROM contacts WHERE id=?", (c["id"],))
                if c["company_id"]:
                    cx.execute("DELETE FROM companies WHERE id=? AND plan='inbound' AND id NOT IN "
                               "(SELECT company_id FROM contacts WHERE company_id IS NOT NULL)", (c["company_id"],))
            removed += len(msgs)
    return removed


def company_for_domain(dom):
    """A company in our lists that this email domain belongs to: its website, a contact's email, or its form's website."""
    if not dom or dom in FREEMAIL:
        return None
    return (db.q1("SELECT * FROM companies WHERE domain=?", (dom,))
            or db.q1("SELECT co.* FROM companies co JOIN contacts c ON c.company_id=co.id WHERE c.email LIKE ? LIMIT 1",
                     ("%@" + dom,))
            or db.q1("SELECT * FROM companies WHERE form_url LIKE ? OR form_url LIKE ? OR form_url LIKE ? "
                     "OR form_url LIKE ? LIMIT 1",
                     (f"%//{dom}/%", f"%//www.{dom}/%", f"%//{dom}", f"%//www.{dom}")))


def wants_header(raw_hdr):
    """Cheap decision from headers only: is this email worth downloading? Mirrors process_inbound's rules."""
    import email as _email
    import email.policy as _policy
    from email.utils import parseaddr
    h = _email.message_from_bytes(raw_hdr, policy=_policy.default)
    mid = str(h.get("Message-ID", "")).strip()
    if mid and db.scalar("SELECT COUNT(*) FROM messages WHERE message_id=? AND direction='in'", (mid,)):
        return False
    frm = parseaddr(str(h.get("From", "")))[1].lower()
    rt = parseaddr(str(h.get("Reply-To", "")))[1].lower()
    if rt and rt != frm and mailer.is_automated_sender(frm) and not mailer.is_automated_sender(rt):
        frm = rt
    if frm in _our_addresses():
        return False
    if "mailer-daemon" in frm or frm.startswith("postmaster@") or h.get_content_type() == "multipart/report" \
            or h.get("X-Failed-Recipients"):
        return True  # possible bounce of one of our emails
    if _ignored(frm):
        return False
    ids = set(mailer.MSGID_RE.findall(f"{h.get('In-Reply-To', '')} {h.get('References', '')}"))
    if ids:
        ph = ",".join("?" for _ in ids)
        if db.scalar(f"SELECT COUNT(*) FROM messages WHERE direction='out' AND message_id IN ({ph})", tuple(ids)):
            return True
    if db.scalar("SELECT COUNT(*) FROM contacts WHERE email=?", (frm,)):
        return True
    if company_for_domain(frm.split("@")[-1]):
        return True
    if mailer.is_russian(str(h.get("Subject", "")), ""):
        return False
    auto = str(h.get("Auto-Submitted", "")).lower()
    if h.get("List-Unsubscribe") or h.get("List-Id") or (auto and auto != "no") \
            or str(h.get("Precedence", "")).lower() in ("bulk", "list", "junk") or mailer.is_automated_sender(frm):
        return False
    return True


def process_inbound(account, raw, folder):
    p = mailer.parse(raw)
    if p["message_id"] and db.scalar("SELECT COUNT(*) FROM messages WHERE message_id=? AND direction='in'", (p["message_id"],)):
        return None
    # Website forms (FormSubmit) and similar relays send from a robot address with the real person in Reply-To.
    if p["reply_to"] and p["reply_to"] != p["from_addr"] and mailer.is_automated_sender(p["from_addr"]) \
            and not mailer.is_automated_sender(p["reply_to"]):
        p["from_addr"], p["from_name"] = p["reply_to"], p["reply_to_name"] or p["from_name"]
    if p["from_addr"] in _our_addresses():
        return None
    if not p["is_bounce"] and _ignored(p["from_addr"]):
        return None

    ids = set(mailer.MSGID_RE.findall(p["in_reply_to"] + " " + p["references"]))
    if p["is_bounce"]:
        ids |= set(mailer.MSGID_RE.findall(p["raw_text"]))
    out = None
    if ids:
        ph = ",".join("?" for _ in ids)
        out = db.q1(f"SELECT * FROM messages WHERE direction='out' AND message_id IN ({ph}) ORDER BY ts DESC LIMIT 1",
                    tuple(ids))

    contact = None
    if out and out["contact_id"]:
        contact = db.q1("SELECT * FROM contacts WHERE id=?", (out["contact_id"],))
    if not contact and p["is_bounce"]:
        cands = p["failed_recipients"] or [x.lower() for x in mailer.EMAIL_RE.findall(p["raw_text"])]
        for addr in cands:
            contact = db.q1("SELECT * FROM contacts WHERE email=?", (addr,))
            if contact:
                break
    if not contact and not p["is_bounce"]:
        contact = db.q1("SELECT * FROM contacts WHERE email=?", (p["from_addr"],))
        if not contact:
            # Any company already in our lists (emailed, form filled, or just imported): always keep its mail -
            # welcome emails, application confirmations and approvals often look automated.
            co = company_for_domain(p["from_addr"].split("@")[-1])
            if co:
                first, _, last = (p["from_name"] or "").partition(" ")
                cid = upsert_contact({"email": p["from_addr"], "first_name": first, "last_name": last,
                                      "company_id": co["id"], "notes": "Wrote from an address at this company."})
                contact = db.q1("SELECT * FROM contacts WHERE id=?", (cid,))
    if p["is_bounce"] and not contact:
        return None
    if not contact:
        # A new person writing to us: start a conversation, unless it's Russian (team / personal mail)
        # or automated mail (newsletters, notifications, no-reply senders).
        if mailer.is_russian(p["subject"], p["reply_text"] or p["body"]):
            return None
        if p["is_bulk"] or p["is_auto"] or mailer.is_automated_sender(p["from_addr"]):
            return None
        first, _, last = (p["from_name"] or "").strip().partition(" ")
        cid = upsert_contact({"email": p["from_addr"], "first_name": first, "last_name": last, "tags": "inbound",
                              "notes": "Wrote to us first."})
        contact = db.q1("SELECT * FROM contacts WHERE id=?", (cid,))
        if contact["company_id"]:
            db.execute("UPDATE companies SET plan='inbound', source='Wrote to us' WHERE id=? AND COALESCE(plan,'')=''",
                       (contact["company_id"],))

    enrollment = None
    if out and out["enrollment_id"]:
        enrollment = db.q1("SELECT * FROM enrollments WHERE id=?", (out["enrollment_id"],))
    if not enrollment and contact:
        enrollment = db.q1("SELECT * FROM enrollments WHERE contact_id=? AND created_at<=? ORDER BY "
                           "CASE WHEN state IN ('active','paused') THEN 0 ELSE 1 END, id DESC LIMIT 1",
                           (contact["id"], db.iso(p["ts"])))
    if not out and contact:
        out = db.q1("SELECT * FROM messages WHERE direction='out' AND contact_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                    (contact["id"], db.iso(p["ts"])))

    row_id = db.insert("messages", {
        "direction": "in", "account_id": account["id"], "contact_id": contact["id"] if contact else None,
        "company_id": contact["company_id"] if contact else None,
        "enrollment_id": enrollment["id"] if enrollment else None,
        "pipeline_id": enrollment["pipeline_id"] if enrollment else None,
        "node_id": out["node_id"] if out else None,
        "message_id": p["message_id"], "in_reply_to": p["in_reply_to"], "references_hdr": p["references"],
        "from_addr": p["from_addr"], "from_name": p["from_name"], "to_addr": ",".join(p["to"]),
        "subject": p["subject"], "body": p["body"][:200000], "reply_text": p["reply_text"][:50000], "ts": db.iso(p["ts"]),
        "replied_to_id": out["id"] if out else None, "is_bounce": 1 if p["is_bounce"] else 0,
        "is_auto": 1 if p["is_auto"] else 0, "folder": folder,
    })
    db.log_event("received", contact["id"] if contact else None, enrollment["id"] if enrollment else None, row_id,
                 p["subject"])
    save_price_lists(row_id, p, contact)
    analyze_and_route(row_id)
    return row_id


def save_price_lists(row_id, p, contact):
    """Files and price-list links from an incoming email go to the Price lists section."""
    import re as _re
    if not (p.get("attachments") or p.get("links")):
        return 0
    folder = db.DATA_DIR / "attachments" / str(row_id)
    n = 0
    for a in p.get("attachments") or []:
        folder.mkdir(parents=True, exist_ok=True)
        safe = _re.sub(r"[^\w.\- ()]+", "_", a["filename"])[:120] or "file"
        path = folder / safe
        k = 1
        while path.exists():
            path = folder / f"{k}-{safe}"
            k += 1
        path.write_bytes(a["data"])
        db.insert("price_lists", {"message_row_id": row_id, "contact_id": contact["id"] if contact else None,
                                  "company_id": contact["company_id"] if contact else None, "kind": "file",
                                  "filename": a["filename"], "path": str(path), "size": len(a["data"]),
                                  "content_type": a["content_type"], "received_at": db.iso(p["ts"])})
        n += 1
    for u in p.get("links") or []:
        db.insert("price_lists", {"message_row_id": row_id, "contact_id": contact["id"] if contact else None,
                                  "company_id": contact["company_id"] if contact else None, "kind": "link",
                                  "url": u, "filename": u.split("/")[2] if "//" in u else u,
                                  "received_at": db.iso(p["ts"])})
        n += 1
    if n:
        who = (contact["email"] if contact else p["from_addr"])
        notify("interested", f"📎 Price list from {who}", f"{n} file(s)/link(s) - see Price lists", "#/prices", "success")
    return n


def analyze_and_route(row_id, route=True, use_ai=None):
    m = db.q1("SELECT * FROM messages WHERE id=?", (row_id,))
    try:
        a, source = analyzer.analyze(m, use_ai)
    except Exception as ex:
        a, source = analyzer.rules(m), "rules"
        a["next_action"] = (a.get("next_action") or "") + f" (Claude unavailable: {str(ex)[:160]})"
    db.update("messages", row_id, {"category": a["category"], "sentiment": a["sentiment"],
                                   "interest_score": a["interest_score"], "summary": a["summary"],
                                   "analysis_json": json.dumps(a), "analyzed_at": db.now_iso(),
                                   "analysis_source": source,
                                   "handled": 1 if a["category"] in ("auto_reply", "bounce", "out_of_office") else 0})
    m = db.q1("SELECT * FROM messages WHERE id=?", (row_id,))
    contact = db.q1("SELECT * FROM contacts WHERE id=?", (m["contact_id"],)) if m["contact_id"] else None
    cat = a["category"]
    if contact:
        status = {"interested": "interested", "question": "replied", "referral": "replied", "other": "replied",
                  "not_interested": "not_interested", "unsubscribe": "unsubscribed", "bounce": "bounced"}.get(cat)
        upd = {}
        if cat not in ("bounce", "auto_reply", "out_of_office"):
            upd["last_reply_at"] = m["ts"]
        if status and contact["status"] not in ("do_not_contact",):
            upd["status"] = status
        db.update("contacts", contact["id"], upd)
        if cat == "referral" and a.get("referral_email") and "@" in a["referral_email"]:
            ref = a["referral_email"].strip().lower()
            if not db.q1("SELECT id FROM contacts WHERE email=?", (ref,)):
                first, _, last = (a.get("referral_name") or "").partition(" ")
                upsert_contact({"email": ref, "first_name": first, "last_name": last,
                                "company_id": contact["company_id"], "tags": "referral",
                                "notes": f"Referred by {contact['email']}"})
    if route and contact:
        _route_reply(m, contact, a)
    _notify_reply(m, contact, a)


def _route_reply(m, contact, a):
    cat = a["category"]
    enrollments = db.q("SELECT * FROM enrollments WHERE contact_id=? AND state IN ('active','paused') AND created_at<=?",
                       (contact["id"], m["ts"]))
    for e in enrollments:
        on_reply(e, m, a)
    if cat in ("bounce", "auto_reply", "out_of_office") or not contact["company_id"]:
        return
    others = db.q("SELECT e.* FROM enrollments e JOIN contacts c ON c.id=e.contact_id "
                  "WHERE c.company_id=? AND e.contact_id<>? AND e.state IN ('active','paused')",
                  (contact["company_id"], contact["id"]))
    for e in others:
        p = load_pipeline(e["pipeline_id"])
        if p and p["settings"].get("stop_company_on_reply", True):
            finish(e, "stop", f"colleague {contact['email']} replied ({cat})", state="stopped")


def on_reply(e, m, a):
    cat = a["category"]
    p = load_pipeline(e["pipeline_id"])
    node = p["nodes"].get(e["current_node"]) or {}
    branches = node.get("branches") or {}
    target = branches.get(cat)
    if not target and cat not in ("out_of_office", "auto_reply", "bounce", "unsubscribe"):
        target = branches.get("any_reply")
    if not target:
        target = DEFAULT_REACTION.get(cat) or ("stop" if p["settings"].get("stop_on_reply", True) else "ignore")
    if cat in ("bounce", "unsubscribe"):
        target = "stop"  # never keep emailing a dead or opted-out address
    db.update("enrollments", e["id"], {"path": _path_add(e, {"node": e["current_node"], "why": f"reply: {cat} → {target}"})})
    e = db.q1("SELECT * FROM enrollments WHERE id=?", (e["id"],))
    if target == "ignore":
        return
    if target == "ooo":
        resume = db.utcnow() + timedelta(days=5)
        try:
            if a.get("ooo_return_date"):
                d = datetime.fromisoformat(a["ooo_return_date"]).replace(tzinfo=timezone.utc)
                resume = max(db.utcnow() + timedelta(hours=12), d + timedelta(days=1, hours=14))
        except Exception:
            pass
        if e["phase"] == "waiting" and e["next_action_at"] and db.parse_iso(e["next_action_at"]) < resume:
            db.update("enrollments", e["id"], {"next_action_at": db.iso(resume)})
        db.execute("UPDATE queue SET scheduled_at=? WHERE enrollment_id=? AND status IN ('pending','needs_approval') "
                   "AND scheduled_at<?", (db.iso(resume), e["id"], db.iso(resume)))
        db.log_event("ooo_paused", e["contact_id"], e["id"], m["id"], f"resume {db.iso(resume)}")
        return
    if target == "stop":
        state = {"bounce": "bounced", "unsubscribe": "unsubscribed"}.get(cat, "replied")
        return finish(e, "stop", f"reply: {cat}", state=state)
    cancel_pending(e["id"], f"reply received ({cat})")
    goto(e, target, f"reply: {cat}")


def _notify_reply(m, contact, a):
    cat = a["category"]
    who = contact["email"] if contact else m["from_addr"]
    link = f"#/replies/{m['id']}"
    if not m["replied_to_id"] and cat not in ("bounce", "auto_reply", "out_of_office"):
        notify("reply", f"✉ New email from {m['from_name'] or who}", a["summary"] or m["subject"], link, "info")
        return
    if cat == "bounce":
        notify("bounce", "Bounced", f"{who} - address does not exist or rejected mail", link, "error")
    elif cat in ("interested", "question", "referral"):
        notify("interested" if cat == "interested" else "reply",
               {"interested": "🔥 Interested reply", "question": "Question from a lead",
                "referral": "Referral reply"}[cat], f"{who}: {a['summary']}", link, "success")
    elif cat not in ("auto_reply", "out_of_office"):
        notify("reply", f"Reply: {cat.replace('_', ' ')}", f"{who}: {a['summary']}", link, "info")
