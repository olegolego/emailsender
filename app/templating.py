"""Message templates: {{first_name}}, {{first_name|there}} fallbacks, and {Hi|Hello} spintax."""
import json
import random
import re
from datetime import datetime

VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_\.]+)\s*(?:\|([^}]*))?\}\}")
SPIN_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def contact_vars(contact, company=None, account=None, extra=None):
    v = {}
    custom = {}
    try:
        custom = json.loads(contact.get("custom_json") or "{}")
    except Exception:
        pass
    first = (contact.get("first_name") or "").strip()
    last = (contact.get("last_name") or "").strip()
    email = contact.get("email") or ""
    v.update({
        "first_name": first,
        "last_name": last,
        "full_name": (first + " " + last).strip(),
        "email": email,
        "title": contact.get("title") or "",
        "phone": contact.get("phone") or "",
        "domain": email.split("@")[-1] if "@" in email else "",
    })
    if company:
        v.update({
            "company": company.get("name") or "",
            "company_domain": company.get("domain") or "",
            "website": company.get("website") or "",
            "category": company.get("category") or "",
        })
    else:
        v.setdefault("company", "")
    if account:
        v.update({
            "sender_name": account.get("from_name") or "",
            "sender_first_name": (account.get("from_name") or "").split(" ")[0],
            "sender_email": account.get("email") or "",
            "signature": account.get("signature") or "",
        })
    v["day_of_week"] = datetime.now().strftime("%A")
    for k, val in custom.items():
        if val not in (None, ""):
            v[str(k)] = str(val)
    if extra:
        v.update({k: val for k, val in extra.items() if val is not None})
    return v


def render(text, variables, rng=None):
    """Returns (rendered_text, missing_variable_names)."""
    if not text:
        return "", []
    rng = rng or random
    missing = []

    def sub_var(m):
        name, fallback = m.group(1), m.group(2)
        val = variables.get(name, "")
        if val in (None, ""):
            if fallback is not None:
                return fallback
            missing.append(name)
            return ""
        return str(val)

    out = VAR_RE.sub(sub_var, text)
    # spintax, innermost first
    for _ in range(10):
        new = SPIN_RE.sub(lambda m: rng.choice(m.group(1).split("|")), out)
        if new == out:
            break
        out = new
    return out, sorted(set(missing))


def variables_in(text):
    return sorted({m.group(1) for m in VAR_RE.finditer(text or "")})
