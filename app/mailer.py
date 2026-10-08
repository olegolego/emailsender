"""Real sending (SMTP) and receiving (IMAP) for a mailbox such as Google Workspace."""
import email
import email.policy
import html
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime, getaddresses

from . import db, secrets


class MailError(Exception):
    pass


def account_password(account):
    pw = secrets.get_secret(f"account:{account['id']}")
    if not pw:
        raise MailError(f"No app password saved for {account['email']} - add it in Settings → Mailboxes.")
    return pw


# ---------------------------------------------------------------- sending

def build_message(account, to_addr, subject, body, in_reply_to="", references="", unsubscribe=True):
    msg = EmailMessage()
    domain = account["email"].split("@")[-1]
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["From"] = formataddr((account.get("from_name") or "", account["email"]))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references or in_reply_to).strip()
    if unsubscribe:
        msg["List-Unsubscribe"] = f"<mailto:{account['email']}?subject=unsubscribe>"
    msg.set_content(body or "")
    return msg


def smtp_send(account, msg):
    pw = account_password(account)
    host, port = account.get("smtp_host") or "smtp.gmail.com", int(account.get("smtp_port") or 587)
    user = account.get("username") or account["email"]
    ctx = ssl.create_default_context()
    try:
        if (account.get("smtp_security") or "starttls") == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(user, pw)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("SMTP login rejected - check the app password / that 2-Step Verification is on. "
                        f"({e.smtp_code} {e.smtp_error.decode(errors='ignore')[:200]})")
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError(f"Recipient refused: {e.recipients}")
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP error: {e}")


def test_smtp(account):
    pw = account_password(account)
    host, port = account.get("smtp_host") or "smtp.gmail.com", int(account.get("smtp_port") or 587)
    user = account.get("username") or account["email"]
    ctx = ssl.create_default_context()
    if (account.get("smtp_security") or "starttls") == "ssl":
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, pw)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)


# ---------------------------------------------------------------- receiving

def _imap(account):
    pw = account_password(account)
    m = imaplib.IMAP4_SSL(account.get("imap_host") or "imap.gmail.com", int(account.get("imap_port") or 993),
                          ssl_context=ssl.create_default_context(), timeout=60)
    try:
        m.login(account.get("username") or account["email"], pw)
    except imaplib.IMAP4.error as e:
        raise MailError(f"IMAP login rejected - check the app password. ({e})")
    return m


def test_imap(account):
    m = _imap(account)
    try:
        typ, data = m.select("INBOX", readonly=True)
        return int(data[0]) if typ == "OK" else 0
    finally:
        m.logout()


def _quote_folder(name):
    name = name.strip().strip('"')
    return '"' + name.replace('"', '\\"') + '"'


_SPAM = {}


def spam_folder(account):
    """Name of the Gmail Spam folder (localised, e.g. Russian UI), found via the \\Junk flag; cached per account."""
    if account["id"] in _SPAM:
        return _SPAM[account["id"]]
    name = None
    try:
        m = _imap(account)
        try:
            typ, folders = m.list()
            for f in folders or []:
                line = f.decode(errors="ignore")
                if "\\Junk" in line:
                    name = line.split(' "/" ')[-1].strip().strip('"')
                    break
        finally:
            m.logout()
    except Exception:
        return None
    _SPAM[account["id"]] = name
    return name


HEADER_FIELDS = ("FROM REPLY-TO SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES LIST-UNSUBSCRIBE LIST-ID PRECEDENCE "
                 "AUTO-SUBMITTED CONTENT-TYPE X-FAILED-RECIPIENTS")


