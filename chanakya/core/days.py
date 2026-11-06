"""The day the manager asked about, in a voice note or typed (user, 6 Nov 2026: "When voice note asks for digest
and any day r date it should give a consolidated analysis"): 'aaj', 'kal', 'parson', 'Monday', '3 Nov', '3 tareekh',
in Hinglish, English and Marathi, Roman or Devanagari. Rules only (instant, and the same answer every time).

A digest is always about the past, so 'kal' / 'कल' is yesterday. A date with no year is this year's, unless it is
more than two months ahead (then last year's); a date still ahead is returned with future=True."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from chanakya.truth.fmt import latin_digits

TODAY = {"aaj", "aj", "today", "आज", "आजचा", "आजचं", "आजचे", "आजची"}
YESTERDAY = {"kal", "kall", "yesterday", "कल", "काल", "कालचा", "कालचं", "कालचे", "कालची", "kalcha"}
DAY_BEFORE = {"parson", "parso", "parsoon", "parsun", "परसों", "परसो", "परवा", "परवाचा", "परवाचं", "parva"}
LAST = {"pichhle", "pichle", "picchle", "pichhla", "pichla", "last", "previous", "पिछले", "पिछला", "मागच्या", "मागील",
        "गेल्या"}
WEEKDAYS = [  # Monday = 0
    ("monday", "somvaar", "somvar", "somwar", "सोमवार"),
    ("tuesday", "mangalvaar", "mangalvar", "mangalwar", "मंगलवार", "मंगळवार"),
    ("wednesday", "budhvaar", "budhvar", "budhwar", "बुधवार"),
    ("thursday", "guruvaar", "guruvar", "guruwar", "veervaar", "veervar", "गुरुवार", "गुरूवार", "वीरवार"),
    ("friday", "shukravaar", "shukravar", "shukrawar", "शुक्रवार"),
    ("saturday", "shanivaar", "shanivar", "shaniwar", "शनिवार"),
    ("sunday", "ravivaar", "ravivar", "raviwar", "itvaar", "itwar", "रविवार", "इतवार"),
]
MONTHS = [  # Roman (exact) and Devanagari (Hindi and Marathi; long names also match with a suffix: नोव्हेंबरचा)
    ("jan", "january", "janvari", "जनवरी", "जानेवारी"),
    ("feb", "february", "farvari", "फरवरी", "फ़रवरी", "फेब्रुवारी"),
    ("mar", "march", "मार्च"),
    ("apr", "april", "अप्रैल", "एप्रिल"),
    ("may", "मई", "मे"),
    ("jun", "june", "जून"),
    ("jul", "july", "जुलाई", "जुलै"),
    ("aug", "august", "agast", "अगस्त", "ऑगस्ट"),
    ("sep", "sept", "september", "sitambar", "सितंबर", "सितम्बर", "सप्टेंबर"),
    ("oct", "october", "aktubar", "अक्टूबर", "अक्तूबर", "ऑक्टोबर"),
    ("nov", "november", "navambar", "नवंबर", "नवम्बर", "नोव्हेंबर"),
    ("dec", "december", "disambar", "दिसंबर", "दिसम्बर", "डिसेंबर"),
]
TAREEKH = ("tareekh", "tarikh", "tareek", "tarik", "date", "तारीख", "तारीखला", "तारखेचा", "तारखेला", "तारखेचं")
_SUFFIX = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)$")


RANGE = ("week", "hafte", "hafta", "hafton", "month", "mahina", "mahine", "year", "saal", "sal", "आठवड", "हफ्त", "हफ़्त",
         "महिन", "महीन", "वर्ष", "साल")
NEGATE = {"nahi", "nahin", "not", "na", "नाही", "नहीं", "नको"}


@dataclass(frozen=True)
class AskedDay:
    day: date | None
    future: bool = False
    bad: str | None = None  # 'invalid' (a date that does not exist: 31 Feb) | 'range' (a week, a month, a year)


def _month(tok: str) -> int | None:
    for i, names in enumerate(MONTHS, 1):
        for n in names:
            if tok == n or (not n.isascii() and len(n) >= 4 and tok.startswith(n)):
                return i
    return None


def _weekday(tok: str) -> int | None:
    for i, names in enumerate(WEEKDAYS):
        for n in names:
            if tok == n or tok == n + "s" or (not n.isascii() and tok.startswith(n)):
                return i
    return None


def _tokens(text: str) -> list[str]:
    s = latin_digits(text or "").lower()
    s = re.sub(r"[,!?;:\"'()“”‘’।]", " ", s)
    out = []
    for t in s.split():
        t = t.strip(".")
        m = _SUFFIX.match(t)
        out.append(m.group(1) if m else t)
    return [t for t in out if t]


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _no_year(m: int, d: int, today: date) -> AskedDay | None:
    """This year's date; more than two months ahead means last year's (in January, '28 Dec' is last month)."""
    x = _valid(today.year, m, d)
    if x is None:
        return None
    if x > today + timedelta(days=62):
        x = _valid(today.year - 1, m, d) or x
    return AskedDay(x, x > today)


def parse_day(text: str, today: date) -> AskedDay | None:
    """The day asked about, or None when no day was said. 'kal ka nahi, aaj ka' → today (what follows a 'not');
    '31 Feb' → bad='invalid'; 'last week' / 'is mahine' → bad='range' (one day at a time)."""
    toks = _tokens(text)
    if not toks:
        return None
    neg = [i for i, t in enumerate(toks) if t in NEGATE]
    if neg and neg[-1] < len(toks) - 1:  # what was said after the 'not' wins
        tail = _parse(toks[neg[-1] + 1:], today)
        if tail is not None:
            return tail
    return _parse(toks, today)


def _parse(toks: list[str], today: date) -> AskedDay | None:
    if any(t.startswith(r) if not r.isascii() else t == r or t == r + "s" for t in toks for r in RANGE):
        return AskedDay(None, bad="range")
    joined = " ".join(toks)
    m = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", joined)  # 2026-11-03
    if m:
        x = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if x:
            return AskedDay(x, x > today)
    m = re.search(r"(?<![\d.])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?![\d.])", joined)  # 3/11, 3-11-2026 (never 1.5)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            x = _valid(y + 2000 if y < 100 else y, mo, d)
            if x:
                return AskedDay(x, x > today)
        elif 1 <= mo <= 12:
            got = _no_year(mo, d, today)
            if got:
                return got
    for i, t in enumerate(toks):  # '3 Nov', 'Nov 3', '3 नोव्हेंबर', with an optional year after
        mo = _month(t)
        if mo is None:
            continue
        before = toks[i - 1] if i > 0 else ""
        after = toks[i + 1] if i + 1 < len(toks) else ""
        d = int(before) if before.isdigit() and len(before) <= 2 else int(after) if after.isdigit() and len(after) <= 2 else None
        if d is None:
            continue
        rest = toks[i + 1:i + 3]
        year = next((int(r) for r in rest if r.isdigit() and len(r) == 4), None)
        if year:
            x = _valid(year, mo, d)
            if x:
                return AskedDay(x, x > today)
            return AskedDay(None, bad="invalid")
        got = _no_year(mo, d, today)
        if got:
            return got
        return AskedDay(None, bad="invalid")  # '31 Feb': no such day
    for i, t in enumerate(toks):  # '3 tareekh': this month's 3rd, or last month's when it is still ahead
        if any(t == w or (not w.isascii() and t.startswith(w[:4])) for w in TAREEKH) and i > 0 and toks[i - 1].isdigit():
            d = int(toks[i - 1])
            x = _valid(today.year, today.month, d)
            if x and x > today:
                prev = today.replace(day=1) - timedelta(days=1)
                x = _valid(prev.year, prev.month, d)
            if x:
                return AskedDay(x)
    for i, t in enumerate(toks):  # 'Monday': the latest one; 'pichhle Monday': the latest before today
        wd = _weekday(t)
        if wd is None:
            continue
        back = (today.weekday() - wd) % 7
        if back == 0 and i > 0 and toks[i - 1] in LAST:
            back = 7
        return AskedDay(today - timedelta(days=back))
    words = set(toks)
    if words & DAY_BEFORE or "day before yesterday" in joined:
        return AskedDay(today - timedelta(days=2))
    if words & YESTERDAY:
        return AskedDay(today - timedelta(days=1))
    if words & TODAY:
        return AskedDay(today)
    return None
