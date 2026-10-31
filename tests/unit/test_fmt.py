from datetime import date

from chanakya.truth import fmt


def test_inr_indian_grouping():
    assert fmt.inr(62990) == "₹62,990"
    assert fmt.inr(109990) == "₹1,09,990"
    assert fmt.inr(124990) == "₹1,24,990"
    assert fmt.inr(999) == "₹999"
    assert fmt.inr(4832.5) == "₹4,832" or fmt.inr(4832.5) == "₹4,833"
    assert fmt.inr(10000000) == "₹1,00,00,000"


def test_num_and_dates():
    assert fmt.num(10) == "10"
    assert fmt.num(7.5) == "7.5"
    assert fmt.date_short(date(2026, 11, 15), "hi") == "15 Nov"
    assert fmt.date_short(date(2026, 11, 15), "mr") == "15 नोव्हें."
    assert fmt.day_name(date(2026, 11, 2), "en") == "Monday"
    assert fmt.latin_digits("१२३") == "123"
