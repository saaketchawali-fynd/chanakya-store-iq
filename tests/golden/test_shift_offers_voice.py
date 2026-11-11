"""User feedback, 2 Nov 2026 (evening):
- ONE visual for customer and associate, in English (no copy in another language);
- the shift start goes one step at a time: the pre-read (stock, offers, keep in mind: data only) → the clip → ready
  → the customer button;
- offers are always shown with the products, and one tap away;
- every question says why, with numbers from today's stock, and can be answered by voice (hi / en / mr);
- a voice note that adds nothing new shows what was heard."""

from __future__ import annotations

import re

from chanakya.ai.schemas import Understanding
from chanakya.core import repo
from chanakya.core.clock import today
from chanakya.sim.harness import PHONES
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.helpers import button_ids, check_floor, open_brief, options_view, pick, to_options
from tests.unit.test_jio import ITEMS, PROMO, backend, item, live


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


async def test_shift_start_is_one_step_at_a_time(h):
    out = await h.say("namaste")
    check_floor(out)
    assert not any(m.card for m in out)  # first: which product (stock and offers for that product)
    out = await open_brief(h, out, "tv")
    check_floor(out)
    m = next(m for m in out if m.card and m.card.template == "brief")
    assert m.card.lang == "en" and button_ids(out) == ["g:clip:translate", "g:brief"]  # the clip next, or another product
    d = m.card.data
    assert d["stock"]["n"] and d["stock"]["tiles"] and d["offers"]  # stock and offers from the data
    for k in d["keep"]:  # every 'keep in mind' point carries a number from the data, never a generic rule
        assert re.search(r"\d", k), k
    out = await h.tap("g:clip:translate")
    assert any(x.kind == "video" for x in out) and button_ids(out) == ["g:ready:translate"]
    out = await h.tap("g:ready:translate")
    check_floor(out)
    assert button_ids(out) == ["g:new"] and out[0].footer  # only now: the customer (or a voice note)


async def test_live_data_brief_never_invents_stock_counts(tmp_path, monkeypatch):
    """Jio gives availability, not counts: no 'only 1 left'; out of stock since yesterday is real (two syncs)."""
    monkeypatch.setattr(JioClient, "transport", backend())
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        out = await open_brief(h, await h.say("namaste"), "tv")
        b = next(m for m in out if m.card and m.card.template == "brief")
        assert not [c for c in b.card.data["changes"] if c["kind"] == "low"]
        assert "out of" not in str(b.card.data["changes"])  # nothing changed yet
        sony = item("Sony 108 cm (43 inch)")
        assert sony.get("sellable")
        await h.clock("+1d")  # the next day the backend stops selling it: out of stock since yesterday
        monkeypatch.setitem(sony, "sellable", False)
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        changes = await h.rt.facts.stock_changes(h.rt.settings.store_id, "tv")
        assert [c["kind"] for c in changes] == ["out_of_stock"] and changes[0]["sku"]
        assert len(ITEMS) > 3
    finally:
        await h.close()


async def test_offers_are_always_with_the_products(h):
    out = await to_options(h)
    card = options_view(out)
    assert all(o["offer"]["text"] for o in card.data["options"])  # each product page shows its offer
    out = await h.tap("s:C1:more")  # 'Tell me more' with the products: the offers on these are one row away
    assert "s:C1:ofs" in [r.id for m in out for sec in (m.sections or []) for r in sec.rows]
    out = await h.pick("s:C1:ofs")
    check_floor(out)
    rows = [r for m in out for sec in (m.sections or []) for r in sec.rows]
    assert rows and all(r.title[:1] in "ABC" for r in rows)  # per option
    out = await h.pick(rows[0].id)
    check_floor(out)
    assert out[0].body.startswith("*Bolo*:") and "s:C1:ofs" in button_ids(out)
    s = await session(h)
    assert s.chosen_sku is None  # looking at offers never skips the pick and the full cost


