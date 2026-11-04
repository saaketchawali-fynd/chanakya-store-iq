"""A full TV sale on live (Jio Commerce) data, from recorded UAT responses: every product, price and offer on the
way comes from the backend; where the backend has no data (stock counts, installation, EMI, bank offers,
delivery dates) nothing is made up."""

from __future__ import annotations

import pytest

from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.helpers import button_ids, check_floor, pick, texts
from tests.unit.test_jio import ITEMS, PROMO, backend, item, live

NAMES = {i["name"] for i in ITEMS}


@pytest.fixture
async def hj(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend({item("Panasonic 139.7 cm (55 inch)")["slug"]: [PROMO]}))
    h = await live(tmp_path)
    await jio_sync.sync(h.rt.settings, h.rt.facts)
    yield h
    await h.close()


async def to_options(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:trigger:new_home")
    await h.tap("s:C1:photo_no")
    await h.tap("s:C1:readback_ok")
    return await h.tap("s:C1:options")


async def test_options_are_backend_products_with_honest_roles(hj):
    out = await to_options(hj)
    check_floor(out)
    card = next(m.card for m in out if m.card and m.card.template == "options")
    opts = card.data["options"]
    assert all(o["title"] in NAMES for o in opts)  # the backend's own product names
    s = await hj.rt.svc.sessions.by_code(hj.phone, "C1")
    by_label = {lab: hj.rt.facts.product_sync(sku) for lab, sku in s.option_labels.items()}
    best = next(by_label[o["label"]] for o in opts if o["is_best"])
    for o in opts:
        p = by_label[o["label"]]
        if o["role"] == "value":
            assert p.price < best.price  # 'costs less' is true
        if o["role"] == "step":
            assert p.price > best.price
    assert "in store" not in texts(out) and "Delivery in" not in texts(out)  # no counts or days the API doesn't give


async def test_bill_offers_and_close_use_only_backend_data(hj):
    await to_options(hj)
    await hj.tap("s:C1:shown")
    out = await pick(hj)
    k = next(m.card for m in out if m.card and m.card.template == "kharcha")
    lines = k.data["lines"]
    assert [ln["tag"] for ln in lines] == ["product"]  # no installation or accessory charges: none in the backend
    s = await hj.rt.svc.sessions.by_code(hj.phone, "C1")
    p = hj.rt.facts.product_sync(s.chosen_sku)
    assert lines[0]["title"] in NAMES and k.data["zaroori_total_str"] == f"₹{p.price:,}"
    out = await hj.tap("s:C1:offers")
    assert "EMI" not in out[0].body  # no EMI plans in the backend → no EMI choice
    if s.chosen_sku == "491934634":  # the model with a live offer: how they pay can matter, so it is asked
        assert button_ids(out) == ["s:C1:pay:card", "s:C1:pay:upi"]
        out = await hj.tap("s:C1:pay:card")  # no bank offers → straight to the offer card, no empty bank list
    else:  # no offer and no EMI depend on the payment: not asked, the price straight away
        assert not any(":pay:" in b for b in button_ids(out))
    oc = next(m.card for m in out if m.card and m.card.template == "offer")
    assert oc.data["price_str"] == f"₹{p.price:,}"
    out = await hj.tap("s:C1:delivery")
    assert "store will confirm" in out[0].body  # a preferred day, not a promised delivery date


async def test_backend_promotion_reaches_the_offer_card(hj):
    await to_options(hj)
    s = await hj.rt.svc.sessions.by_code(hj.phone, "C1")
    label = next(lab for lab, sku in s.option_labels.items() if sku == "491934634")  # the 55" with the promotion
    await hj.tap("s:C1:shown")
    await pick(hj, label)
    await hj.tap("s:C1:offers")
    out = await hj.tap("s:C1:pay:upi")
    oc = next(m.card for m in out if m.card and m.card.template == "offer")
    p = hj.rt.facts.product_sync("491934634")
    assert "Flat 10% off" in str(oc.data)
    assert oc.data["total_str"] == f"₹{p.price - round(p.price * 0.10):,}"  # 10% as stated, under the ₹3,000 cap
