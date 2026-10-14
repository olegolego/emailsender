"""Price-list finder: when a supplier answers with a link to their website / "live catalog" instead of a file,
visit the site, find downloadable price lists (PDF, Excel, CSV, ZIP, Google Sheets) and save them to Price lists.

No AI - plain HTTP and link scoring, so it costs nothing and can run automatically. Polite: respects robots.txt,
fetches at most ~15 pages per supplier, 25 MB per file. Also copies order terms (MOQ, minimum order, payment,
shipping) from the email into the price list's note.
"""
import re
import threading
import traceback
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

from . import db
from .notifier import notify

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
FILE_EXT = re.compile(r"\.(pdf|xlsx|xlsm|xls|csv|zip|docx)(\?|#|$)", re.I)
SHEETS = re.compile(r"docs\.google\.com/spreadsheets/d/([\w-]+)|drive\.google\.com/file/d/([\w-]+)", re.I)
GOOD_WORDS = re.compile(r"price|pricing|catalog|catalogue|line ?sheet|order ?form|wholesale|dealer|distributor|"
                        r"reseller|msrp|map ?policy|products? list|retailer|b2b", re.I)
PAGE_WORDS = re.compile(r"wholesale|price|pricing|catalog|catalogue|dealer|distributor|reseller|retailer|b2b|"
                        r"download|resources|line ?sheet|order ?form|become|apply|partners?", re.I)
LOGIN_WORDS = re.compile(r"log ?in to see|login to view|sign in to see|wholesale login|create an account to view|"
                         r"approved accounts? only|price(s)? (are )?(only )?(visible|available) (after|to)", re.I)
TERMS_LINE = re.compile(r"\b(moq|mov|minimum order|min\.? order|minimum purchase|order minimum|per sku|"
                        r"payment|net ?\d{2}|ach|wire|credit card|prepay|free shipping|shipping|fba prep|"
                        r"lead time|processing|business days|discount)\b", re.I)
MAX_PAGES, MAX_FILES, MAX_BYTES = 15, 5, 25_000_000


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._a, self._txt = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._a = dict(attrs).get("href")
            self._txt = []

    def handle_data(self, data):
        if self._a is not None:
            self._txt.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.links.append((self._a, " ".join("".join(self._txt).split())[:120]))
            self._a = None


