"""User, 4 Nov 2026: "Translate and Complete are not hitting". On live Jio data (no brightness, refresh or dialogue
specs; no charges, exchange or offers) both steps must still be real, personal and data-only:

- Translate (Saral Samjhao): the product's OWN highlights → what they mean for this customer's needs, quoted as
  proof; nothing is called unimportant unless today's stock proves it; at the choice, 'Made for your home'.
- Dikhao: option reasons and demo steps from real differences (never "bigger screen" for the same size).
- Complete: accessories Jio really sells (soundbar, wall mount …) suggested from the answers, protection as a
  choice with its per-day cost, the warranty and the install notes on the bill; no question that changes nothing.
- Close: no "card or UPI?" when no offer or EMI depends on it."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from chanakya.brain import highlights as HL
from chanakya.brain.packs import load_packs, set_slot
from chanakya.core.contracts import HomeCard
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient, category_of, companion_of
from chanakya.truth.jio_map import map_accessory, map_product
from tests.helpers import answer_complete, check_floor, options_view
from tests.unit import test_jio as TJ

PACKS = load_packs()
NOW = __import__("datetime").datetime(2026, 11, 2, 11, 0)


def product(name_part: str):
    it = TJ.item(name_part)
    r = map_product(it, category_of(it))
    return SimpleNamespace(sku=r["sku"], category=r["category"], brand=r["brand"], info=r["info"],
                           specs=r["specs"], spec=lambda k, d=None, s=r["specs"]: s.get(k, d))


def home(cat: str, **slots) -> HomeCard:
    h = HomeCard(category=cat)
    for k, v in slots.items():
        for one in (v if isinstance(v, list) else [v]):
            set_slot(h, PACKS.get(cat), k, one, None, "tap", NOW)
    return h


# ---------------------------------------------------------------- the highlights engine, on the recorded items
def test_highlights_quote_the_products_own_words_for_this_home():
    tv = PACKS.get("tv")
    sony, pana, reco = product("Sony 108 cm (43 inch)"), product("Panasonic 109.22 cm"), product("Reconnect 163.9")
    pts = HL.points(sony, home("tv", elders="yes"), {}, tv, general=False)
    assert pts and pts[0].theme == "sound" and pts[0].need == "elders" and pts[0].proof == "ClearAudio+"
    assert "dialogue" in pts[0].say["en"]  # the words say only what the highlight says (dialogue clarity)
    found = HL.found(pana)
    assert found["picture"].proof == "HDR10+" and found["smart"].proof == "Chromecast"
    assert HL.found(reco)["angle"].proof == "IPS panel"  # "Wireless display, IPS panel": the matching part only
    assert HL.points(reco, home("tv", seating="wide"), {}, tv, general=False)[0].theme == "angle"
    fridge = product("Samsung 670 liters")
    p = HL.points(fridge, home("fridge", power_cuts="yes"), {}, PACKS.get("fridge"), general=False)
    assert p[0].theme == "power" and p[0].proof == "Coolpack 12 Hours"
    lap = product("Asus Vivobook 16X")
    assert HL.points(lap, home("laptop", user="student"), {}, PACKS.get("laptop"), general=False)[0].theme == "office"
    say = HL.say(pts[0], "hi")
    assert "ClearAudio+" in say and "Ghar ke bado ke liye" in say  # Hindi in Roman Hinglish


def test_a_negative_highlight_is_never_a_benefit():
    p = SimpleNamespace(sku="L1", category="laptop", info={"features": [{"en": "Microsoft Office included: No"}]},
                        spec=lambda k, d=None: d)
    assert "office" not in HL.found(p)


def test_highlights_only_move_the_fit_where_no_spec_judges_it():
    tv = PACKS.get("tv")
    with_hdr, without = product("Panasonic 109.22 cm"), product("PANASONIC 28 HD")
    h = home("tv", content="movies")
    assert HL.contributions(with_hdr, h, tv) == []  # it shows the theme: left as it was
    assert HL.contributions(without, h, tv) == [("hl_picture", 1.5, 0.4)]  # it does not: counts against it


# ---------------------------------------------------------------- the live flow
async def walk_to_options(h, content="movies"):
    await jio_sync.sync(h.rt.settings, h.rt.facts)
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.pick("s:C1:cat:tv")
    for _ in range(12):
        ids = [b.id for m in out for b in m.buttons] + [r.id for m in out for s in (m.sections or []) for r in s.rows]
        if any(":slot:content:" in i for i in ids):
            nxt = f"s:C1:slot:content:{content}"
        elif "s:C1:budget:none" in ids:  # needs decide here, not a price band (bands come from the real prices)
            nxt = "s:C1:budget:none"
        else:
            nxt = next((i for i in ids if ":slot:" in i or ":dq:" in i or ":budget:" in i
                        or i.endswith(("photo_no", "readback_ok"))), None)
        if nxt is None:
            break
        rows = [r.id for m in out for s in (m.sections or []) for r in s.rows]
        out = await (h.pick(nxt) if nxt in rows else h.tap(nxt))
        check_floor(out)
    return await h.tap("s:C1:options")


async def test_live_translate_is_personal_and_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        await walk_to_options(h)
        sony = TJ.item("Sony 108 cm (43 inch)")["item_code"]
        out = await h.pick(f"s:C1:label:{sony}")
        check_floor(out)
        said = [out[0].body]
        for _ in range(3):
            nxt = next((b.id for m in out for b in m.buttons if ":bt:yes:" in b.id), None)
            if nxt is None:
                break
            out = await h.tap(nxt)
            check_floor(out)
            said.append(out[0].body)
        text = " ".join(said)
        assert "X-Reality PRO" in text and "movie" in text.lower()  # their need + the product's own highlight
        assert "won't change much" not in text and "no need to pay extra" not in text  # nothing called unimportant
    finally:
        await h.close()


async def test_live_options_demo_and_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        out = await walk_to_options(h)
        card = options_view(out)
        caps = {m.card.data["chip_label"]: m.body or m.caption for m in out if m.card and m.card.template == "product"}
        by = {o["label"]: o for o in card.data["options"]}
        for cap in caps.values():
            assert "Bigger screen" not in cap and "5 years" not in cap  # no fixed claim
        best = next(o for o in card.data["options"] if o["is_best"])
        assert any(pt.get("proof") == "HDR10+" for pt in best["points"])  # movies → the product's own HDR10+
        step = next(o for o in card.data["options"] if o["role"] == "step")
        assert "55 inch vs 43 inch" in caps[step["label"]]  # one step up: the real difference, from the data
        out = await h.tap("s:C1:demo")
        check_floor(out)
        s = await h.rt.svc.sessions.by_code("919000000001", "C1")
        plan = s.extra["demo_plan"]
        assert any(st["kind"] == "highlight" for st in plan) and len(plan) >= 2
        assert len(out) == 1 and out[0].card.template == "demo"  # the demo guide: one message, per product
        assert "1. *Wow:*" in out[0].body and "(HDR10+)" in out[0].body  # their need first, its proof from the data
        await h.tap("s:C1:shown")
        lab = next(k for k, v in by.items() if v["is_best"])
        out = await h.pick(f"s:C1:opt:{lab}")
        check_floor(out)
        made = out[0]
        assert made.card.template == "product" and made.card.data["picked"]
        assert len(made.card.data["made"]) >= 2 and all(x["text"] for x in made.card.data["made"])
        assert "Great choice" in made.caption
        assert out[-1].buttons[0].id == "s:C1:slot:mounting:wall"  # then the Complete questions, same reply
    finally:
        await h.close()


# ---------------------------------------------------------------- Complete with the accessories Jio sells
def accessory(name: str, cat: str, price: int, code: str, attrs: dict | None = None) -> dict:
    base = copy.deepcopy(TJ.item("Sony 108 cm (43 inch)"))
    base.update(name=name, item_code=code, uid=int(code), slug=f"acc-{code}", categories=[{"name": cat}],
                attributes=attrs or {}, brand={"name": name.split()[0]})
    base["price"] = {"effective": {"min": price, "max": price}, "marked": {"min": price + 500, "max": price + 500}}
    return base


ACCESSORIES = [
    accessory("Panasonic SC-HTB100 2.1 Soundbar 120 W", "TV Speakers & Soundbars", 700, "700001", {"hdmi_arc": "Yes"}),
    accessory("Reconnect Fixed TV Wall Mount for 32-55 inch TVs", "TV Accessories", 300, "700002"),
    accessory("Reconnect Tilt TV Wall Mount for 65-85 inch TVs", "TV Accessories", 350, "700003"),
]


def test_accessories_are_read_from_the_backend_names():
    assert [companion_of(a) for a in ACCESSORIES] == ["soundbar", "tv_mount", "tv_mount"]
    m = map_accessory(ACCESSORIES[1], "tv_mount")
    assert m["category"] == "tv_mount" and m["info"]["fits_in"] == [32.0, 55.0] and m["price"] == 300


async def test_live_complete_is_the_full_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(TJ, "ITEMS", TJ.ITEMS + ACCESSORIES)
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        out = await walk_to_options(h)
        card = options_view(out)
        lab = next(o["label"] for o in card.data["options"] if o["is_best"])
        meta = await jio_sync.last_meta()
        assert meta["counts"]["accessories"] == 3 and meta["catalog"]["accessories"]["tv_mount"]["with_size_range"] == 2
        await h.tap("s:C1:demo")
        await h.tap("s:C1:shown")
        await h.pick(f"s:C1:opt:{lab}")
        await h.tap("s:C1:slot:mounting:wall")
        out = await h.tap("s:C1:slot:wall_type:concrete")
        assert any("s:C1:slot:soundbar:" in b.id for m in out for b in m.buttons)  # a soundbar is on sale: ask
        out = await h.tap("s:C1:slot:soundbar:no")
        check_floor(out)
        ao = next(m.card for m in out if m.card and m.card.template == "addons")  # "Complete it", before the cost
        sug = {x["title"]: x for x in ao.data["items"]}
        assert "Panasonic SC-HTB100 2.1 Soundbar 120 W" in sug and "HDMI ARC" in sug[
            "Panasonic SC-HTB100 2.1 Soundbar 120 W"]["why"]  # the TV has ARC: one cable
        assert "Reconnect Fixed TV Wall Mount for 32-55 inch TVs" in sug  # fits 43 inch; the 65-85 one does not
        assert "Reconnect Tilt TV Wall Mount for 65-85 inch TVs" not in sug
        assert all(x["image"] for x in ao.data["items"] if "Wall Mount" in x["title"] or "Soundbar" in x["title"])
        out = await h.tap("s:C1:addons:all")
        k = next(m.card for m in out if m.card and m.card.template == "kharcha")
        added = {ln["title"] for ln in k.data["lines"] if ln["tag"] == "addon"}
        assert {"Panasonic SC-HTB100 2.1 Soundbar 120 W", "Reconnect Fixed TV Wall Mount for 32-55 inch TVs"} <= added
        assert k.data["zaroori_total_str"] == ao.data["total_str"] and k.data["mrp_text"].startswith("MRP")
        assert any(x.startswith("Warranty:") for x in k.data["included"])  # the backend's own warranty
        assert "Mounting: On the wall" in k.data["notes"] and "Wall type: Solid wall" in k.data["notes"]  # they count
    finally:
        await h.close()


async def test_live_no_soundbar_question_without_soundbars_and_no_pay_question(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        out = await walk_to_options(h)
        card = options_view(out)
        lab = next(o["label"] for o in card.data["options"] if o["is_best"])
        await h.tap("s:C1:demo")
        await h.tap("s:C1:shown")
        await h.pick(f"s:C1:opt:{lab}")
        await h.tap("s:C1:slot:mounting:wall")
        out = await h.tap("s:C1:slot:wall_type:concrete")
        assert not any("s:C1:slot:soundbar:" in b.id for m in out for b in m.buttons)
        out = await answer_complete(h, out)  # "Complete it" (a protection plan in the data), then the cost
        k = next(m.card for m in out if m.card)
        assert k.template == "kharcha"  # no soundbar on sale: the soundbar question would change nothing
        assert any("store confirms" in x for x in k.data["notes"])  # no mount in the data: said honestly
        out = await h.tap("s:C1:offers")
        check_floor(out)
        assert not any(":pay:" in b.id for m in out for b in m.buttons)  # no offer or EMI depends on payment
        offer = next(m.card for m in out if m.card)
        assert offer.template == "offer" and offer.data["pay_any"]
    finally:
        await h.close()


async def test_sample_bill_shows_protection_as_a_choice(h):
    from tests.helpers import to_options

    await to_options(h)
    await h.tap("s:C1:shown")
    out = await answer_complete(h, await h.pick("s:C1:opt:B"))
    k = out[-1].card
    assert k.template == "kharcha"
    pr = k.data.get("protection")
    if pr:  # a plan that is not already a suggestion line: price and per day, not in the totals
        assert pr["price_str"].startswith("₹") and "a day" in pr["note"]
    assert isinstance(json.dumps(k.data, default=str), str)


@pytest.mark.parametrize("lang", ["hi", "mr", "en"])
def test_every_theme_speaks_all_three_languages(lang):
    for cat in ("tv", "ac", "wm", "fridge", "laptop"):
        for th in HL.themes(cat):
            assert th.lines and all(li.say.get(lang) for li in th.lines), (cat, th.id)
            assert th.demo.get(lang), (cat, th.id)
            for n in th.needs:
                assert HL.need_label(n.id, lang), (cat, th.id, n.id)


def test_mount_and_bag_sizes_in_cm_and_attributes():
    from chanakya.truth.jio_map import _fits_inches, _size_text

    assert _fits_inches("Wall Mount (81.28 cm - 139.7 cm) Tilt") == [32.0, 55.0]  # Jio names sizes in cm
    assert _fits_inches("Universal TV Wall Mount up to 165.1 cm") == [0.0, 65.0]
    assert _fits_inches("Fixed Wall Mount") is None  # no size stated: never suggested as a fit
    assert _fits_inches(_size_text({"name": "Wall Mount", "attributes": {"suitable-screen-size": "32 - 55 Inches"}})) \
        == [32.0, 55.0]  # its own size attribute
    assert _fits_inches("Laptop Backpack 39.62 cm", single=True) == [0.0, 15.6]
