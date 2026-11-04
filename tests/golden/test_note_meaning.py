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
from tests.helpers import button_ids, check_floor
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
        card = next(m.card for m in out if m.card and m.card.template == "options")
        assert all(o["points"] for o in card.data["options"])  # every option says why it fits them
        assert "Abhi: Sony 43" in out[0].body and "Naye features chahiye" in out[0].body  # the note said back
        assert button_ids(out) == ["s:C1:demo", "s:C1:tryon", "s:C1:more"]
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
        card = next(m.card for m in out if m.card and m.card.template == "options")
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
            assert any(m.card and m.card.template == "options" for m in out)
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
