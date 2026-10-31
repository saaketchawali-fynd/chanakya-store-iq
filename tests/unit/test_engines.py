"""Hisaab, offers, finance, fit, options, next question, demos — golden numbers from the spec."""

from datetime import UTC, date, datetime

import pytest

from chanakya.brain import packs as P
from chanakya.brain.demos import pick_demo
from chanakya.brain.fit import fit
from chanakya.brain.next_question import next_slot, score_slots
from chanakya.brain.options import compute_options
from chanakya.core.contracts import HomeCard
from chanakya.truth import finance, hisaab, offers
from chanakya.truth.firewall import NumberRegistry

NOW = datetime.now(UTC)


def shah(rt, **extra) -> HomeCard:
    pack = rt.packs.get("tv")
    h = HomeCard(
        category="tv",
        customer_name="Shah ji",
        customer_lang="mr",
        start_level_inr=50000,
        start_level_source="associate_voice",
    )
    P.set_slot(h, pack, "seat_distance", "num", 10, "voice", NOW)
    P.set_slot(h, pack, "room_light", "sunny", None, "voice", NOW)
    P.set_slot(h, pack, "content", "sports", None, "voice", NOW)
    P.set_slot(h, pack, "content", "kids", None, "voice", NOW)
    for k, v in extra.items():
        P.set_slot(h, pack, k, v, None, "tap", NOW)
    return h


def joshi(rt) -> HomeCard:
    pack = rt.packs.get("ac")
    h = HomeCard(category="ac", customer_name="Joshi ji")
    P.set_slot(h, pack, "room_size", "num", 150, "voice", NOW)
    P.set_slot(h, pack, "pipe_run_ft", "num", 15, "voice", NOW)
    P.set_slot(h, pack, "hours_per_day", "num", 10, "voice", NOW)
    for k, v in {"sun_exposure": "yes", "power_issues": "yes", "outdoor_spot": "wall", "wall_hole": "needed"}.items():
        P.set_slot(h, pack, k, v, None, "voice", NOW)
    return h


def test_seat_distance_band(rt):
    h = shah(rt)
    assert P.value(h, "seat_distance") == "d8_10" and P.number(h, "seat_distance") == 10


async def test_hisaab_tv_golden(rt):
    reg = NumberRegistry()
    cs = await hisaab.compute("TV-MER-55-QL120", shah(rt, mounting="wall"), rt.facts, rt.packs.get("tv"), reg, "mr")
    assert cs.zaroori_total == 65288 and cs.with_suggestions_total == 68787
    assert cs.zaroori_total_str == "₹65,288"
    codes = [ln.code for ln in cs.lines]
    assert codes == ["TV-MER-55-QL120", "TV_WALLMOUNT_S", "TV_INSTALL_WALL", "TV_EW2_S"]
    assert "price:TV-MER-55-QL120@RD-JUHU" in cs.sources and "charge:TV_WALLMOUNT_S" in cs.sources
    assert cs.optional_count == 1  # HDMI hidden by default


async def test_hisaab_ac_golden(rt):
    reg = NumberRegistry()
    cs = await hisaab.compute("AC-MER-15-5S", joshi(rt), rt.facts, rt.packs.get("ac"), reg, "hi")
    assert cs.zaroori_total == 48240
    assert cs.with_suggestions_total == 54539
    pipe = next(ln for ln in cs.lines if ln.code == "AC_PIPE_FT")
    assert pipe.qty == 5 and pipe.amount == 2250
    assert cs.lifetime and cs.lifetime["monthly"] == round(820 * 9.5 / 12)


async def test_offers_hdfc_cap_and_exchange(rt):
    reg = NumberRegistry()
    r = await offers.best_stack("TV-MER-55-QL120", shah(rt), rt.facts, reg, date(2026, 11, 2), bank="HDFC", emi=True)
    assert [a.id for a in r.applied] == ["OFF-HDFC-10"] and r.applied[0].amount == 5000 and r.payable == 57990
    r2 = await offers.best_stack(
        "TV-MER-55-QL120", shah(rt, exchange_old="yes_working"), rt.facts, reg, date(2026, 11, 2), bank="HDFC"
    )
    assert {a.id for a in r2.applied} == {"OFF-HDFC-10", "OFF-EXCH-TV"} and r2.payable == 55990


