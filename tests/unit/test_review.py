from chanakya.core.contracts import Event
from chanakya.skills.review import score


def ev(*types):
    return [Event(type=t) for t in types]


def test_golden_score_14():
    e = ev(
        "slot_filled",
        "slot_filled",
        "slot_filled",
        "slot_filled",
        "slot_filled",
        "claim_validated",
        "label_read",
        "benefit_said",
        "benefit_said",
        "options_shown",
        "demo_shown",
        "cost_shared",
        "offers_shown",
        "finance_shown",
        "parivaar_sent",
        "task_created",
        "install_job_created",
    )
    steps, _ = score(e, {"price_first_by": "none"})
    assert sum(steps.values()) == 14


def test_price_before_demo_by_associate_is_zero():
    e = ev("slot_filled", "options_shown", "cost_shared", "demo_shown", "offers_shown")
    steps, _ = score(e, {"price_first_by": "associate"})
    assert steps["close"] == 0


def test_customer_asked_override_is_one_and_partials():
    e = ev(
        "slot_filled",
        "slot_filled",
        "price_range_given",
        "options_shown",
        "demo_shown",
        "cost_shared",
        "benefit_said",
        "tryon_sent",
        "parivaar_sent",
    )
    steps, _ = score(e, {"price_first_by": "customer", "first_note_slots": 3})
    assert steps["close"] == 1 and steps["validate"] == 1 and steps["assess"] == 1 and steps["translate"] == 1
    assert steps["follow_through"] == 1
    assert score(ev("tryon_sent"), {})[0]["demonstrate"] == 1
