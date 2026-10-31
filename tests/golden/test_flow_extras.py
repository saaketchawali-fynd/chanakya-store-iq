"""Spec-alignment extras: catalogue, data-grounded product answers, Close buttons, category question,
'B ke features', send-to-customer."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, texts


async def to_options(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:mr")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:mounting:wall")
    return await h.tap("s:C1:options")


async def test_catalog_prices_only_outside_conversation_or_after_demo(h):
    out = await h.say("catalog")
    check_floor(out)
    card = out[0].card
    assert card.template == "catalog" and card.data["items"]
    assert all(it["price_str"] and it["price_str"].startswith("₹") for it in card.data["items"])
    await to_options(h)
    out = await h.say("catalog")
    items = out[0].card.data["items"]
    assert all(it["price_str"] is None for it in items)  # price last: locked before the demo
    assert items[0]["title"] == "Arka 55 4K"  # the customer's picks first (A, B, C)
    await h.tap("s:C1:shown")
    out = await h.say("catalog")
    assert out[0].card.data["items"][0]["price_str"] == "₹38,990"


async def test_product_question_answered_from_data_not_expert(h):
    await to_options(h)
    out = await h.say("Meru 55 QLED Pro mein kitne HDMI port hain?")
    check_floor(out)
    assert out[0].body.startswith("📋 Meru 55 QLED Pro · HDMI ports: 3")
    assert not await repo.expert_q_open()
    out = await h.say("Option A mein Dolby Atmos hai kya?")
    assert "Dolby Atmos: Nahi" in out[0].body  # Arka 55 4K: dolby_atmos false in data
    ev = (await repo.events_for(None, ["fact_answered"]))[-1]
    assert ev.sources == ["spec:TV-ARK-55-4K60:dolby_atmos"]


async def test_close_buttons_and_send_to_customer(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    await h.tap("s:C1:opt:B")
    await h.tap("s:C1:offers")
    out = await h.tap("s:C1:bank:HDFC")
    assert button_ids(out) == ["s:C1:sendoffer", "s:C1:delivery", "s:C1:parivaar"]  # spec 12.7
    out = await h.tap("s:C1:sendoffer")  # no consent yet → consent first
    assert "s:C1:qr" in button_ids(out)
    await h.tap("s:C1:qr")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    h.as_("customer")
    await h.say(f"CHANAKYA {s.optin_token}")
    h.as_("associate")
    out = await h.tap("s:C1:sendoffer")
    check_floor(out)
    to_c = [m for m in out if m.to == PHONES["customer"]]
    assert to_c and "₹57,990" in to_c[0].body and "₹4,833" in to_c[0].body


async def test_category_question_when_product_unknown(h):
    await repo.staff_update(PHONES["associate"], dept="all")
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    out = await h.say("sofa se das feet hai, dopahar mein dhoop aati hai")
    assert button_ids(out) == ["s:C1:cat:tv", "s:C1:cat:ac", "s:C1:cat:wm"]
    out = await h.tap("s:C1:cat:tv")
    check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.category == "tv" and s.home.slots["seat_distance"] and s.home.slots["room_light"]


async def test_option_features_by_voice(h):
    await to_options(h)
    out = await h.say("B ke features batao")
    check_floor(out)
    assert out[0].coach and "120 Hz" in out[0].body
    ev = (await repo.events_for(None, ["label_read"]))[-1]
    assert ev.payload["sku"] == "TV-MER-55-QL120"
    assert "₹" not in texts(out)


async def test_old_tv_on_wall_asks_its_size_then_draws(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:mounting:wall")
    await h.tap("s:C1:photo")
    out = await h.photo("wall_with_tv.jpg")
    ask = next(m for m in out if m.kind == "list")
    rows = [r.id for sec in ask.sections for r in sec.rows]
    assert "s:C1:oldtv:32" in rows and "s:C1:oldtv:none" in rows
    assert not any(m.image_bytes for m in out)  # nothing drawn until the reference size is known
    out = await h.pick("s:C1:oldtv:32")
    check_floor(out)
    assert any(m.image_bytes for m in out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.slots["existing_tv_in"][-1].number == 32
    assert (await repo.events_for(s.id, ["tryon_sent"]))
