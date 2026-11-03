"""User, 3 Nov 2026: when the associate tells the needs in one voice note, don't keep asking the fixed questions:
pull the right-fit products from the note, give reasons that match what was said, and say that the picks come
from the note. Explicit asks ('55 inch Meru QLED', 'dedh ton 5 star') come first."""

from __future__ import annotations

from chanakya.brain.wants import meets_all, parse
from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import answer_complete, button_ids, check_floor, texts


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


def test_wants_are_read_in_three_languages():
    assert parse("customer ko 55 inch Meru QLED chahiye", "tv", ["Meru", "Arka"]) == {"size_in": 55, "panel": "QLED", "brand": "Meru"}
    assert parse("dedh ton 5 star chahiye", "ac", []) == {"ton": 1.5, "star": 5}
    assert parse("७ नहीं, 7 kg front load", "wm", []) == {"capacity_kg": 7.0, "load": "front"}
    assert parse("दीड टन, 3 स्टार", "ac", []) == {"ton": 1.5, "star": 3}
    assert parse("16 GB RAM laptop", "laptop", []) == {"ram_gb": 16}
    assert parse("sofa se das feet", "tv", ["Meru"]) == {}  # nothing about the product itself: no want


async def test_a_full_note_gives_the_products_at_once(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.voice("assess_1")  # living room, 10 ft, sunny, cricket, cartoons, ₹50k: the needs in one note
    check_floor(out)
    m = out[0]
    assert m.card and m.card.template == "options" and m.body.startswith("Aapke note se:")
    assert "10 ft" in m.body and "Dhoop mein bhi saaf" in m.body  # the reasons are the note's own needs
    assert button_ids(out) == ["s:C1:demo", "s:C1:ofs", "s:C1:tryon"]  # no fixed questions first
    assert "₹" not in m.body.split("\n", 1)[1]  # the budget is acknowledged, but no price before the demo
    s = await session(h)
    assert (await repo.events_for(s.id, ["note_shortlist"]))
    await h.tap("s:C1:shown")  # and the sale goes on as usual: demo → pick → the full cost
    out = await answer_complete(h, await h.tap("s:C1:opt:B"))
    assert any(x.card and x.card.template == "kharcha" for x in out)


async def test_explicit_asks_pick_the_products(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")
    out = await h.say("customer ko 55 inch Meru QLED chahiye, sofa se 9 feet, dhoop aati hai")
    m = out[0]
    assert m.card and m.card.template == "options"
    s = await session(h)
    best = h.rt.facts.product_sync(s.option_labels[next(k for k, r in s.extra["option_roles"].items() if r == "best")])
    assert meets_all(best, s.extra["wants"]) and "jaisa kaha" in m.body  # '55", QLED, Meru as asked'
    assert "₹" not in texts(out)


async def test_a_short_note_still_goes_one_question_at_a_time(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.voice("assess_short")  # room and distance only
    assert out[0].body.startswith("*Bolo*:") and "s:C1:slot:trigger:replace" in button_ids(out)
