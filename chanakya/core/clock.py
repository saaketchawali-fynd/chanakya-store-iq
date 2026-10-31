"""Demo-clock aware time (spec 14.6): now() = real IST time + demo offset."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_offset = timedelta(0)
_frozen: datetime | None = None  # tests can freeze time


def now() -> datetime:
    base = _frozen if _frozen is not None else datetime.now(IST)
    return (base + _offset).astimezone(IST)


def offset() -> timedelta:
    return _offset


def set_offset(td: timedelta) -> None:
    global _offset
    _offset = td


def advance(td: timedelta) -> None:
    global _offset
    _offset = _offset + td


def freeze(dt: datetime | None) -> None:
    global _frozen
    _frozen = dt.astimezone(IST) if dt else None


def today():
    return now().date()