def fetch_new(account, folder, wanted=None, max_bodies=100):
    """Returns [(uid, raw_bytes or None)] for messages not seen before in this folder, oldest first.
    Headers of all new messages are read in one go; the full email is downloaded only when wanted(header_bytes)
    says so (None = skipped, e.g. Russian or newsletter). Never marks mail as read.
    The caller stores progress with mark_seen() after each message is processed."""
    key = f"_imap:{account['id']}:{folder}"
    state = db.get_setting(key) or {}
    m = _imap(account)
    try:
        typ, data = m.select(_quote_folder(folder), readonly=True)
        if typ != "OK":
            raise MailError(f"Cannot open folder {folder}")
        typ, uv = m.response("UIDVALIDITY")
        uidvalidity = uv[0].decode() if uv and uv[0] else ""
        last_uid = state.get("last_uid") if state.get("uidvalidity") == uidvalidity else None
        if last_uid is None:
            days = int(db.get_setting("imap_initial_days") or 14)
            since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = m.uid("search", None, "SINCE", since)
        else:
            typ, data = m.uid("search", None, "UID", f"{int(last_uid) + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split()) if typ == "OK" else []
        if last_uid is not None:
            uids = [u for u in uids if u > int(last_uid)]
        headers = {}
        for i in range(0, len(uids), 100):
            chunk = ",".join(str(u) for u in uids[i:i + 100])
            typ, resp = m.uid("fetch", chunk, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            for item in resp or []:
                if isinstance(item, tuple):
                    mu = re.search(rb"UID (\d+)", item[0])
                    if mu:
                        headers[int(mu.group(1))] = item[1]
        out, bodies = [], 0
        for uid in uids:
            hdr = headers.get(uid, b"")
            if wanted is not None and hdr and not wanted(hdr):
                out.append((uid, None))
                continue
            if bodies >= max_bodies:
                break  # the rest next time; progress stops here so nothing is skipped
            typ, msg_data = m.uid("fetch", str(uid), "(BODY.PEEK[])")
            bodies += 1
            if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                out.append((uid, msg_data[0][1]))
            else:
                out.append((uid, None))
        if not uids:
            max_uid = int(last_uid or 0)
            if last_uid is None:
                typ, d = m.uid("search", None, "ALL")
                all_uids = [int(u) for u in (d[0] or b"").split()] if typ == "OK" else []
                max_uid = max(all_uids) if all_uids else 0
            db.set_setting(key, {"uidvalidity": uidvalidity, "last_uid": max_uid})
        return out, (lambda uid: mark_seen(key, uidvalidity, uid))
    finally:
        try:
            m.logout()
        except Exception:
            pass


def mark_seen(key, uidvalidity, uid):
    state = db.get_setting(key) or {}
    prev = state.get("last_uid") if state.get("uidvalidity") == uidvalidity else 0
    db.set_setting(key, {"uidvalidity": uidvalidity, "last_uid": max(int(prev or 0), int(uid))})


# ---------------------------------------------------------------- parsing

_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_HEADERS = [
    re.compile(r"^On .{0,200}wrote:\s*$", re.I | re.M),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I | re.M),
    re.compile(r"^From:\s.*$\n^(Sent|Date):\s", re.I | re.M),
    re.compile(r"^_{5,}\s*$", re.M),
]


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h\d)>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def strip_quoted(text):
    cut = len(text)
    for rx in _QUOTE_HEADERS:
        mm = rx.search(text)
        if mm:
            cut = min(cut, mm.start())
    head = text[:cut]
    lines = [ln for ln in head.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def _body_text(msg):
    if msg.is_multipart():
        plain, htm = None, None
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").lower().startswith("attachment"):
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                payload = (part.get_payload(decode=True) or b"").decode(errors="ignore")
            if not isinstance(payload, str):
                continue
            if ctype == "text/plain" and plain is None:
                plain = payload
            elif ctype == "text/html" and htm is None:
                htm = payload
        if plain:
            return plain.strip()
        if htm:
            return html_to_text(htm)
        return ""
    try:
        payload = msg.get_content()
    except Exception:
        payload = (msg.get_payload(decode=True) or b"").decode(errors="ignore")
    if not isinstance(payload, str):
        return ""
    return html_to_text(payload) if msg.get_content_type() == "text/html" else payload.strip()


DOC_EXT = (".pdf", ".xls", ".xlsx", ".xlsm", ".csv", ".numbers", ".zip", ".doc", ".docx", ".txt", ".ods", ".rtf", ".pptx")
PRICE_WORDS = re.compile(r"price|pricing|catalog|catalogue|line ?sheet|order ?form|wholesale|msrp|map", re.I)
SHARE_HOSTS = re.compile(r"docs\.google\.com/(spreadsheets|document)|drive\.google\.com|dropbox\.com|1drv\.ms|onedrive|"
                         r"sharepoint\.com|wetransfer\.com|box\.com/s|we\.tl", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _attachments(msg):
    """Real file attachments (price lists, catalogs, applications) - not inline logos or calendar invites."""
    out = []
    for part in msg.iter_attachments():
        ctype = part.get_content_type()
        name = part.get_filename() or ""
        if ctype.startswith("message/") or ctype in ("text/calendar", "application/ics"):
            continue
        is_doc = name.lower().endswith(DOC_EXT) or ctype in (
            "application/pdf", "text/csv", "application/zip", "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if ctype.startswith("image/") and not PRICE_WORDS.search(name):
            continue  # signature logos, social icons
        if not is_doc and not ctype.startswith("image/"):
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not data or len(data) > 30_000_000:
            continue
        out.append({"filename": name or f"attachment.{ctype.split('/')[-1]}", "content_type": ctype, "data": data})
    return out


def price_links(text):
    """Links in an email that look like a price list / catalog (Google Sheets, Dropbox, '...price-list.pdf')."""
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if SHARE_HOSTS.search(u) or (PRICE_WORDS.search(u) and re.search(r"\.(pdf|xlsx?|csv)(\?|$)", u, re.I)):
            if u not in out:
                out.append(u)
    return out[:10]


def parse(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    try:
        ts = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc)
    except Exception:
        ts = datetime.now(timezone.utc)
    body = _body_text(msg)
    refs = " ".join(str(msg.get("References", "")).split())
    ctype = msg.get_content_type()
    auto = str(msg.get("Auto-Submitted", "")).lower()
    precedence = str(msg.get("Precedence", "")).lower()
    from_l = from_addr.lower()
    is_bounce = (
        ctype == "multipart/report"
        or "mailer-daemon" in from_l or from_l.startswith("postmaster@")
        or bool(msg.get("X-Failed-Recipients"))
    )
    failed = []
    if msg.get("X-Failed-Recipients"):
        failed = [a.strip().lower() for a in str(msg["X-Failed-Recipients"]).split(",") if a.strip()]
    raw_text = ""
    if is_bounce:
        try:
            raw_text = raw.decode(errors="ignore")
        except Exception:
            raw_text = body
    attachments = [] if is_bounce else _attachments(msg)
    return {
        "attachments": attachments,
        "links": [] if is_bounce else price_links(body),
        "message_id": str(msg.get("Message-ID", "")).strip(),
        "in_reply_to": str(msg.get("In-Reply-To", "")).strip(),
        "references": refs,
        "from_addr": from_l,
        "from_name": from_name,
        "to": [a.lower() for _, a in getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))]) if a],
        "subject": str(msg.get("Subject", "")),
        "ts": ts.replace(microsecond=0),
        "body": body,
        "reply_text": strip_quoted(body) if not is_bounce else body[:2000],
        "is_auto": bool(auto and auto != "no") or precedence in ("bulk", "auto_reply", "junk"),
        "is_bulk": bool(msg.get("List-Unsubscribe") or msg.get("List-Id")) or precedence in ("bulk", "list", "junk"),
        "reply_to": parseaddr(str(msg.get("Reply-To", "")))[1].lower(),
        "reply_to_name": parseaddr(str(msg.get("Reply-To", "")))[0],
        "is_bounce": is_bounce,
        "failed_recipients": failed,
        "raw_text": raw_text,
    }


