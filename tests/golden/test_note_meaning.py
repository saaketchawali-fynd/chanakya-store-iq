"""User, 4 Nov 2026: a voice note is understood by its meaning. The TV they have now is never what gets suggested
("abhi Sony 43 hai, naya chahiye" must not shortlist that Sony 43), what they rule out is left out, needs said in
plain words ("papa ko dialogue sunai nahi deta", "Netflix bahut") pick the products through their own highlights,
and after the note only what it did not cover is asked. Then a demo guide per product, and the home compare."""

from __future__ import annotations

import pytest

from chanakya.ai import rules
from chanakya.brain.packs import load_packs
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.helpers import button_ids, check_floor, option_pages, options_view, texts
from tests.unit import test_jio as TJ

PACKS = load_packs()
BRANDS = ["Panasonic", "Sony", "Reconnect", "Samsung", "LG"]
SONY43 = "491297651"


@pytest.mark.parametrize("text,existing,wants,avoid,upgrade", [
    ("unke paas abhi Sony 43 inch hai, naya chahiye new features ke saath", {"brand": "Sony", "size_in": "43"}, {}, {}, True),
    ("purane 32 inch ki jagah 55 inch chahiye, Samsung nahi chahiye", {"size_in": "32"}, {"size_in": "55"},
     {"brand": "Samsung"}, False),
    ("जुना 32 इंच आहे, नवीन 50 इंच हवा", {"size_in": "32"}, {"size_in": "50"}, {}, True),
    ("they have a 5 year old LG 43, want something better", {"brand": "LG", "size_in": "43"}, {}, {}, True),
    ("customer ko 55 inch LG chahiye", {}, {"size_in": "55", "brand": "LG"}, {}, False),
])
def test_the_note_is_read_clause_by_clause(text, existing, wants, avoid, upgrade):
    u = rules.understand_text(text, PACKS, "tv", brands=BRANDS)
    assert {w.key: w.value for w in u.existing} == existing
    assert {w.key: w.value for w in u.wants} == wants  # what they have is never a want
    assert {w.key: w.value for w in u.avoid} == avoid
    assert u.upgrade is upgrade


def test_needs_said_in_plain_words_become_asks():
    u = rules.understand_text("papa ko dialogue sunai nahi deta, Netflix bahut dekhte hain", PACKS, "tv", brands=BRANDS)
    assert set(u.asks) >= {"sound", "smart"}


async def start(h):
    await jio_sync.sync(h.rt.settings, h.rt.facts)
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")


async def test_the_tv_they_have_is_never_the_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        await start(h)
        out = await h.say("unke paas abhi Sony 43 inch hai, naya chahiye new features ke saath, sofa 8 feet door hai")
        check_floor(out)
        s = await h.rt.svc.sessions.by_code(h.phone, "C1")
        assert s.home.existing == {"brand": "Sony", "size_in": 43} and s.home.upgrade
        assert not (s.extra.get("wants") or {})  # nothing of their old TV became a want
        assert s.option_labels, "the note shortlists at once"
        picked = [h.rt.facts.product_sync(x) for x in s.option_labels.values()]
        assert SONY43 not in s.option_labels.values()  # never the TV they already have
        assert all((p.spec("size_in") or 0) >= 43 for p in picked)  # an upgrade is never smaller
        card = options_view(out)
        assert all(o["points"] for o in card.data["options"])  # every option's page says why it fits them
        assert "Abhi: Sony 43" in texts(out) and "Naye features chahiye" in texts(out)  # the note said back
        assert set(button_ids(out)) >= {"s:C1:moreopts", "s:C1:demo"}  # happy with these? / more options
    finally:
        await h.close()