async def test_no_offers_says_so_plainly(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend())  # UAT: no promotions
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        for b in ("g:new", "s:C1:lang:en"):
            await h.tap(b)
        await h.voice("assess_1")
        for b in ("s:C1:slot:trigger:new_home", "s:C1:photo_no", "s:C1:readback_ok"):
            out = await h.tap(b)
        out = await h.tap("s:C1:options")
        assert all(o["offer"]["none"] for o in options_view(out).data["options"])
        out = await h.tap("s:C1:ofs")
        assert "live offer" in out[0].body.lower() and out[0].buttons  # honest, and a next step
    finally:
        await h.close()


async def test_offer_with_a_promotion_on_live_data(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend({item("Panasonic 139.7 cm (55 inch)")["slug"]: [PROMO]}))
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        out = await h.say("catalog")
        assert out
        o = h.rt.facts.snap.offers
        assert o and o[0].valid_to >= today()
    finally:
        await h.close()


async def test_every_question_has_a_voice_option_and_a_stock_reason(h):
    await h.say("lang en")
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.pick("s:C1:cat:tv")
    seen = []
    for b in ("s:C1:slot:trigger:new_home", "s:C1:slot:room:living", "s:C1:slot:seat_distance:d8_10"):
        assert out[0].footer and "voice note" in out[0].footer.lower()  # voice first, at every question
        seen.append(out[0].body)
        out = await h.tap(b)
    seen.append(out[0].body)
    assert any("in stock" in b and "inch" in b for b in seen)  # the distance question: the sizes in stock
    assert re.search(r"of \d+ TVs in stock qualify", seen[-1])  # sunlight: how many models are bright enough


async def test_voice_note_with_nothing_new_shows_what_was_heard(h, monkeypatch):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")

    async def heard(text, audio, ctx, mime=None):
        return Understanding(transcript="haan ji, ek minute dekhte hain", language="hi")

    monkeypatch.setattr(h.rt.ai, "understand", heard)
    out = await h.voice_bytes(b"OggS-test")
    check_floor(out)
    assert out[0].footer.startswith("Suna:") and "ek minute" in out[0].footer


async def test_one_card_per_visual_everywhere(h):
    """Options, bill, offer card and try-on: one visual each, in English (no copy for the associate)."""
    out = await to_options(h, photo="room_living.jpg")
    n_pages = sum(1 for m in out if m.card and m.card.template == "product")
    assert n_pages == len(options_view(out).data["options"])  # one page per option, no copy
    assert sum(1 for m in out if m.card and m.card.template == "home") == 1  # and the Home Card, once
    await h.tap("s:C1:shown")
    out = await pick(h)
    assert sum(1 for m in out if m.card or m.image_bytes) == 1 and all(m.card.lang == "en" for m in out if m.card)


async def test_brief_for_the_product_chosen(h):
    """User, 6 Nov 2026: stock and offers only ever showed TVs. Now the product is chosen first, each row with its
    numbers for today, and the card is that product's, from today's data."""
    out = await h.say("namaste")
    rows = {r.id: r.description for m in out for sec in (m.sections or []) for r in sec.rows}
    assert {"g:briefcat:tv", "g:briefcat:ac", "g:briefcat:wm", "g:briefcat:fridge"} <= set(rows)
    assert all(re.search(r"\d", d) for d in rows.values())  # today's stock and offers on every row
    out = await open_brief(h, out, "ac")
    check_floor(out)
    d = next(m for m in out if m.card and m.card.template == "brief").card.data
    n_ac = sum(1 for p in h.rt.facts.products_sync("ac") if p.qty > 0 or p.nearby_qty > 0)
    assert d["cat"] == "ACs" and d["stock"]["n"] == str(n_ac) and d["stock"]["tiles"]
    out = await open_brief(h, await h.tap("g:brief"), "fridge")  # another product
    d = next(m for m in out if m.card and m.card.template == "brief").card.data
    assert d["cat"] == "fridges"
    evs = [e.payload.get("dept") for e in await repo.events_for(types=["brief_shown"])]
    assert evs[-2:] == ["ac", "fridge"]
