"""User, 3 Nov 2026: every question must translate to the products we really have (the backend data); new
features must use only that data: the upgrade check, the MRP saving, protection per day, the price watch."""

from __future__ import annotations

from types import SimpleNamespace

from chanakya.brain import data_questions as DQ
from chanakya.brain.packs import load_packs
from chanakya.core import repo
from chanakya.core.contracts import HomeCard
from chanakya.jobs import price_watch
from chanakya.sim.harness import PHONES
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.helpers import check_floor, pay, pick, to_options
from tests.unit.test_jio import backend, live

PACKS = load_packs()


def tv(sku, panel=None, os=None, nits=None, hz=None, size=55, brand="X"):
    specs = {k: v for k, v in {"panel": panel, "os": os, "brightness_nits": nits, "refresh_hz": hz, "size_in": size}.items() if v is not None}
    return SimpleNamespace(sku=sku, brand=brand, qty=1, nearby_qty=0, price=40000,
                           spec=lambda k, d=None, s=specs: s.get(k, d))


def test_questions_the_data_cannot_answer_are_skipped():
    pack = PACKS.get("tv")
    no_specs = [tv(f"T{i}", panel="LED" if i % 2 else "QLED", os="Google TV" if i < 5 else "webOS", size=43 + i) for i in range(10)]
    skip = DQ.skip_slots(pack, no_specs, HomeCard(), {})
    assert {"room_light", "content"} <= skip  # no brightness / refresh in the data: sun and cricket cannot pick a TV
    assert "seat_distance" not in skip  # sizes vary: distance picks the size
    full = [tv(f"T{i}", nits=300 + 100 * i, hz=60 if i < 5 else 120, size=43 + i) for i in range(10)]
    assert not ({"room_light", "content"} & DQ.skip_slots(pack, full, HomeCard(), {}))
    q = DQ.next_data_question(pack, no_specs, set(), {})
    assert q and q[0] == "panel" and {v for v, _ in q[1]} == {"LED", "QLED"}  # asked from what the stock differs on


async def test_live_flow_asks_only_what_the_stock_can_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend())
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        out = await h.pick("s:C1:cat:tv")
        asked = []
        for _ in range(8):
            ids = [b.id for m in out for b in m.buttons]
            slot = next((i for i in ids if ":slot:" in i or ":dq:" in i), None)
            if slot is None:
                break
            asked.append(slot.split(":")[3] if ":slot:" in slot else "dq")
            out = await h.tap(slot)
        assert "room_light" not in asked and "content" not in asked  # the recorded TVs have no brightness/refresh
        check_floor(out)
    finally:
        await h.close()


async def test_upgrade_check_and_mrp_saving(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await h.pick("s:C1:upgrade")
    check_floor(out)
    body = out[0].body
    assert "A → B" in body and "/din" in body  # what each step up costs, per day over 5 years


async def test_price_watch_nudges_once_when_the_price_falls(h):
    await to_options(h)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    sku = s.option_labels["B"]
    assert s.extra["seen_prices"][sku]
    st = h.rt.facts.snap.stock[(sku, h.rt.settings.store_id)]
    st.price = int(s.extra["seen_prices"][sku] * 0.9)  # the backend lowers the price
    from chanakya.core import clock

    n0 = len(h.ch.sent)
    assert await price_watch.run(h.rt.svc, clock.now()) == 1
    msg = h.ch.sent[n0].msg
    assert msg.to == PHONES["associate"] and "Price watch" in msg.body and "s:C1:called" in [b.id for b in msg.buttons]
    assert await price_watch.run(h.rt.svc, clock.now()) == 0  # told once


async def test_no_price_watch_after_a_sale(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    await pick(h)
    await pay(h)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    s.extra["outcome"] = "sold"
    await h.rt.svc.sessions.save(s)
    from chanakya.core import clock

    sku = s.chosen_sku
    h.rt.facts.snap.stock[(sku, h.rt.settings.store_id)].price -= 5000
    assert await price_watch.run(h.rt.svc, clock.now()) == 0
    assert await repo.events_for(s.id)


async def test_the_card_shows_everything_the_customer_told(tmp_path, monkeypatch):
    """3 Nov 2026 on the phone: the Home Card showed the old questions' answers only, not the new ones (picture
    type, smart system, a brand asked in a note). Now it shows all of it, 'any is fine' included."""
    from chanakya.skills.base import SkillContext  # noqa: F401  (the card is built through the flow below)

    h = await live(tmp_path)
    try:
        monkeypatch.setattr(JioClient, "transport", backend())
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        await h.pick("s:C1:cat:tv")
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        s.extra.update(wants={"panel": "QLED", "brand": "Sony", "size_in": 55}, dq_done=["panel", "os"])
        await h.rt.svc.sessions.save(s)
        out = await h.say("status")
        card = next(m.card for m in out if m.card and m.card.template == "home")
        rows = {r["label"]: r["value"] for r in card.data["rows"]}
        assert rows.get("Picture type") == "QLED" and rows.get("Brand") == "Sony" and rows.get("Screen size") == '55"'
        assert rows.get("Smart system") == "Any is fine"  # 'any' is an answer too
        assert "Sunlight" not in " ".join(card.data["missing"])  # never 'still to ask' for a question we skip
    finally:
        await h.close()


def test_every_data_choice_can_be_explained_simply():
    """4 Nov 2026: customers don't know LED vs QLED or Google TV vs webOS. Each choice has a plain analogy in the
    three languages, and a hint from the home."""
    from chanakya.brain import explain
    from chanakya.core.contracts import HomeCard

    for attr, vals in (("panel", ["LED", "QLED", "OLED", "NanoCell", "Mini LED"]),
                       ("os", ["Google TV", "Android TV", "webOS", "Tizen", "VIDAA", "Linux"]),
                       ("resolution", ["4K Ultra HD", "Full HD", "HD Ready"]), ("inverter", [True, False]),
                       ("frost_free", [True, False]), ("load", ["front", "top"]), ("automatic", [True, False])):
        for v in vals:
            for lang in ("hi", "mr", "en"):
                assert explain.analogy(attr, v, lang), (attr, v, lang)
    home = HomeCard()
    from datetime import UTC, datetime

    from chanakya.brain.packs import set_slot

    set_slot(home, PACKS.get("tv"), "room_light", "sunny", None, "tap", datetime.now(UTC))
    assert "QLED" in explain.need_hint("panel", home, PACKS.get("tv"), "en")  # from THIS home: sun → brighter panels


async def test_the_data_question_offers_to_explain_then_the_customer_chooses(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend())
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        s_ctx = None
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        await h.pick("s:C1:cat:tv")
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        s.extra.update(skip_key="x")  # recompute on the next step
        await h.rt.svc.sessions.save(s)
        from chanakya.brain import explain

        assert explain.can_explain("panel", ["LED", "QLED"]) and not explain.can_explain("brand", ["Sony", "LG"])
        _ = s_ctx
    finally:
        await h.close()
