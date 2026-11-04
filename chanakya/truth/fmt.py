"""Formatting: INR with Indian grouping, numbers, dates and day names. Latin digits only."""

from __future__ import annotations

from datetime import date, datetime

from chanakya.core.contracts import Lang


def _indian_group(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        out = ",".join(groups) + "," + tail
    return ("-" if n < 0 else "") + out


def inr(value: float) -> str:
    """inr(62990) → '₹62,990'; inr(109990) → '₹1,09,990'. Whole rupees."""
    n = int(round(value))
    return "₹" + _indian_group(n)


def num(value: float | int) -> str:
    """num(10) → '10'; num(7.5) → '7.5'; num(12000) → '12,000'."""
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}".rstrip("0").rstrip(".")
    n = int(value)
    return _indian_group(n) if abs(n) >= 10000 else str(n)


_MONTHS = {
    "hi": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "mr": ["जाने.", "फेब्रु.", "मार्च", "एप्रि.", "मे", "जून", "जुलै", "ऑग.", "सप्टें.", "ऑक्टो.", "नोव्हें.", "डिसें."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}
_DAYS = {
    "hi": ["Somvaar", "Mangalvaar", "Budhvaar", "Guruvaar", "Shukravaar", "Shanivaar", "Ravivaar"],  # Roman Hinglish
    "mr": ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
# Roman Hinglish day names for associate-facing Hindi text
_DAYS_ROMAN_HI = ["Somvaar", "Mangalvaar", "Budhvaar", "Guruvaar", "Shukravaar", "Shanivaar", "Ravivaar"]


def date_short(d: date | datetime, lang: Lang) -> str:
    return f"{d.day} {_MONTHS[lang][d.month - 1]}"


def day_name(d: date | datetime, lang: Lang, roman: bool = False) -> str:
    if roman and lang == "hi":
        return _DAYS_ROMAN_HI[d.weekday()]
    return _DAYS[lang][d.weekday()]


def time_short(dt: datetime) -> str:
    return dt.strftime("%H:%M")


_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def latin_digits(s: str) -> str:
    return s.translate(_DEV_DIGITS)