def _get(url, limit=3_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        data = r.read(limit + 1)
        return r.geturl(), ctype, data


def _robots(base):
    rp = urllib.robotparser.RobotFileParser()
    try:
        _, _, data = _get(urllib.parse.urljoin(base, "/robots.txt"), 300_000)
        rp.parse(data.decode("utf-8", "ignore").splitlines())
    except Exception:
        rp.parse([])  # no robots.txt -> allowed
    return rp


def order_terms(text):
    """Lines from an email that describe order terms: MOQ, minimum order value, payment, shipping."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip(" •-*\t")
        if 6 < len(ln) < 220 and TERMS_LINE.search(ln) and not ln.lower().startswith(("http", "on ", ">")):
            if ln not in out:
                out.append(ln)
    return out[:10]


def _host(u):
    return urllib.parse.urlparse(u).netloc.lower().split(":")[0].removeprefix("www.")


def find(message_row_id):
    """Crawl the supplier's site for price lists. Returns a short summary string."""
    from . import mailer
    m = db.q1("SELECT m.*, co.domain AS co_domain, co.website AS co_site FROM messages m "
              "LEFT JOIN companies co ON co.id=m.company_id WHERE m.id=?", (message_row_id,))
    if not m:
        return "message not found"
    text = m["reply_text"] or m["body"] or ""
    sender_dom = (m["from_addr"] or "").split("@")[-1].lower()
    roots = []
    for u in mailer.URL_RE.findall(text):
        u = u.rstrip(".,;)")
        h = _host(u)
        if h and (h.endswith(sender_dom) or sender_dom.endswith(h) or (m["co_domain"] and h.endswith(m["co_domain"]))
                  or GOOD_WORDS.search(u)):
            if u not in roots:
                roots.append(u)
    if not roots:
        site = m["co_site"] or (f"https://{m['co_domain']}" if m["co_domain"] else "")
        if not site and sender_dom and sender_dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com"):
            site = f"https://{sender_dom}"
        if site:
            roots.append(site)
    if not roots:
        return "no website to check"

    terms = order_terms(text)
    note = ("Terms from the email: " + " · ".join(terms)) if terms else ""
    pages, files, login_needed, seen = [], {}, False, set()
    queue = list(roots[:3])
    robots = {}
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
        rp = robots.setdefault(base, _robots(base))
        if not rp.can_fetch(UA, url):
            continue
        try:
            final, ctype, data = _get(url)
        except Exception:
            continue
        if FILE_EXT.search(final) or ctype in ("application/pdf", "text/csv", "application/zip") or "spreadsheet" in ctype \
                or "excel" in ctype:
            files.setdefault(final, ("", 10))
            continue
        if "html" not in ctype:
            continue
        pages.append(final)
        html = data.decode("utf-8", "ignore")
        if LOGIN_WORDS.search(html):
            login_needed = True
        p = _Links()
        try:
            p.feed(html)
        except Exception:
            continue
        for href, label in p.links:
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = urllib.parse.urljoin(final, href).split("#")[0]
            score_txt = f"{full} {label}"
            sh = SHEETS.search(full)
            if FILE_EXT.search(full) or sh:
                score = 3 if GOOD_WORDS.search(score_txt) else 1
                if score >= 1 and (GOOD_WORDS.search(score_txt) or _host(full).endswith(sender_dom) or sh):
                    prev = files.get(full, ("", 0))
                    files[full] = (label or prev[0], max(prev[1], score))
            elif _host(full) == _host(final) and PAGE_WORDS.search(score_txt) and full not in seen and len(queue) < 40:
                queue.append(full)

    saved = 0
    folder = db.DATA_DIR / "attachments" / "web" / str(message_row_id)
    ranked = sorted(files.items(), key=lambda kv: -kv[1][1])
    if any(sc >= 3 for _, (_, sc) in ranked):  # clearly labelled price lists exist: skip brochures & other PDFs
        ranked = [kv for kv in ranked if kv[1][1] >= 3]
    for url, (label, score) in ranked[:MAX_FILES]:
        if db.scalar("SELECT COUNT(*) FROM price_lists WHERE url=?", (url,)):
            continue
        dl = url
        sh = SHEETS.search(url)
        if sh and sh.group(1):
            dl = f"https://docs.google.com/spreadsheets/d/{sh.group(1)}/export?format=xlsx"
        elif sh and sh.group(2):
            dl = f"https://drive.google.com/uc?export=download&id={sh.group(2)}"
        try:
            final, ctype, data = _get(dl, MAX_BYTES)
        except Exception:
            continue
        if len(data) > MAX_BYTES or "html" in ctype or len(data) < 200:
            if sh:  # private Google file: keep the link at least
                db.insert("price_lists", {"message_row_id": message_row_id, "contact_id": m["contact_id"],
                                          "company_id": m["company_id"], "kind": "link", "url": url,
                                          "filename": label or "Google file (needs access)", "received_at": db.now_iso(),
                                          "notes": "Found on their website - needs access. " + note})
                saved += 1
            continue
        name = urllib.parse.unquote(urllib.parse.urlparse(final).path.split("/")[-1]) or "price-list"
        if sh:
            name = (label or "Google sheet") + ".xlsx"
        if not FILE_EXT.search(name):
            ext = {"application/pdf": ".pdf", "text/csv": ".csv", "application/zip": ".zip"}.get(ctype, "")
            name += ext or ".xlsx"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\- ()]+", "_", name)[:120]
        path = folder / safe
        path.write_bytes(data)
        db.insert("price_lists", {"message_row_id": message_row_id, "contact_id": m["contact_id"],
                                  "company_id": m["company_id"], "kind": "file", "filename": name, "url": url,
                                  "path": str(path), "size": len(data), "content_type": ctype,
                                  "received_at": db.now_iso(), "notes": ("Found on their website. " + note).strip()})
        saved += 1

    who = m["co_domain"] or sender_dom
    if saved:
        notify("interested", f"🔎 Found {saved} price list file(s) on {who}", "Saved to Price lists", "#/prices", "success")
        return f"found {saved} file(s) on {who}"
    # nothing downloadable: record the catalog link so it isn't lost
    main = roots[0]
    if not db.scalar("SELECT COUNT(*) FROM price_lists WHERE url=? AND message_row_id=?", (main, message_row_id)):
        why = "prices need a wholesale login" if login_needed else "online catalog, no downloadable file found"
        db.insert("price_lists", {"message_row_id": message_row_id, "contact_id": m["contact_id"],
                                  "company_id": m["company_id"], "kind": "link", "url": main,
                                  "filename": f"{who} - {why}", "received_at": db.now_iso(), "notes": note})
    notify("reply", f"🔎 {who}: no downloadable price list", "Saved the catalog link and order terms to Price lists",
           "#/prices", "info")
    return f"no file on {who}; saved the catalog link ({len(pages)} pages checked)"


def mentions_catalog(text):
    return bool(re.search(r"catalog|catalogue|price ?list|pricing|our (web)?site|online store|wholesale portal|"
                          r"apply (now|online)|sign ?up|https?://", text or "", re.I))


def start(message_row_id):
    def job():
        try:
            find(message_row_id)
        except Exception:
            traceback.print_exc()
        finally:
            db.close()
    threading.Thread(target=job, daemon=True, name=f"pricefinder-{message_row_id}").start()
