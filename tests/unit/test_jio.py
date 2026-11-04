"""Live data from Jio Commerce (getProducts, getPromotionOffers), tested on real UAT responses recorded on
2 Nov 2026 (tests/fixtures/jio/products.json: 25 items from the Reliance Digital sales channel).

The rule under test: every product fact, price and offer comes from the backend; nothing is invented, and no
sample (seed) data is ever loaded in live mode."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient, category_of, is_protection
from chanakya.truth.jio_map import map_product, map_promotions, map_protection

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "jio"
ITEMS = json.loads((FIX / "products.json").read_text())
TODAY = date(2026, 11, 2)
# A promotion shaped like the doc's PromotionOffer object (test input; UAT returned no promotions on 2 Nov 2026)
PROMO = {"id": "P-1", "offer_text": "Flat 10% off", "promotion_type": "percentage", "promotion_name": "Diwali 10",
         "promotion_group": "product", "valid_till": "2026-11-30T23:59:59Z", "buy_rules": {}, "free_gift_items": [],
         "discount_rules": [{"discount_type": "percentage", "offer": {"discount_percentage": 10,
                                                                      "max_discount_amount": 3000}}],
         "description": "T&C apply"}


def item(name_part: str) -> dict:
    return next(i for i in ITEMS if name_part in i["name"])


def mapped() -> list[dict]:
    return [r for i in ITEMS if (c := category_of(i)) and (r := map_product(i, c))]


def test_categories_from_the_backend():
    assert category_of(item("Sony 108 cm (43 inch)")) == "tv"
    assert category_of(item("Panasonic 584 litres")) == "fridge"  # category "Side by side refrigerators"
    assert category_of(item("Samsung 393 L 3 Star")) == "fridge"  # category "Frost Free": name + fridge attributes
    assert category_of(item("Samsung 6.5 Kg Top Load")) == "wm"
    assert category_of(item("Reconnect RHSAB1506")) == "ac"
    plan = item("4 Year - resQ Care Plan")
    assert category_of(plan) is None and is_protection(plan)
    # backend category names seen in the live UAT catalogue that are NOT the product itself
    for cat_name, name, attrs in (("TV Speakers & Soundbars", "Sony HT-S20R 5.1ch Soundbar for TV", {}),
                                  ("Laptop Batteries", "HP Laptop Battery 3 Cell", {}),
                                  ("Laptop Utilities", "Laptop Cooling Stand", {}),
                                  ("Tablet PCs", "Lenovo Tab laptop-style keyboard", {"ram": "4 GB"}),
                                  ("DSLR Cameras", "Canon DSLR with TV out", {"display_type": "LCD"})):
        assert category_of({"categories": [{"name": cat_name}], "name": name, "attributes": attrs}) is None, cat_name
    cats = {}
    for r in mapped():
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    assert cats == {"tv": 5, "ac": 1, "wm": 5, "fridge": 5, "laptop": 5}


def test_product_facts_are_copied_not_invented():
    sony = map_product(item("Sony 108 cm (43 inch)"), "tv")
    assert sony["title"]["en"] == "Sony 108 cm (43 inch) Ultra HD 4K LED Smart TV, KD-43X7002E"  # verbatim
    assert (sony["price"], sony["mrp"]) == (45990, 68900)  # its own selling price (offer_price), not a ₹1,000 stand-in
    assert sony["specs"]["size_in"] == 43 and sony["specs"]["resolution"] == "4K"
    assert sony["info"]["source"] == "jio" and sony["info"]["slug"]
    fridge = map_product(item("Panasonic 584 litres"), "fridge")
    assert fridge["specs"]["capacity_l"] == 584 and fridge["specs"]["door"] == "side_by_side"
    assert fridge["dims"] == {"width_mm": 895, "height_mm": 1770, "depth_mm": 745}  # item-width/height/length (cm)
    old = map_product(item("PANASONIC 28 HD LED TV"), "tv")
    assert "size_in" not in old["specs"]  # "28" with no unit in the name: not taken as a size


def test_every_spec_traces_to_an_attribute_or_the_name():
    for it in ITEMS:
        cat = category_of(it)
        r = map_product(it, cat) if cat else None
        if not r:
            continue
        src = r["info"]["spec_source"]
        for key, value in {**r["specs"], **r["dims"]}.items():
            where = src[key]
            assert where, (r["sku"], key)
            if where == "name" and isinstance(value, (int, float)) and not isinstance(value, bool):
                assert re.search(rf"\b{re.escape(str(value))}\b", it["name"]), (it["name"], key, value)
            elif where.startswith("attributes."):
                keys = where.split(".", 1)[1].split("/")
                assert any(k.replace("*", "") in " ".join(it["attributes"]) for k in keys), (key, where)


def test_resq_plans_from_the_catalogue():
    plan = map_protection(item("4 Year - resQ Care Plan"), {"air conditioner": "ac"})
    assert plan["category"] == "ac" and plan["years"] == 4 and plan["price"] == 5899
    assert plan["filter"] == {"price_min": 20000.0, "price_max": 30000.0}  # "from Rs.20000 to Rs.30000" in its name
    assert map_protection(item("Extended Warranty Renewal - Air Conditioner"), {"air conditioner": "ac"}) is None


def test_promotions_only_with_stated_amounts():
    rows = map_promotions({"491297651": [PROMO]}, {"491297651": "tv"}, TODAY)
    assert len(rows) == 1
    o = rows[0]
    assert o["title"]["en"] == "Flat 10% off" and o["valid_to"] == date(2026, 11, 30)
    assert (o["pct"], o["max_discount"], o["flat"]) == (10.0, 3000, None) and o["filter"]["skus"] == ["491297651"]
    vague = dict(PROMO, id="P-2", discount_rules=[{"discount_type": "bogo"}])
    o2 = map_promotions({"x": [vague]}, {"x": "tv"}, TODAY)[0]
    assert o2["pct"] is None and o2["flat"] is None  # no amount stated → none used
    expired = dict(PROMO, id="P-3", valid_till="2026-10-01T00:00:00Z")
    assert map_promotions({"x": [expired]}, {"x": "tv"}, TODAY) == []


def backend(promos: dict[str, list] | None = None, fail: bool = False) -> httpx.MockTransport:
    """Serves the recorded items through the two documented endpoints, paged like UAT."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"].startswith("Bearer ")
        if fail:
            return httpx.Response(401, json={"error": "Invalid authorization token"})
        if req.url.path == "/service/application/catalog/v1.0/products":
            n, size = int(req.url.params["page_no"]), int(req.url.params["page_size"])
            chunk = ITEMS[(n - 1) * size: n * size]
            page = {"current": n, "type": "number", "item_total": len(ITEMS), "has_next": n * size < len(ITEMS)}
            return httpx.Response(200, json={"items": chunk, "page": page})
        if req.url.path == "/service/application/cart/v1.0/available-promotions":
            slug = req.url.params["slug"]
            return httpx.Response(200, json={"available_promotions": (promos or {}).get(slug, [])})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def jio_backend(monkeypatch):
    def use(promos=None, fail=False):
        monkeypatch.setattr(JioClient, "transport", backend(promos, fail))

    return use


