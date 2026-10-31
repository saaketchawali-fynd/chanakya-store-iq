"""Offline (no-model) understanding of associate text."""

from chanakya.ai.rules import understand_text
from chanakya.brain.packs import load_packs

R = load_packs()


def test_hinglish_note():
    u = understand_text(
        "Shah ji ka living room, sofa se das feet, dopahar mein dhoop aati hai, papa cricket dekhte "
        "hain, budget 50 hazaar",
        R,
        "tv",
    )
    nums = {n.name: n.value for n in u.numbers}
    assert nums["seat_distance_ft"] == 10 and nums["budget_inr"] == 50000
    slots = {(s.slot, s.value) for s in u.slots}
    assert ("room_light", "sunny") in slots and ("content", "sports") in slots
    assert u.customer_name == "Shah ji" and u.sports_viewer == "papa"


def test_never_infers_budget_without_budget_word():
    u = understand_text("customer ki gaadi 20 lakh ki hai", R, "tv")
    assert "budget_inr" not in {n.name for n in u.numbers}


def test_price_question_and_steps():
    u = understand_text("ye TV kitne ka hai?", R, "tv")
    assert "price_asked" in u.intents
    u2 = understand_text("sofa se 4 kadam", R, "tv")
    assert {n.name: n.value for n in u2.numbers}["seat_distance_ft"] == 10


def test_marathi_devanagari():
    u = understand_text("खोलीत ऊन येतं, टीव्ही भिंतीवर लावणार", R, "tv")
    slots = {(s.slot, s.value) for s in u.slots}
    assert ("room_light", "sunny") in slots and ("mounting", "wall") in slots
