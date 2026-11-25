"""Time-formatting helpers that remain reliable on minimal Termux installs."""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def india_timezone():
    """Return Asia/Kolkata when system tzdata exists, else fixed UTC+05:30."""
    try:
        return ZoneInfo("Asia/Kolkata")
    except (ZoneInfoNotFoundError, OSError):
        # India uses UTC+05:30 year-round. This keeps Buy/Notify callbacks
        # working in Termux even when the OS IANA timezone database is absent.
        return timezone(timedelta(hours=5, minutes=30), name="IST")


def format_created_at(value):
    if not value:
        return "Time unavailable"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return str(value)
    return dt.astimezone(india_timezone()).strftime("%d %b %Y, %I:%M:%S %p IST")
