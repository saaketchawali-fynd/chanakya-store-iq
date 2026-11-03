"""Chanakya Live (docs/live-mode-plan.md): the conversation fills the Home Card, A / B / C come from today's stock,
ONE cue at a time, objections get the pack's line in the customer's language, and a model's report is validated."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chanakya.live import routes
from chanakya.live.engine import Floor
from chanakya.truth import fmt

TALK = ["TV dekhna hai, naya ghar liya hai", "hall mein lagega, sofa se das feet door", "dhoop bahut aati hai, papa cricket dekhte hain"]


def test_conversation_fills_the_home_and_picks_options(rt):
    f = Floor(rt, lang="hi")
    f.absorb("namaste ji")
    assert f.view()["cue"]["id"] == "product"  # first: what are they looking for
    f.absorb(TALK[0])
    v = f.view()
    assert v["category"] == "tv" and v["cue"]["kind"] == "ask" and not v["options"]
    for line in TALK[1:]:
        f.absorb(line)
    v = f.view()
    assert {"room", "seat_distance", "room_light"} <= {n["slot"] for n in v["needs"]}
    assert [o["label"] for o in v["options"]][:2] == ["A", "B"]
    prods = {p.sku: p for p in rt.facts.products_sync("tv")}
    for o in v["options"]:  # every price is the stock row's price
        assert o["price"] == fmt.inr(prods[o["sku"]].price)
        assert o["reasons"] and (o["offer"] is None or o["offer"]["till"])
    assert v["cue"]["id"] == "options_ready"


def test_wants_come_first_and_are_ticked(rt):
    f = Floor(rt, lang="en")
    for line in TALK + ["55 inch chahiye"]:
        f.absorb(line)
    v = f.view()
    assert any(w["key"] == "size_in" for w in v["wants"])
    assert all("55 inch as asked" in o["reasons"] for o in v["options"])


def test_one_cue_and_objection_in_customer_language(rt):
    f = Floor(rt, lang="hi")
    f.home.customer_lang = "mr"
    f.absorb(TALK[0])
    first = f.view()["cue"]["id"]
    f.absorb("hmm theek hai")  # nothing new: the same cue stays, never a list
    assert f.view()["cue"]["id"] == first
    f.absorb("online sasta hai")
    c = f.view()["cue"]
    assert c["kind"] == "say" and "ऑनलाइन" in c["text"]
    f.done_cue()
    assert f.view()["cue"] is None or f.view()["cue"]["id"] != c["id"]
    f.absorb("online sasta hai")  # never the same cue twice for one customer
    assert f.view()["cue"] is None or f.view()["cue"]["id"] != c["id"]


def test_model_report_is_validated_against_the_pack(rt):
    f = Floor(rt, lang="en")
    f.absorb("TV chahiye")
    bad = f.heard_tool({"slots": [{"slot": "room_light", "value": "very very bright"}, {"slot": "made_up", "value": "x"}]})
    assert not bad["accepted"] and "room_light" not in f.home.slots
    ok = f.heard_tool({"slots": [{"slot": "room_light", "value": "sunny"}], "numbers": [{"name": "seat_distance_ft", "value": 9}]})
    assert ok["accepted"] and {"room_light", "seat_distance"} <= set(f.home.slots)


def test_budget_gap_is_whispered(rt):
    f = Floor(rt, lang="en")
    for line in TALK + ["budget 15000 tak"]:
        f.absorb(line)
    assert "budget_gap" in f.shown


def _client(rt, **kw):
    for k, v in {"live_enabled": True, "live_key": "", "live_provider": "browser", **kw}.items():
        object.__setattr__(rt.settings, k, v)
    app = FastAPI()
    app.state.rt = rt
    app.include_router(routes.router)
    return TestClient(app)


def test_socket_off_by_default_and_key(rt):
    c = _client(rt, live_enabled=False)
    assert c.get("/live").status_code == 404
    c = _client(rt, live_key="s3cret")
    assert c.get("/live").status_code == 404 and c.get("/live?k=s3cret").status_code == 200
    _client(rt)


def test_socket_browser_mode_end_to_end(rt):
    c = _client(rt)
    with c.websocket_connect("/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "lang": "hi", "clang": "hi"}))
        assert json.loads(ws.receive_text()) == {"type": "provider", "name": "browser", "rate": 16000}
        assert json.loads(ws.receive_text())["type"] == "view"
        for line in TALK:
            ws.send_text(json.dumps({"type": "text", "text": line}))
            v = json.loads(ws.receive_text())
        assert v["category"] == "tv" and v["options"] and v["cue"]
        ws.send_text(json.dumps({"type": "reset"}))
        assert json.loads(ws.receive_text())["needs"] == []
