"""User, 3 Nov 2026: when the associate tells the needs in one voice note, don't keep asking the fixed questions:
pull the right-fit products from the note, give reasons that match what was said, and say that the picks come
from the note. Explicit asks ('55 inch Meru QLED', 'dedh ton 5 star') come first."""

from __future__ import annotations

from chanakya.brain.wants import meets_all, parse
from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import answer_complete, button_ids, check_floor, options_view, texts


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
    body = "\n\n".join(x.body or x.caption or "" for x in out)
    assert options_view(out) and (m.body or m.caption).startswith("*Note se samjha:*")  # bullets, then each page
    assert "10 ft" in body and "Dhoop mein bhi saaf" in body  # the reasons are the note's own needs
    assert button_ids(out) == ["s:C1:proceed", "s:C1:moreopts", "s:C1:doubt"]  # one clear choice (11 Nov 2026)
    assert out[-1].buttons[1].title == "Aur options"  # all 4 home facts told: nothing left for 'More details'
    s = await session(h)
    prices = [f"₹{p.price:,}" for p in (h.rt.facts.product_sync(x) for x in s.option_labels.values())]
    assert prices and not any(pr in body for pr in prices)  # the offers are with the products; no price yet
    assert (await repo.events_for(s.id, ["note_shortlist"]))
    await h.tap("s:C1:shown")  # and the sale goes on as usual: demo → pick → the full cost
    out = await answer_complete(h, await h.tap("s:C1:opt:B"))
    assert any(x.card and x.card.template == "kharcha" for x in out)


async def test_explicit_asks_pick_the_products(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")
    out = await h.say("customer ko 55 inch Meru QLED chahiye, sofa se 9 feet, dhoop aati hai")
    assert options_view(out)
    s = await session(h)
    best = h.rt.facts.product_sync(s.option_labels[next(k for k, r in s.extra["option_roles"].items() if r == "best")])
    assert meets_all(best, s.extra["wants"]) and "jaisa kaha" in texts(out)  # '55", QLED, Meru as asked'
    assert "₹" not in texts(out, footer=False)


async def test_a_short_note_gives_the_products_and_more_details(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.voice("assess_short")  # room and distance only (user, 11 Nov 2026: the products from the note)
    assert options_view(out) and button_ids(out) == ["s:C1:proceed", "s:C1:moreopts", "s:C1:doubt"]
    assert out[-1].buttons[1].title == "Aur jaankari"  # More details: the home questions the note did not cover


# ---------- the needs by voice, the product not named (user, 6 Nov 2026: "when the associate defines the home
# conditions, reqs etc by voice note (not photo); it should be able to understand and give the best fit products") ---
# Three home facts (or a clear ask) still decide: the contest scores asking about the home, and a budget is not one.
def test_the_product_is_read_from_the_needs():
    from chanakya.ai.rules import infer_category

    packs = __import__("chanakya.brain.packs", fromlist=["load_packs"]).load_packs()
    assert infer_category("living room, sofa se 10 feet, papa cricket dekhte hain", packs) == "tv"
    assert infer_category("150 sq ft bedroom, top floor, garmi bahut", packs) == "ac"
    assert infer_category("बेडरूम 140 स्क्वेअर फूट, खूप उकाडा", packs) == "ac"
    assert infer_category("5 log, roz kapde dhulte hain, bedsheet bahut", packs) == "wm"
    assert infer_category("4 log, roz khana banta hai, hafte mein sabzi laate hain", packs) == "fridge"
    assert infer_category("beta college mein, coding aur gaming", packs) == "laptop"
    assert infer_category("customer ke ghar mein 4 log hain", packs) is None  # not clear: the associate is asked


def test_a_budget_said_without_the_word():
    from chanakya.ai.rules import _norm, budget_said, explicit_budget

    for said, amount in (("35 hazaar tak", 35000), ("around 35000", 35000), ("40k ke andar", 40000),
                         ("45 हजारपर्यंत", 45000), ("lagbhag 60 hazaar", 60000), ("under 50k", 50000)):
        assert budget_said(_norm(said)) == amount and explicit_budget(said), said
    assert budget_said(_norm("online 40 hazaar tak mil raha hai")) is None  # a price seen elsewhere
    assert budget_said(_norm("bmw se aaye 2 lakh tak dikhao")) is None and not explicit_budget("BMW se aaye, 2 lakh tak")


async def test_a_note_that_does_not_name_the_product_gives_the_products(h):
    await h.tap("g:new")
    out = await h.say("150 sq ft bedroom, top floor, garmi bahut lagti hai, bijli bill kam chahiye, 40 hazaar tak")
    check_floor(out)
    s = await session(h)
    assert s.home.category == "ac" and s.home.start_level_inr == 40000 and len(s.shortlist) == 3
    cap = out[0].body or out[0].caption
    assert cap.startswith("*Note se samjha:*") and "₹40,000" in cap and out[0].card.template == "home"
    assert sum(1 for m in out if m.card is not None and m.card.template == "product") == 3


async def test_marathi_note_without_the_product(h):
    await h.tap("g:new")
    out = await h.say("बेडरूम 140 स्क्वेअर फूट, वरचा मजला, खूप उकाडा, रात्री लागतो, दोघं राहतात, 40 हजारपर्यंत")
    s = await session(h)
    assert s.home.category == "ac" and s.home.start_level_inr == 40000 and len(s.shortlist) == 3
    assert any(m.card is not None and m.card.template == "product" for m in out)


async def test_an_unclear_note_asks_the_product_then_reads_the_note_again(h):
    """Not clear which product: the associate is asked; once they tap it, the same note is read again with that
    product's questions, and the products come at once (before: the note's details were lost)."""
    await h.tap("g:new")
    out = await h.say("naya ghar hai, living room, dhoop bahut aati hai, 50 hazaar tak")  # a TV? an AC? not clear
    s = await session(h)
    assert s.home.category is None and not s.home.slots
    assert any(r.id == "s:C1:cat:tv" for m in out for sec in m.sections for r in sec.rows)
    out = await h.pick("s:C1:cat:tv")
    assert await repo.events_for(s.id, ["note_reread"])
    s = await session(h)
    assert s.home.category == "tv" and s.home.start_level_inr == 50000
    assert {"trigger", "room", "room_light"} <= set(s.home.slots) and len(s.shortlist) == 3
    assert any(m.card is not None and m.card.template == "product" for m in out)


async def test_looks_never_set_the_budget_even_with_the_needs(h):
    await h.tap("g:new")
    out = await h.say("customer BMW se aaye, branded kapde, 2 lakh tak ka dikhao, sofa se 12 feet")
    assert "dikhawat" in out[0].body.lower()  # 'I don't guess from appearance. Ask: how much have they thought of?'
    s = await session(h)
    assert s.home.start_level_inr is None
