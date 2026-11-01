"""The sales flow as agreed: one question at a time (product → why now → 4 home questions → photo → comfort range →
read it back → doubts → options → demo → which felt right → cost questions → full cost → payment → offer card →
a choice of two days, or 'not today' → the real reason). Fridge end to end, plus the side paths."""

from __future__ import annotations

from chanakya.config import ROOT
from chanakya.core import repo
from chanakya.i18n.lang import strings
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, pick, texts, to_options


def rows(msg):
    return [r.id for s in msg.sections for r in s.rows]


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


async def test_fridge_end_to_end(h):
    out = await h.tap("g:new")
    out = await h.pick("s:C1:cat:fridge")
    check_floor(out)
    assert button_ids(out) == ["s:C1:slot:trigger:replace", "s:C1:slot:trigger:new_home", "s:C1:slot:trigger:first"]
    for bid, nxt in (
        ("s:C1:slot:trigger:replace", "s:C1:slot:family_size:f1_2"),
        ("s:C1:slot:family_size:f3_4", "s:C1:slot:shopping:daily"),
        ("s:C1:slot:shopping:weekly", "s:C1:slot:space_width:w_lt65"),
        ("s:C1:slot:space_width:w_lt65", "s:C1:slot:power_cuts:yes"),
    ):
        out = await h.tap(bid)
        check_floor(out)
        assert len(out) == 1 and button_ids(out)[0] == nxt  # one question at a time, in order
    out = await h.tap("s:C1:slot:power_cuts:yes")
    assert button_ids(out) == ["s:C1:photo_yes", "s:C1:photo_no"] and "Ghar 4/4" in out[0].body
    out = await h.tap("s:C1:photo_no")
    r = rows(out[0])
    assert out[0].kind == "list" and r[-1] == "s:C1:budget:none" and "₹" in texts(out) + str(out[0].sections)
    out = await h.pick("s:C1:budget:none")
    assert out[0].card.template == "home" and button_ids(out) == ["s:C1:readback_ok", "s:C1:readback_edit"]
    await h.tap("s:C1:readback_ok")
    out = await h.tap("s:C1:doubt")
    assert "s:C1:myth:fr_stabiliser_needed" in rows(out[0])
    out = await h.tap("s:C1:options")
    check_floor(out)
    s = await session(h)
    for sku in s.option_labels.values():
        p = h.rt.svc.facts.product_sync(sku)
        assert p.category == "fridge" and p.spec("width_mm") <= 620  # never wider than the kitchen spot
    out = await h.tap("s:C1:demo")
    assert out[0].coach
    out = await h.tap("s:C1:shown")
    assert rows(out[0])[-1] == "s:C1:nonefit"
    out = await pick(h)
    k = next(m for m in out if m.card and m.card.template == "kharcha")
    assert k.card.data["lifetime_text"]  # the running cost of a fridge that runs all day
    out = await h.tap("s:C1:offers")
    assert button_ids(out)[0] == "s:C1:slot:exchange_old:yes_working"  # replacing → the exchange question
    await h.tap("s:C1:slot:exchange_old:yes_dead")
    out = await h.tap("s:C1:pay:upi")
    oc = out[0].card
    assert oc.template == "offer" and oc.data["total_str"].startswith("₹")
    ev = (await repo.events_for(s.id, ["offers_shown"]))[-1]
    assert ev.payload["pay"] == "upi" and ev.payload["bank"] is None  # no bank offers on UPI
    out = await h.tap("s:C1:nottoday")
    assert "s:C1:obj:ask_family" in rows(out[0])
    out = await h.pick("s:C1:obj:ask_family")
    assert button_ids(out)[0] == "s:C1:presale"
    out = await h.tap("s:C1:presale")
    assert "s:C1:qr" in button_ids(out)  # consent first, then the family card
    assert (await session(h)).extra["presale_parivaar"] is True


async def test_readback_change_something(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:trigger:new_home")
    out = await h.tap("s:C1:photo_no")
    out = await h.tap("s:C1:readback_edit")
    r = rows(out[0])
    assert "s:C1:slotask:seat_distance" in r and "s:C1:slotask:room" in r
    out = await h.pick("s:C1:slotask:seat_distance")
    assert button_ids(out)[0] == "s:C1:slot:seat_distance:d6_8"
    out = await h.tap("s:C1:slot:seat_distance:d6_8")  # the new answer → the read-back again, updated
    assert button_ids(out) == ["s:C1:readback_ok", "s:C1:readback_edit"] and "6-8 ft" in out[0].body


async def test_none_of_these_reranks(h):
    await to_options(h)
    before = set((await session(h)).option_labels.values())
    await h.tap("s:C1:shown")
    out = await h.pick("s:C1:nonefit")
    assert button_ids(out) == ["s:C1:rerank:lower", "s:C1:rerank:higher", "s:C1:rerank:brand"]
    out = await h.tap("s:C1:rerank:brand")
    check_floor(out)
    s = await session(h)
    brands = {h.rt.svc.facts.product_sync(x).brand for x in before}
    assert not brands & {h.rt.svc.facts.product_sync(x).brand for x in s.option_labels.values()}
    assert out[0].footer  # 'Updated: other brands'


async def test_remove_an_item_from_the_cost(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await pick(h)
    k = next(m for m in out if m.card and m.card.template == "kharcha")
    before = k.card.data["with_suggestions_total_str"]
    out = await h.tap("s:C1:cost_rm")
    r = rows(out[0])
    drop = next(x for x in r if x.startswith("s:C1:cost_drop:") and not x.endswith(("_optional", "_restore")))
    out = await h.pick(drop)
    k2 = next(m for m in out if m.card and m.card.template == "kharcha")
    assert k2.card.data["with_suggestions_total_str"] != before
    assert k2.card.data["zaroori_total_str"] == k.card.data["zaroori_total_str"]  # required charges never change


async def test_every_string_in_every_language():
    for key, d in strings().items():
        assert all(d.get(lg) for lg in ("hi", "mr", "en")), key


async def test_customer_language_from_the_menu(h):
    await to_options(h, clang=None)
    out = await h.say("menu")
    assert "s:C1:clang" in rows(out[0])
    out = await h.tap("s:C1:clang")
    assert button_ids(out) == ["s:C1:lang:en", "s:C1:lang:hi", "s:C1:lang:mr"]
    await h.tap("s:C1:lang:mr")
    assert (await session(h)).home.customer_lang == "mr"


def test_clips_for_every_step_and_language():
    for step in ("validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"):
        for lg in ("hi", "mr", "en"):
            f = ROOT / "data" / "clips" / f"{step}_{lg}.mp4"
            assert f.exists() and f.stat().st_size > 50_000, f.name