CYR_RE = re.compile(r"[\u0400-\u04FF]")
LAT_RE = re.compile(r"[A-Za-z]")
URLISH_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+")
AUTOMATED_SENDER = re.compile(r"(no-?reply|do-?not-?reply|donotreply|notifications?|mailer-daemon|postmaster|bounces?)"
                              r"|^(news(letter)?|marketing|updates?|digest|billing|receipts?|security|alerts?)\b", re.I)
# Platforms that only send account / billing / system mail - never a supplier conversation.
SERVICE_DOMAINS = ("google.com", "googleusercontent.com", "cloudflare.com", "github.com", "anthropic.com",
                   "formsubmit.co", "stripe.com", "paypal.com", "intuit.com", "linkedin.com", "facebookmail.com",
                   "apple.com", "microsoft.com", "amazonses.com", "irs.gov", "michigan.gov")


def is_automated_sender(addr):
    local, _, dom = (addr or "").lower().partition("@")
    if AUTOMATED_SENDER.search(local):
        return True
    return any(dom == d or dom.endswith("." + d) for d in SERVICE_DOMAINS)


def is_russian(subject, text):
    """True when the email is written in Russian (team / personal mail). Links are ignored when counting."""
    def ratio(t):
        t = URLISH_RE.sub(" ", t or "")
        cyr, lat = len(CYR_RE.findall(t)), len(LAT_RE.findall(t))
        return cyr, lat
    cs, ls = ratio(subject)
    if cs >= 3 and cs >= ls:
        return True
    cb, lb = ratio((text or "")[:6000])
    return cb > 20 and cb >= 0.3 * (cb + lb)


MSGID_RE = re.compile(r"<[^<>\s]+@[^<>\s]+>")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
