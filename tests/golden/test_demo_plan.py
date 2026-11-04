"""User, 4 Nov 2026: the demo follows the customer: what they asked for first, their needs compared with the real
options (both values from the data), then one more data-backed difference. And a scanned label shows its features,
price and a comparison at once."""

from __future__ import annotations

from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, to_options


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


async def test_the_demo_is_a_plan_built_from_this_customer(h):
    await to_options(h)
    out = await h.tap("s:C1:demo")
    check_floor(out)
    guide, m = out[0], out[-1]  # first the demo guide card (per product, wow first), then step 1 to run live
    assert guide.card.template == "demo" and guide.card.data["products"][0]["steps"][0]["tag"] == "wow"
    assert "Demo 1/" in m.body and m.footer and m.footer.startswith("Plan:")  # the order at a glance
    assert "s:C1:demo2" in button_ids(out)
    s = await session(h)
    plan = s.extra["demo_plan"]
    assert plan and plan[0]["kind"] in ("want", "recipe")
    # each step names the values on both models, from the data
    assert any(":" in part for part in m.body.split("·"))
    for _ in range(len(plan) - 1):
        out = await h.tap("s:C1:demo2")
        assert "Demo " in out[0].body
    out = await h.tap("s:C1:demo2")  # after the last step: which one felt right
    assert out[0].kind == "list"


async def test_what_they_asked_for_is_shown_first(h):
    await to_options(h)
    s = await session(h)
    best = s.option_labels[next(k for k, r in s.extra["option_roles"].items() if r == "best")]
    other = next(v for k, v in s.option_labels.items() if v != best)
    bp, op = h.rt.facts.product_sync(best), h.rt.facts.product_sync(other)
    key = next(k for k in ("panel", "resolution", "brand") if (bp.brand if k == "brand" else bp.spec(k))
               != (op.brand if k == "brand" else op.spec(k)))
    s.extra["wants"] = {key: bp.brand if key == "brand" else bp.spec(key)}
    s.extra.pop("demo_plan", None)
    await h.rt.svc.sessions.save(s)
    await h.tap("s:C1:demo")
    s = await session(h)
    first = s.extra["demo_plan"][0]
    assert first["kind"] == "want" and first["key"] == key  # what they asked for comes first


async def test_a_scanned_label_compares_at_once(h):
    await to_options(h)
    out = await h.photo("label_arka55.jpg")
    card = next(m.card for m in out if m.card)
    assert card.data["price_str"]  # its price, from the data
    out = await h.tap("s:C1:compare")
    cmp = next(m.card for m in out if m.card and m.card.template == "compare")
    assert cmp.data["rows"] and cmp.data.get("price_a") and cmp.data.get("price_b")
