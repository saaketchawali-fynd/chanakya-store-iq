"""The manager's huddle for any day, voice first (user, 6 Nov 2026: "When voice note asks for digest and any day r
date it should give a consolidated analysis of what happened, team members, status, demos etc; all from the data";
"voice first analytics quickly ... then text/options"; "show funnels, etc visually all backed from the data only").

Day 1 (Monday 2 Nov): C1 sold after the full cost, C2 a follow-up with the reason 'price feels high', C3 left open.
Day 2 (Tuesday 3 Nov): one customer, still on the floor. The manager then asks by voice and by text."""

from __future__ import annotations

from datetime import timedelta

from chanakya.core import clock, repo
from chanakya.sim.harness import PHONES
from tests.helpers import answer_complete, button_ids, check_floor, to_options


async def _options(h, code: str):
    await h.tap("g:new")
    await h.tap(f"s:{code}:lang:mr")
    await h.voice("assess_1")
    await h.tap(f"s:{code}:slot:trigger:new_home")
    await h.tap(f"s:{code}:photo_no")
    await h.tap(f"s:{code}:readback_ok")
    return await h.tap(f"s:{code}:options")


async def two_days(h):
    h.as_("associate")
    await to_options(h)  # C1: options → demo → B → the full cost → sold
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await answer_complete(h, await h.tap("s:C1:opt:B"))
    await h.tap("s:C1:khatam")
    out = await h.tap("s:C1:outcome:sold")
    pf = [i for i in button_ids(out) if ":pricefirst:" in i]
    if pf:
        await h.tap(pf[0].rsplit(":", 1)[0] + ":associate")
    clock.advance(timedelta(hours=1))
    await _options(h, "C2")  # C2: options → not today: the price → follow-up
    await h.tap("s:C2:nottoday")
    await h.pick("s:C2:obj:too_expensive")
    await h.tap("s:C2:khatam")
    out = await h.tap("s:C2:outcome:followup")
    pf = [i for i in button_ids(out) if ":pricefirst:" in i]
    if pf:
        await h.tap(pf[0].rsplit(":", 1)[0] + ":customer")
    clock.advance(timedelta(hours=1))
    await h.tap("g:new")  # C3: left open
    clock.advance(timedelta(days=1))  # Tuesday 3 Nov
    await h.tap("g:new")  # today's C1, on the floor
    h.as_("manager")


def card_of(out):
    return next(m.card for m in out if m.card is not None)


async def test_yesterday_by_voice_first_the_answer_then_the_card(h, monkeypatch):
    await two_days(h)

    async def heard(audio, mime=None):
        return "kal ka digest"

    monkeypatch.setattr(h.rt.ai, "transcribe", heard)
    out = await h.voice_bytes(b"OggS-manager")
    check_floor(out)
    quick, dash = out[0], out[1]
    assert quick.card is None and quick.kind == "text" and quick.footer == "Suna: “kal ka digest”"
    lines = quick.body.split("\n")  # the answer in words first: the funnel, how the day ended, the gap
    assert lines[0].startswith("*Kal:*") and "customer 3 → options 2 → demo 1 → kharcha 1 → bikri 1" in lines[0]
    assert lines[1] == "Follow-up 1 · Khula chhoda 1 · sabse zyada: Daam zyada laga"  # only what happened
    d = dash.card.data
    assert dash.card.template == "digest" and d["labels"]["title"] == "Kal ki sabha"
    assert [f["n"] for f in d["funnel"]] == ["3", "2", "1", "1", "1"]  # customers → options → demo → cost → sold
    assert {o["label"]: o["n"] for o in d["outcomes"]} == {"Bik gaya": "1", "Follow-up": "1", "Khula chhoda": "1"}
    assert d["why"] == "Daam zyada laga 1"
    assert [c["cust"].split()[0] for c in d["customers"]] == ["C1", "C2", "C3"]  # only Monday's customers
    assert d["customers"][0]["status"].startswith("Bik gaya · ") and d["customers"][0]["tone"] == "g"
    assert d["customers"][1]["status"] == "Follow-up · Daam zyada laga"
    ravi = next(t for t in d["team"] if t["name"] == "Ravi")
    assert (ravi["c"], ravi["d"], ravi["s"]) == ("3", "1", "1")
    assert {k["sub"] for k in d["kpis"] if k.get("sub")} == {"October: 6", "October: associate 6/6", "October: 2"}
    ids = button_ids(out)
    assert "m:digest:2026-11-01" in ids and "m:digest" in ids  # the day before, and back to today
    ev = (await repo.events_for(types=["manager_voice"]))[-1].payload
    assert ev["what"] == "digest" and ev["day"] == "2026-11-02" and ev["transcript"] == "kal ka digest"
    shown = (await repo.events_for(types=["digest_shown"]))[-1].payload
    assert (shown["day"], shown["customers"], shown["sold"], shown["followup"]) == ("2026-11-02", 3, 1, 1)


async def test_today_by_text_and_greeting(h):
    await two_days(h)
    out = await h.say("aaj kaisa raha")
    check_floor(out)
    d = card_of(out).data
    assert d["labels"]["title"] == "Aaj ki sabha" and [f["n"] for f in d["funnel"]][0] == "1"
    assert d["customers"][0]["status"] == "Floor par"
    assert "m:floor" in button_ids(out) and "m:digest:2026-11-02" in button_ids(out)
    out = await h.say("namaste")  # voice first: today's numbers at once, not a menu
    assert out[0].kind == "text" and card_of(out).template == "digest"
    out = await h.say("menu")  # the options when asked
    assert out[0].kind == "list"


async def test_a_date_a_weekday_an_empty_day_a_day_ahead(h):
    await two_days(h)
    out = await h.say("2 Nov ka funnel")
    assert card_of(out).data["labels"]["title"] == "Kal ki sabha"
    out = await h.say("Monday ka report")
    assert [f["n"] for f in card_of(out).data["funnel"]][0] == "3"
    out = await h.say("1 Nov ka digest")  # Sunday: nobody came
    check_floor(out)
    assert len(out) == 1 and out[0].card is None and "koi customer nahi tha" in out[0].body
    assert "m:digest:2026-10-31" in button_ids(out)
    out = await h.say("10 Dec ka digest")
    assert len(out) == 1 and "abhi aaya nahi" in out[0].body and button_ids(out) == ["m:digest"]
    out = await h.tap("m:digest:2026-11-02")  # the day buttons
    assert card_of(out).data["labels"]["title"] == "Kal ki sabha"


async def test_marathi(h):
    await two_days(h)
    await h.tap("g:alang:mr")
    out = await h.say("कालचा अहवाल")
    check_floor(out)
    d = card_of(out).data
    assert d["labels"]["title"] == "कालची सभा" and out[0].body.startswith("*काल:*")
    assert {o["label"] for o in d["outcomes"]} == {"विकलं", "फॉलो-अप", "उघडं राहिलं"}
    assert d["why"] == "किंमत जास्त वाटली 1"
    assert PHONES["manager"] == out[0].to
