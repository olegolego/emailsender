"""Passwords and API keys live in the macOS Keychain (service "EmailManager"), never in the database."""
import json
import os
import stat

from . import db

SERVICE = "EmailManager"
_FALLBACK = db.DATA_DIR / ".secrets.json"

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None


def _fallback_load():
    if _FALLBACK.exists():
        return json.loads(_FALLBACK.read_text())
    return {}


def _fallback_save(data):
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _FALLBACK.write_text(json.dumps(data))
    os.chmod(_FALLBACK, stat.S_IRUSR | stat.S_IWUSR)


def set_secret(name, value):
    if keyring is not None:
        try:
            if value:
                keyring.set_password(SERVICE, name, value)
            else:
                try:
                    keyring.delete_password(SERVICE, name)
                except Exception:
                    pass
            return "keychain"
        except Exception:
            pass
    data = _fallback_load()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    _fallback_save(data)
    return "file"


def get_secret(name):
    if keyring is not None:
        try:
            v = keyring.get_password(SERVICE, name)
            if v:
                return v
        except Exception:
            pass
    return _fallback_load().get(name)


def has_secret(name):
    return bool(get_secret(name))
