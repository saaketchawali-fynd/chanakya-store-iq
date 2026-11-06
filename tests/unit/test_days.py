"""The day the manager asked about (core/days.py), in Hinglish, English and Marathi, against a fixed today:
Friday 6 Nov 2026."""

from __future__ import annotations

from datetime import date

import pytest

from chanakya.core.days import parse_day

TODAY = date(2026, 11, 6)  # a Friday


@pytest.mark.parametrize(
    "said, want",
    [
        ("aaj ka digest", date(2026, 11, 6)),
        ("today's report", date(2026, 11, 6)),
        ("आजचा अहवाल", date(2026, 11, 6)),
        ("kal ka digest", date(2026, 11, 5)),
        ("kal kaisa raha", date(2026, 11, 5)),
        ("yesterday summary", date(2026, 11, 5)),
        ("कल का डायजेस्ट", date(2026, 11, 5)),
        ("कालचा अहवाल", date(2026, 11, 5)),
        ("parson ka report", date(2026, 11, 4)),
        ("परसों क्या हुआ", date(2026, 11, 4)),
        ("परवाचा अहवाल", date(2026, 11, 4)),
        ("day before yesterday", date(2026, 11, 4)),
        ("Monday ka digest", date(2026, 11, 2)),
        ("somvaar kaisa gaya", date(2026, 11, 2)),
        ("सोमवारचा अहवाल", date(2026, 11, 2)),
        ("guruvaar", date(2026, 11, 5)),
        ("friday ka digest", date(2026, 11, 6)),  # today is Friday
        ("pichhle friday ka digest", date(2026, 10, 30)),  # the one before today
        ("3 Nov ka digest", date(2026, 11, 3)),
        ("3rd November report", date(2026, 11, 3)),
        ("November 3 digest", date(2026, 11, 3)),
        ("3 नवंबर का डायजेस्ट", date(2026, 11, 3)),
        ("३ नोव्हेंबरचा अहवाल", date(2026, 11, 3)),
        ("28 October", date(2026, 10, 28)),
        ("3/11 ka digest", date(2026, 11, 3)),
        ("2026-11-03", date(2026, 11, 3)),
        ("3 tareekh ka digest", date(2026, 11, 3)),
        ("3 तारखेचा अहवाल", date(2026, 11, 3)),
        ("20 tareekh ka digest", date(2026, 10, 20)),  # the 20th is still ahead: last month's
    ],
)
def test_the_day_said(said, want):
    got = parse_day(said, TODAY)
    assert got is not None and got.day == want and not got.future


def test_a_day_still_ahead_is_marked_future():
    got = parse_day("10 Dec ka digest", TODAY)
    assert got.day == date(2026, 12, 10) and got.future


def test_year_boundary():
    jan = date(2027, 1, 2)
    assert parse_day("28 Dec ka digest", jan).day == date(2026, 12, 28)
    assert parse_day("kal ka digest", jan).day == date(2027, 1, 1)
    assert parse_day("Thursday", jan).day == date(2026, 12, 31)


@pytest.mark.parametrize("said", ["digest", "team kaisi hai", "1.5 ton AC", "Ravi ke voice notes", ""])
def test_no_day_said(said):
    assert parse_day(said, TODAY) is None
