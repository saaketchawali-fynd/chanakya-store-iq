"""Test labels (data/labels/<sku>.jpg, generated from the product database) → scan → the exact product, its
details from data, fit for this home once understood, and compare mode."""

from __future__ import annotations

import pytest
import yaml

from chanakya.ai.qr import decode_sku
from chanakya.config import ROOT
from tests.helpers import button_ids, check_floor

LABELS = ROOT / "data" / "labels"
SKUS = [p["sku"] for p in yaml.safe_load((ROOT / "data/seed/products.yaml").read_text())]


def test_every_label_decodes_to_its_own_product():
    for sku in SKUS:
        assert decode_sku((LABELS / f"{sku}.jpg").read_bytes()) == sku, sku
    assert (LABELS / "all_labels.pdf").exists()


@pytest.mark.parametrize("sku", ["TV-MER-55-QL120", "TV-NOV-65-ML120"])
async def test_scan_label_early_shows_product_from_data(h, sku):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.photo(LABELS / f"{sku}.jpg")
    check_floor(out)
    card = out[0].card
    p = h.rt.facts.product_sync(sku)
    assert card.template == "product" and card.data["title"].startswith(p.brand)
    assert card.data["key_specs"] and "badge" not in card.data  # fit only after the home is understood


async def test_scan_ac_label_in_ac_conversation_gives_fit(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    await h.voice("ac_2")  # 150 sq ft, no direct sun, 10 h/day, voltage issues
    await h.say("4 log hain, faisla khud karenge")
    out = await h.photo(LABELS / "AC-MER-15-5S.jpg")
    check_floor(out)
    d = out[0].card.data
    assert d["badge_kind"] in ("good", "partial") and d["fit_str"].endswith("%") and d["pros"]
    assert d["price_str"] is None  # price locked before the demo


async def test_compare_two_real_labels(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:mr")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:trigger:new_home")
    await h.photo(LABELS / "TV-MER-55-QL120.jpg")
    await h.tap("s:C1:compare")
    out = await h.photo(LABELS / "TV-ARK-55-4K60.jpg")
    check_floor(out)
    assert out[0].card.template == "compare"
    assert button_ids(out)[:2] == ["s:C1:tpick:TV-MER-55-QL120", "s:C1:tpick:TV-ARK-55-4K60"]
    assert "Meru 55 QLED Pro" in out[0].card.data["verdict"]