async def live(tmp_path):
    from tests.conftest import make_harness

    return await make_harness(tmp_path, data_source="jio", jio_app_id="app-id", jio_app_token="app-token")


async def test_sync_loads_only_backend_data(tmp_path, jio_backend):
    sony_slug = item("Sony 108 cm (43 inch)")["slug"]
    jio_backend({sony_slug: [PROMO]})
    h = await live(tmp_path)
    try:
        assert h.rt.facts.snap.products == {}  # live mode starts empty: no sample data
        st = await jio_sync.sync(h.rt.settings, h.rt.facts)
        assert st["last_error"] is None and st["counts"]["products"] == 21
        snap = h.rt.facts.snap
        assert all((p.info or {}).get("source") == "jio" for p in snap.products.values())
        assert not snap.charges and not snap.finance and not snap.exchange  # no source in the documented API
        assert "tariff" not in snap.guides and "size_tv" in snap.guides  # the agent's own sizing rules only
        sony = h.rt.facts.product_sync("491297651")
        assert (sony.price, sony.mrp) == (45990, 68900) and not sony.stock_known and sony.in_stock
        assert [o.title["en"] for o in snap.offers] == ["Flat 10% off"]
        assert {pl.code for pl in snap.protection} == {"600562736", "600562710"}
        meta = await jio_sync.last_meta()
        assert meta["catalog"]["backend_categories"]["fridge"] == {"Frost Free": 3, "Side by side refrigerators": 2}
        assert meta["catalog"]["key_spec"]["tv"] == {"spec": "size_in", "attributes": 4, "name": 0, "missing": 1}
    finally:
        await h.close()


