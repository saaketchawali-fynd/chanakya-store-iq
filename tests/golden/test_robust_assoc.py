"""Edge cases judges try on the associate journey (user, 6 Nov 2026: "make it robust from testing around many edge
cases by the judges"). Each test is a case a probe found going wrong, on the sample catalogue."""

from __future__ import annotations

import re

from chanakya.ai.schemas import NumberPair, Understanding
from chanakya.sim.harness import PHONES
from tests.helpers import check_floor, to_options


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


def pages(out):
    return [m for m in out if m.card is not None and m.card.template == "product"]


async def test_an_ask_is_never_shown_as_a_feature_the_product_lacks(h):
    await h.tap("g:new")
    out = await h.say("OLED 85 inch chahiye, hall, sofa se 12 feet, budget 3 lakh")
    check_floor(out)
    for m in pages(out):
        cap = m.caption or ""
        title = cap.split("\n")[0]
        if "OLED" not in title:
            assert "OLED" not in cap.split("\n")[1], cap  # never 'Picture type: OLED' on an LED TV
        if '85' not in title:
            assert '85"' not in cap.split("\n")[1], cap


async def test_a_half_fit_is_never_stated_as_a_fit(h):
    await h.tap("g:new")
    out = await h.say("TV chahiye, sofa se 8 feet, budget 20 hazaar, hall, dhoop bahut, papa cricket dekhte hain")
    for m in pages(out):
        cap = m.caption or ""
        if ' 32' in cap.split("\n")[0]:  # a 32" is not 'the right size for 8 ft' (the band is 43–55")
            assert "sahi size" not in cap and "ball blur" not in cap.lower(), cap


async def test_spoken_budgets(h):
    await h.tap("g:new")
    await h.say("Customer wants a TV for the living room, sofa is 10 feet away, budget forty five thousand, sunny room, "
                "watches cricket")
    assert (await session(h)).home.start_level_inr == 45000
    await h.say("naya customer")  # (the same 'new' button on the same message within 2 s is one tap)
    await h.say("TV chahiye, hall, sofa se 10 feet, 1.5 lakh ke andar, dhoop, cricket")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C2")
    assert s.home.start_level_inr == 150000


async def test_not_sold_is_not_a_sale_and_a_sale_needs_a_product(h):
    await to_options(h)
    await h.say("sold nahi hua")
    assert not (await session(h)).extra.get("sale_marked")
    out = await h.say("sold")
    s = await session(h)
    assert not s.extra.get("sale_marked") and any(b.id.startswith("s:C1:opt:") for m in out for b in m.buttons)


async def test_after_the_options_nothing_loops_back_to_question_one(h):
    await to_options(h)
    await h.tap("s:C1:demo")
    for said in ("LG ka dikhao", "hmm", "theek hai"):
        out = await h.say(said)
        check_floor(out)
        assert "Purana TV badalna hai" not in (out[0].body or ""), said


async def test_corrections_and_a_changed_mind(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    await h.say("sofa se 10 feet nahi, 6 feet hai")
    s = await session(h)
    assert [v.number for v in s.home.slots["seat_distance"]][-1] == 6
    await h.tap("g:new")
    await h.say("TV nahi, AC chahiye, 150 sq ft bedroom, top floor")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C2")
    assert s.home.category == "ac"


async def test_ways_to_choose_an_option(h):
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await h.say("she will take B")
    s = await session(h)
    assert s.chosen_sku == s.option_labels["B"]


async def test_a_product_named_with_no_customer_open_starts_one(h):
    out = await h.say("customer ko TV chahiye")
    check_floor(out)
    s = await session(h)
    assert s is not None and s.home.category == "tv"


async def test_a_brand_not_in_the_catalogue_is_said(h):
    await h.tap("g:new")
    out = await h.say("Samsung 55 inch chahiye, hall, sofa se 9 feet, budget 60 hazaar")
    assert re.search(r"Samsung aaj ke catalogue mein nahi", out[0].body)


async def test_price_after_a_label_is_that_products(h):
    await h.tap("g:new")
    await h.photo("label_meru55.jpg")
    out = await h.say("kitne ka hai?")
    p = h.rt.facts.product_sync("TV-MER-55-QL120")
    assert f"{p.price:,}".replace(",", "") in out[0].body.replace(",", "")  # its own price, not the range of all TVs


async def test_absurd_numbers_are_never_stored(h, monkeypatch):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")

    async def garbage(text, audio, ctx, mime=None):
        return Understanding(transcript="budget 10 crore", numbers=[NumberPair(name="budget_inr", value=100000000),
                                                                    NumberPair(name="seat_distance_ft", value=500)])

    monkeypatch.setattr(h.rt.ai, "understand", garbage)
    await h.say("budget 10 crore, sofa 500 feet")
    s = await session(h)
    assert s.home.start_level_inr is None and "seat_distance" not in s.home.slots
