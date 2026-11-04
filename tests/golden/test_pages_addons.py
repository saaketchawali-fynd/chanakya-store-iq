"""User, 5 Nov 2026:
- every best-fit product gets its own product page (like an e-commerce product page: benefits for their needs
  first, then the rest), not one cramped card;
- after the choice, smart add-ons that go with the product and their needs, with the best offers from the data on
  the combos or individually ("Complete it"), before the full cost;
- the demo guide in the associate's own language; Hindi in Roman Hinglish everywhere, never Devanagari."""

from __future__ import annotations

import re
from datetime import date

from chanakya.sim.harness import PHONES
from chanakya.truth import offers as O
from tests.helpers import button_ids, check_floor, to_options

DEV = re.compile(r"[ऀ-ॿ]")


async def test_one_product_page_per_option(h):
    out = await to_options(h)
    check_floor(out)
    pages = [m for m in out if m.card and m.card.template == "product"]
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert len(pages) == len(s.option_labels) >= 2 and not any(m.card.template == "options" for m in out if m.card)
    for m in pages:
        d = m.card.data
        assert d["title"] and d["brand"] and d["image"] and d["key_specs"] and d["chip_label"] in s.option_labels
        assert d["needs"], d  # what it means for THIS home comes first
        assert all(x.get("text") and x.get("icon") for x in d["needs"])
        assert not d.get("price_str")  # the price stays after the demo
        # the reference design: four rows (their needs first), three tiles with a photo, a title and its own specs
        assert 1 <= len(d["rows"]) <= 4 and d["rows"][0]["text"].startswith(d["needs"][0]["text"])
        assert len(d["tiles"]) == 3 and all(x["title"] and x["caption"] and x["image"] for x in d["tiles"])
        assert "Offer:" in (m.body or m.caption)  # the offer the card no longer shows, in its caption
    best = next(m for m in pages if m.card.data["chip_kind"] == "best")
    assert best.card.data["chip"] == "Best fit for your home"
    assert (pages[0].body or pages[0].caption).startswith("*")  # the heading rides on the first page
    assert out[-1].buttons and not out[-1].card  # the next steps last, so the buttons sit at the bottom
    assert "s:C1:cmpall" in button_ids(out)


async def _to_choice(h, soundbar: str):
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await h.pick("s:C1:opt:B")  # Meru 55" QLED Pro: a QLED TV of 55 inch, the seed's soundbar bundle applies
    await h.tap("s:C1:slot:mounting:wall")
    await h.tap("s:C1:slot:wall_type:concrete")
    return await h.tap(f"s:C1:slot:soundbar:{soundbar}")


async def test_complete_it_with_the_bundle_offer(h):
    out = await _to_choice(h, "no")
    check_floor(out)
    m = next(m for m in out if m.card and m.card.template == "addons")
    d = m.card.data
    titles = [x["title"] for x in d["items"]]
    sb = next(x for x in d["items"] if "Soundbar" in x["title"])
    assert sb["combo"].startswith("₹3,000 off a soundbar with the TV")  # the data's own bundle rule, this TV
    assert sb["why"] and sb["image"]  # why it is for them, and its photo
    assert d["total_str"] and d["product"]["price_str"]
    assert button_ids(out) == ["s:C1:addons:all", "s:C1:addons:pick", "s:C1:addons:none"]
    assert "*Karo*" in m.body or "*Do*" in m.body
    out = await h.tap("s:C1:addons:all")
    k = next(m for m in out if m.card and m.card.template == "kharcha")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    lines = {ln["code"]: ln for ln in s.cost_snapshot["lines"]}
    assert lines["SB-MER-21"]["kind"] == "addon" and lines["SB-MER-21"]["amount"] == 12990 - 3000
    assert "bundle offer" in lines["SB-MER-21"]["reason"]
    assert d["total_str"] == s.cost_snapshot["zaroori_total_str"]  # the card said the same total as the bill
    assert not [ln for ln in s.cost_snapshot["lines"] if ln["kind"] == "sujhav" and ln["code"] in
                [x["code"] for x in s.extra["addons_offer"]["rows"]]]
    assert k.card.data["labels"]["total_note"]
    assert titles