async def test_offers_sbi_expiry(rt):
    reg = NumberRegistry()
    ok = await offers.best_stack("TV-MER-55-QL120", shah(rt), rt.facts, reg, date(2026, 11, 10), bank="SBI")
    gone = await offers.best_stack("TV-MER-55-QL120", shah(rt), rt.facts, reg, date(2026, 11, 11), bank="SBI")
    assert [a.id for a in ok.applied] == ["OFF-SBI-7"] and ok.applied[0].amount == 4000
    assert gone.applied == []


async def test_finance_golden(rt):
    reg = NumberRegistry()
    best, alt = await finance.best_plan("tv", 57990, "HDFC", rt.facts, reg)
    assert best.plan_id == "FIN-NCE-12"
    assert (best.monthly, best.fee, best.total, best.extra) == (4833, 299, 58295, 305)
    assert alt.plan_id == "FIN-NCE-6"


def test_finance_standard_formula(rt):
    plan = next(p for p in rt.facts.snap.finance if p.id == "FIN-STD-18")
    q = finance.quote(plan, 50000)
    r = 15 / 1200
    import math

    assert q.monthly == math.ceil(50000 * r * (1 + r) ** 18 / ((1 + r) ** 18 - 1))
    assert q.fee == 500 and q.total == q.monthly * 18 + 500 and q.extra == q.total - 50000


def test_fit_golden(rt):
    pack, g = rt.packs.get("tv"), rt.facts.snap.guides
    h = shah(rt)
    f = {p.sku: fit(p, h, pack, g).score for p in rt.facts.products_sync("tv")}
    assert f["TV-MER-55-QL120"] == 100
    assert f["TV-ARK-55-4K60"] == f["TV-MER-55-QL60"] == f["TV-ARK-65-4K60"] == 63
    assert f["TV-ARK-50-4K60"] == 41 and f["TV-ARK-43-4K60"] == 20


def test_options_golden(rt):
    o = compute_options(rt.facts.products_sync("tv"), shah(rt), rt.packs.get("tv"), rt.facts.snap.guides)
    assert {x.label: x.product.sku for x in o.options} == {
        "A": "TV-ARK-55-4K60",
        "B": "TV-MER-55-QL120",
        "C": "TV-MER-65-QL120",
    }


def test_options_out_of_stock_uses_nearby(rt):
    h = HomeCard(category="tv", start_level_inr=90000)
    pack = rt.packs.get("tv")
    P.set_slot(h, pack, "seat_distance", "num", 9, "voice", NOW)
    P.set_slot(h, pack, "room_light", "dim", None, "voice", NOW)
    P.set_slot(h, pack, "content", "movies", None, "voice", NOW)
    o = compute_options(rt.facts.products_sync("tv"), h, pack, rt.facts.snap.guides)
    skus = [x.product.sku for x in o.options]
    assert "TV-NOV-55-OL120" in skus  # 0 in store, 2 nearby → kept
    p = rt.facts.product_sync("TV-NOV-55-OL120")
    assert p.qty == 0 and p.nearby_qty == 2 and p.delivery_days == 2


def test_next_question_golden(rt):
    pack, g = rt.packs.get("tv"), rt.facts.snap.guides
    prods = rt.facts.products_sync("tv")
    h = shah(rt)
    ranked = dict(score_slots(h, pack, prods, g, 0, False))
    assert next_slot(h, pack, prods, g, 0) == "mounting"
    assert ranked["mounting"] == pytest.approx(0.65)
    assert ranked["elders"] == pytest.approx(0.633, abs=0.001) and ranked["seating"] == pytest.approx(0.633, abs=0.001)
    h2 = shah(rt, mounting="wall")
    assert next_slot(h2, pack, prods, g, 1) == "elders"


def test_demo_golden(rt):
    pack, g = rt.packs.get("tv"), rt.facts.snap.guides
    h = shah(rt)
    o = compute_options(rt.facts.products_sync("tv"), h, pack, g)
    d, other, _ = pick_demo(o, h, pack, g)
    assert d.id == "glare_test" and other == "A"
