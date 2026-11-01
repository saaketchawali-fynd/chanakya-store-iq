"""AC acceptance path (site questions BEFORE the cost; 48,240 / 54,539; install job with risk flags) and
smoke paths for washing machine and laptop using the offline rules understanding."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor


async def test_ac_path(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    out = await h.voice("ac_2")
    check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.category == "ac"
    await h.tap("s:C1:slot:people:p3_4") if "s:C1:slot:people:p3_4" in button_ids(out) else None
    out = await h.tap("s:C1:options")
    check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.option_labels["B"] == "AC-MER-15-5S", s.option_labels
    await h.tap("s:C1:shown")
    out = await h.tap("s:C1:opt:B")
    asked = []
    for _ in range(4):  # the AC story: outdoor spot, pipe run, wall hole are asked before the cost
        q = out[0]
        if q.card:
            break
        ids = button_ids(out)
        slot = ids[0].split(":")[3]
        asked.append(slot)
        pick = {"outdoor_spot": "wall", "pipe_run_ft": "p10_15", "wall_hole": "needed"}[slot]
        out = await h.tap(f"s:C1:slot:{slot}:{pick}")
        check_floor(out)
    assert asked == ["outdoor_spot", "pipe_run_ft", "wall_hole"]
    k = out[0]
    assert k.card.template == "kharcha"
    assert k.card.data["zaroori_total_str"] == "₹48,240" and k.card.data["with_suggestions_total_str"] == "₹54,539"
    assert k.card.data["lifetime_text"]
    assert "Pipe +5 ft" in k.body
    await h.tap("s:C1:slot:socket_15a:no")
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:outcome:sold")
    out = await h.tap("s:C1:islot:t2")
    assert any(m.to == PHONES["manager"] and "risk" in m.body for m in out)
    job = (await repo.install_all())[-1]
    assert job.site_needs["pipe_run_ft"] == "p10_15" and "socket" in job.site_needs["_risks"]


async def test_wm_smoke_rules(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.say("washing machine chahiye, 5 log hain, bathroom mein rakhenge, roz dhulai, bedsheets bhi")
    check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.category == "wm"
    assert button_ids(out)[0] == "s:C1:slot:trigger:replace"  # why now, first
    out = await h.tap("s:C1:slot:trigger:new_home")
    assert button_ids(out)[0] == "s:C1:slot:water:hard"  # the one home question the note did not answer
    check_floor(await h.tap("s:C1:slot:water:hard"))
    out = await h.tap("s:C1:photo_no")
    rows = [r.id for sec in out[0].sections for r in sec.rows]
    assert rows[-1] == "s:C1:budget:none" and len(rows) == 4  # three ranges from the real prices + not decided
    out = await h.pick(rows[1])
    assert button_ids(out) == ["s:C1:readback_ok", "s:C1:readback_edit"]
    await h.tap("s:C1:readback_ok")
    out = await h.tap("s:C1:options")
    check_floor(out)
    assert any(m.card and m.card.template == "options" for m in out)
    await h.tap("s:C1:shown")
    out = await h.tap("s:C1:opt:B")
    check_floor(out)
    assert any(m.card and m.card.template == "kharcha" for m in out)


async def test_laptop_smoke_rules(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    out = await h.say("beta ke liye laptop, engineering student hai, roz college le jayega, budget 75000")
    check_floor(out)
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.category == "laptop" and s.home.start_level_inr == 75000
    for bid in (
        "s:C1:slot:software:coding",
        "s:C1:slot:battery_need:yes",
        "s:C1:slot:decider:family",
        "s:C1:slot:user:student",
        "s:C1:slot:course:engineering",
        "s:C1:slot:carry:daily",
    ):
        check_floor(await h.tap(bid))
    out = await h.tap("s:C1:options")
    check_floor(out)
    assert any(m.card and m.card.template == "options" for m in out)


async def test_manager_expert_visitlog(h):
    await h.say("namaste")
    h.as_("manager")
    out = await h.say("floor")
    check_floor(out)
    assert out[0].body.startswith("Abhi:")
    out = await h.say("digest")
    assert out[0].card.template == "digest"
    h.as_("visitor")
    await h.say("visit")
    out = await h.say(
        "Juhu store, TV scenario, associate ne pehle price bola minute 2 pe, demo nahi dikhaya, "
        "Ravi ne 3 sawaal pooche, wapas nahi aaunga"
    )
    check_floor(out)
    d = await repo.visit_draft(PHONES["visitor"])
    assert d.entry["price_first_by"] == "associate" and d.entry["demo_shown"] is False
    assert "Ravi" not in str(d.entry)
    while True:
        ids = button_ids(out)
        if any(":save:" in i for i in ids):
            out = await h.tap(next(i for i in ids if ":save:" in i))
            break
        out = await h.tap(ids[0])
    out = await h.tap("v:0:export:1")
    assert out[0].kind == "document" and out[0].filename == "visit_log.csv"