async def test_complete_it_one_at_a_time_and_not_now(h):
    out = await _to_choice(h, "no")
    rows = [r.id for m in (await h.tap("s:C1:addons:pick")) for sec in m.sections for r in sec.rows]
    assert "s:C1:addon:SB-MER-21" in rows and rows[-1] == "s:C1:addons:done"
    out = await h.pick("s:C1:addon:SB-MER-21")
    left = [r.id for m in out for sec in m.sections for r in sec.rows]
    assert "s:C1:addon:SB-MER-21" not in left and "Jod diya: Meru Soundbar 2.1" in out[0].body
    out = await h.pick("s:C1:addons:done")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    kinds = {ln["code"]: ln["kind"] for ln in s.cost_snapshot["lines"]}
    assert kinds.get("SB-MER-21") == "addon" and all(k != "sujhav" for k in kinds.values())
    assert any(m.card and m.card.template == "kharcha" for m in out)


async def test_not_now_suggests_nothing_again(h):
    await _to_choice(h, "no")
    out = await h.tap("s:C1:addons:none")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert not [ln for ln in s.cost_snapshot["lines"] if ln["kind"] in ("addon", "sujhav")]
    assert any(m.card and m.card.template == "kharcha" for m in out)


def _p(**kw):
    class P:
        def __init__(self, **k):
            self.__dict__.update(k)

        def spec(self, key):
            return self.specs.get(key)

    kw.setdefault("specs", {})
    kw.setdefault("brand", "X")
    return P(**kw)


def _offer(**kw):
    from chanakya.truth.models import Offer

    base = dict(id="T", kind="instant", title={"en": "t"}, stack_group="g", valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31), categories=["tv"], filter={}, requires={})
    base.update(kw)
    return Offer(**base)


def test_bundle_rule_names_the_tv():
    from chanakya.core.contracts import HomeCard

    bundle = _offer(id="B", kind="bundle", flat=3000, categories=["soundbar"],
                    filter={"with_tv_panel_in": ["QLED"], "with_tv_size_min": 55})
    sb = _p(sku="SB", category="soundbar", price=12990)
    qled55 = _p(sku="TV1", category="tv", price=60000, specs={"panel": "QLED", "size_in": 55})
    led43 = _p(sku="TV2", category="tv", price=30000, specs={"panel": "LED", "size_in": 43})
    today, home = date(2026, 11, 5), HomeCard()
    assert [c["amount"] for c in O.combo_offers([bundle], qled55, [sb], home, today)] == [3000]
    assert O.combo_offers([bundle], led43, [sb], home, today) == []  # the rule does not name this TV: no claim
    assert not O.eligible(bundle, sb, home, None, False, today)  # never on its own


def test_a_bill_offer_counts_only_its_own_categories():
    from chanakya.core.contracts import HomeCard

    bill = _offer(id="M", pct=10, max_discount=5000, min_amount=30000, categories=["tv", "soundbar"])
    tv_only = _offer(id="N", pct=10, max_discount=5000, min_amount=30000, categories=["tv"], stack_group="h")
    tv = _p(sku="TV", category="tv", price=27990, specs={})
    sb = _p(sku="SB", category="soundbar", price=5990)
    today, home = date(2026, 11, 5), HomeCard()
    got = O.combo_offers([bill, tv_only], tv, [sb], home, today)
    assert [(c["offer"].id, c["kind"], c["amount"]) for c in got] == [("M", "bill", 3398)]  # 10% of 33,980


def test_jio_gallery_and_brand_logo():
    import json
    from pathlib import Path

    from chanakya.truth import jio_map

    items = json.loads(Path("tests/fixtures/jio/products.json").read_text())
    items = items.get("items", items) if isinstance(items, dict) else items
    rows = [r for r in (jio_map.map_product(it, "tv") for it in items) if r]
    assert any(len(r["info"]["gallery"]) >= 2 for r in rows) and all(r["info"]["brand_logo"] for r in rows)
    assert not any(jio_map.ENERGY_LABEL.search(u) for r in rows for u in r["info"]["gallery"])


async def test_hindi_is_roman_and_the_demo_guide_is_theirs(h):
    out = await to_options(h, clang="hi")
    out += await h.tap("s:C1:demo")
    guide = next(m for m in out if m.card and m.card.template == "demo")
    assert guide.card.lang == "hi"  # the associate's own script, in their language
    for m in out:
        text = (m.body or m.caption or "") + " " + " ".join(b.title for b in m.buttons)
        assert not DEV.search(text), text  # Hindi in Roman Hinglish, the lines to say included
