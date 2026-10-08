"""Address checks before sending: does the recipient's domain have a working mail server?

    .venv/bin/python -m app.verify          # check everyone in ready batches and the send queue

A domain with no mail server (no MX record, a "null MX", or a domain that doesn't exist) means a guaranteed bounce.
Bounces hurt the sending reputation of eigenreach.com, so such addresses are removed before anything is sent.
(A missing mailbox on a working domain - "user doesn't exist" - can only be found by the bounce itself or a paid
verification service; the app then marks that address as bounced automatically.)
"""
import sys
import concurrent.futures as cf

import dns.exception
import dns.resolver

from . import db

SCHEMA = """CREATE TABLE IF NOT EXISTS domain_checks (
  domain TEXT PRIMARY KEY, ok INTEGER, reason TEXT, checked_at TEXT)"""

_resolver = dns.resolver.Resolver()
_resolver.timeout = 4
_resolver.lifetime = 8


def _lookup(domain):
    """Returns (ok, reason). ok=None means 'could not tell' (DNS timeout) - never treated as bad."""
    try:
        answers = _resolver.resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in answers]
        if not hosts or hosts == [""]:
            return False, "domain refuses all email (null MX)"
        return True, "mail server: " + hosts[0]
    except dns.resolver.NXDOMAIN:
        return False, "domain does not exist"
    except dns.resolver.NoAnswer:
        try:  # no MX: mail may still go to the domain's own server (A record)
            _resolver.resolve(domain, "A")
            return True, "no MX, uses A record"
        except Exception:
            return False, "domain has no mail server"
    except dns.resolver.NoNameservers:
        return False, "domain's DNS is broken"
    except (dns.exception.Timeout, Exception):
        return None, "DNS timeout"


def check_domain(domain, use_cache=True):
    db.execute(SCHEMA)
    domain = (domain or "").lower().strip()
    if use_cache:
        r = db.q1("SELECT ok, reason FROM domain_checks WHERE domain=?", (domain,))
        if r and r["ok"] is not None:
            return bool(r["ok"]), r["reason"]
    ok, reason = _lookup(domain)
    db.execute("INSERT INTO domain_checks(domain, ok, reason, checked_at) VALUES (?,?,?,?) "
               "ON CONFLICT(domain) DO UPDATE SET ok=excluded.ok, reason=excluded.reason, checked_at=excluded.checked_at",
               (domain, None if ok is None else int(ok), reason, db.now_iso()))
    return ok, reason


def mark_invalid(contact_id, reason):
    """Removes a dead address from ready batches and stops it everywhere."""
    from . import engine
    db.update("contacts", contact_id, {"status": "invalid", "notes": f"Not sent: {reason}"})
    db.execute("DELETE FROM batch_members WHERE contact_id=? AND batch_id IN (SELECT id FROM batches WHERE status='draft')",
               (contact_id,))
    for e in db.q("SELECT * FROM enrollments WHERE contact_id=? AND state IN ('active','paused')", (contact_id,)):
        engine.finish(e, "stop", f"address can't receive email: {reason}", state="bounced")


def check_all():
    db.execute(SCHEMA)
    rows = db.q("SELECT DISTINCT c.id, c.email FROM contacts c WHERE c.status NOT IN ('invalid','bounced','unsubscribed') AND ("
                "c.id IN (SELECT contact_id FROM batch_members m JOIN batches b ON b.id=m.batch_id WHERE b.status='draft') OR "
                "c.id IN (SELECT contact_id FROM queue WHERE status IN ('pending','needs_approval')) OR "
                "c.id IN (SELECT contact_id FROM enrollments WHERE state IN ('active','paused')))")
    domains = sorted({r["email"].split("@")[-1] for r in rows})
    results = {}
    with cf.ThreadPoolExecutor(32) as ex:
        for d, res in zip(domains, ex.map(_lookup, domains)):
            results[d] = res
    for d, (ok, reason) in results.items():
        db.execute("INSERT INTO domain_checks(domain, ok, reason, checked_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(domain) DO UPDATE SET ok=excluded.ok, reason=excluded.reason, checked_at=excluded.checked_at",
                   (d, None if ok is None else int(ok), reason, db.now_iso()))
    bad = []
    for r in rows:
        ok, reason = results[r["email"].split("@")[-1]]
        if ok is False:
            mark_invalid(r["id"], reason)
            bad.append((r["email"], reason))
    unsure = sum(1 for ok, _ in results.values() if ok is None)
    return {"addresses": len(rows), "domains": len(domains), "removed": bad, "dns_timeouts": unsure}


if __name__ == "__main__":
    db.init()
    res = check_all()
    print(f"checked {res['addresses']} addresses on {res['domains']} domains; removed {len(res['removed'])}; "
          f"DNS timeouts (kept) {res['dns_timeouts']}")
    for e, why in res["removed"][: int(sys.argv[1]) if len(sys.argv) > 1 else 40]:
        print(f"  {e:45s} {why}")
