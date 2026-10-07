"""Notifications to me: in-app bell, macOS notification centre, and optional phone push via ntfy.sh."""
import subprocess
import threading
import urllib.request

from . import db


def _macos(title, body):
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:240]
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(body)}" with title "EmailManager" subtitle "{esc(title)}" sound name "Glass"'],
                       timeout=10, capture_output=True)
    except Exception:
        pass


def _ntfy(topic, title, body, level, link):
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=(body or title).encode("utf-8"), method="POST")
        req.add_header("Title", title.encode("ascii", "ignore").decode() or "EmailManager")
        req.add_header("Priority", "high" if level in ("success", "error") else "default")
        req.add_header("Tags", {"success": "tada", "error": "warning"}.get(level, "email"))
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def notify(kind, title, body="", link="", level="info"):
    """kind is one of: reply, interested, bounce, failure, approval, summary."""
    nid = db.insert("notifications", {"ts": db.now_iso(), "level": level, "title": title,
                                      "body": body or "", "link": link or ""})
    enabled = db.get_setting("notify_on") or []
    if kind not in enabled and kind != "summary":
        return nid
    if db.get_setting("notify_macos", True):
        threading.Thread(target=_macos, args=(title, body), daemon=True).start()
    topic = (db.get_setting("notify_ntfy_topic") or "").strip()
    if topic:
        threading.Thread(target=_ntfy, args=(topic, title, body, level, link), daemon=True).start()
    return nid