async def test_ruled_out_brand_and_needs_by_meaning(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    try:
        await start(h)
        out = await h.say("papa ko dialogue sunai nahi deta, Netflix bahut dekhte hain, sofa 8 feet, Reconnect nahi chahiye")
        check_floor(out)
        s = await h.rt.svc.sessions.by_code(h.phone, "C1")
        assert set(s.home.asks) >= {"sound", "smart"}
        assert "Reconnect" in (s.extra.get("excluded_brands") or [])
        brands = {h.rt.facts.product_sync(x).brand for x in s.option_labels.values()}
        assert "Reconnect" not in brands
        card = options_view(out)
        proofs = {pt.get("proof") for o in card.data["options"] for pt in o["points"] if isinstance(pt, dict)}
        assert proofs & {"ClearAudio+", "Chromecast", "20W", "Audio Booster Plus"}  # their needs, the products' own words
        # 'Tell me more': only what the note did not cover
        out = await h.tap("s:C1:more")
        check_floor(out)
        rows = [r.id for m in out for sec in (m.sections or []) for r in sec.rows]
        assert "s:C1:askq:seat_distance" not in rows and "s:C1:askq:elders" not in rows  # said / asked as a need
        assert "s:C1:ofs" in rows and "s:C1:nonefit" in rows
        q = next((r for r in rows if r.startswith("s:C1:askq:")), None)
        if q:  # answering one re-picks the products at once
            slot = q.split(":")[-1]
            out = await h.pick(q)
            first = next(b.id for m in out for b in m.buttons if f":slot:{slot}:" in b.id)
            out = await h.tap(first)
            assert options_view(out) is not None
        # the demo guide: per product, the wow first is their need
        out = await h.tap("s:C1:demo")
        check_floor(out)
        guide = out[0].card
        s = await h.rt.svc.sessions.by_code(h.phone, "C1")
        assert guide.template == "demo" and len(guide.data["products"]) == len(s.option_labels)  # one per product
        wow = guide.data["products"][0]["steps"][0]
        assert wow["tag"] == "wow" and wow["do"] and wow["proof"]
    finally:
        await h.close()


async def test_more_options_keeps_the_best_and_adds(h):
    from chanakya.sim.harness import PHONES

    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.voice("assess_1")  # the needs in one note → the best fits at once
    assert "s:C1:moreopts" in button_ids(out) and "Customer ko ye sahi lag rahe hain?" in out[-1].body
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    before = dict(s.option_labels)
    best_before = s.option_labels[next(k for k, r in s.extra["option_roles"].items() if r == "best")]
    out = await h.tap("s:C1:moreopts")  # more options: a follow-up the note did not cover
    check_floor(out)
    for _ in range(3):
        if options_view(out) is not None:
            break
        assert out[-1].body.startswith("*Follow-up")
        nxt = next(b.id for b in out[-1].buttons if ":slot:" in b.id or ":dq:" in b.id)
        rows = [r.id for m in out for sec in (m.sections or []) for r in sec.rows]
        out = await (h.pick(nxt) if nxt in rows else h.tap(nxt))
        check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert all(s.option_labels.get(k) == v for k, v in before.items())  # the earlier picks stay, same labels
    assert best_before in s.option_labels.values()
    added = [k for k, r in s.extra["option_roles"].items() if r in ("new", "broader")]
    assert added and len(s.option_labels) > len(before)
    assert set(option_pages(out)) == set(added)  # a page for each product that joined (the others were just shown)


async def test_side_by_side_without_a_room_photo(h):
    from tests.helpers import to_options

    await to_options(h)
    out = await h.tap("s:C1:cmpall")
    check_floor(out)
    card = next(m.card for m in out if m.card and m.card.template == "homecompare")
    d = card.data
    assert not d["in_room"] and len(d["cols"]) == 3 and all(c["photo"] for c in d["cols"])  # their product photos
    titles = [sec["title"] for sec in d["sections"]]
    assert titles[0] == "For your needs" and "Benefits for you" in titles and "Features" in titles
    assert titles[-1] == "Cost and offers"
    assert out[0].body.startswith("*Side-by-side*") and "s:C1:demo" in button_ids(out)
