"""Send-time maths: delays, business-hours windows, weekdays, recipient timezone and random jitter."""
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db

UNITS = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}

DEFAULT_PIPELINE_SETTINGS = {
    "timezone": "",            # empty -> global setting; contact.timezone wins when set
    "use_contact_timezone": True,
    "days": [0, 1, 2, 3, 4],   # Mon..Fri
    "window_start": "09:00",
    "window_end": "17:00",
    "jitter_minutes": 25,
    "stop_on_reply": True,
    "stop_company_on_reply": True,
    "skip_if_contacted_days": 0,
}


def duration(spec):
    if not spec:
        return timedelta(0)
    if isinstance(spec, (int, float)):
        return timedelta(minutes=spec)
    value = float(spec.get("value") or 0)
    unit = spec.get("unit") or "days"
    return timedelta(seconds=value * UNITS.get(unit, 86400))


def _hm(s, default):
    try:
        h, m = (s or default).split(":")
        return int(h), int(m)
    except Exception:
        h, m = default.split(":")
        return int(h), int(m)


def tz_for(pipeline_settings, contact=None):
    name = ""
    if contact and pipeline_settings.get("use_contact_timezone", True):
        name = (contact.get("timezone") or "").strip()
    name = name or (pipeline_settings.get("timezone") or "").strip() or db.get_setting("timezone") or "America/Detroit"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/Detroit")


def in_window(dt_utc, settings, contact=None):
    s = {**DEFAULT_PIPELINE_SETTINGS, **(settings or {})}
    tz = tz_for(s, contact)
    local = dt_utc.astimezone(tz)
    days = s.get("days") or list(range(7))
    sh, sm = _hm(s.get("window_start"), "09:00")
    eh, em = _hm(s.get("window_end"), "17:00")
    start = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    return local.weekday() in days and start <= local < end


def next_window_time(dt_utc, settings, contact=None, rng=None):
    """Earliest moment >= dt_utc inside the send window (with a small random offset when moved)."""
    rng = rng or random
    s = {**DEFAULT_PIPELINE_SETTINGS, **(settings or {})}
    if in_window(dt_utc, s, contact):
        return dt_utc
    tz = tz_for(s, contact)
    days = s.get("days") or list(range(7))
    sh, sm = _hm(s.get("window_start"), "09:00")
    eh, em = _hm(s.get("window_end"), "17:00")
    local = dt_utc.astimezone(tz)
    for i in range(0, 15):
        day = (local + timedelta(days=i)).date()
        start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
        if start.weekday() not in days:
            continue
        if local < start:
            span = max(60, min(3600, int((end - start).total_seconds() / 4)))
            return (start + timedelta(seconds=rng.randint(0, span))).astimezone(timezone.utc)
        if start <= local < end:
            return dt_utc
    return dt_utc


def plan_send_time(base_utc, delay_spec, settings, contact=None, rng=None):
    """base + delay + jitter, pushed into the next allowed window."""
    rng = rng or random
    s = {**DEFAULT_PIPELINE_SETTINGS, **(settings or {})}
    t = base_utc + duration(delay_spec)
    jitter = int(s.get("jitter_minutes") or 0)
    if jitter > 0:
        t += timedelta(seconds=rng.randint(0, jitter * 60))
    return next_window_time(t, s, contact, rng).replace(microsecond=0)


def local_day_bounds(tzname=None):
    tz = ZoneInfo(tzname or db.get_setting("timezone") or "America/Detroit")
    now_local = datetime.now(tz)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)