async def test_failed_sync_keeps_the_last_good_data(tmp_path, jio_backend):
    jio_backend()
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        n = len(h.rt.facts.snap.products)
        jio_backend(fail=True)
        st = await jio_sync.sync(h.rt.settings, h.rt.facts)
        assert "401" in st["last_error"] and len(h.rt.facts.snap.products) == n
    finally:
        await h.close()


async def test_no_token_means_no_data_never_samples(tmp_path):
    from tests.conftest import make_harness

    h = await make_harness(tmp_path, data_source="jio")
    try:
        st = await jio_sync.sync(h.rt.settings, h.rt.facts)
        assert st["last_error"] == "JIO_APP_ID / JIO_APP_TOKEN not set" and h.rt.facts.snap.products == {}
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        await h.voice("assess_1")
        await h.tap("s:C1:slot:trigger:new_home")
        await h.tap("s:C1:photo_no")
        await h.tap("s:C1:readback_ok")
        out = await h.tap("s:C1:options")
        assert "Jio Commerce" in out[0].body  # the honest reason, not "no model fits"
        out = await h.photo("label_meru55.jpg")
        assert "Jio Commerce" in out[0].body
    finally:
        await h.close()


async def test_live_mode_removes_sample_data_left_in_the_database(tmp_path):
    from chanakya.sim.harness import Harness
    from tests.conftest import db_url

    url = db_url(tmp_path)
    h = await Harness.create(url)  # sample data, as an older deploy would have left it
    assert h.rt.facts.snap.products
    await h.close()
    h = await Harness.create(url, drop=False, data_source="jio")
    try:
        assert h.rt.facts.snap.products == {} and not h.rt.facts.snap.offers
    finally:
        await h.close()


def test_price_is_the_products_own_selling_price_not_a_warehouse_stand_in():
    """User, 5 Nov 2026: the listing's price is a min–max over every store, zero-stock warehouses with stand-in
    prices included (a 43" Sony at ₹1,000 there, ₹45,990 at the store that sells it). The price is always one of the
    product's own values: its offer_price, else the range's end that is a real price; the MRP from the same end."""
    import json
    from pathlib import Path

    from chanakya.truth import jio_map

    items = json.loads(Path("tests/fixtures/jio/products.json").read_text())
    items = items.get("items", items) if isinstance(items, dict) else items
    by = {i["name"]: i for i in items}
    sony = next(i for n, i in by.items() if n.startswith("Sony 108 cm (43 inch)"))
    assert jio_map.prices(sony)[:2] == (45990, 68900)  # its offer_price, not the ₹1,000 stand-in
    for it in items:
        e, m = it["price"]["effective"], it["price"]["marked"]
        eff, mrp, _ = jio_map.prices(it)
        assert eff in {e["min"], e["max"], (it.get("_custom_json") or {}).get("offer_price")}  # never computed
        assert mrp in {m["min"], m["max"]}
        assert eff >= 0.25 * m["max"] or eff == e["max"]  # never a stand-in when a real price is there
