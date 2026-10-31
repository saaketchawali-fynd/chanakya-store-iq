"""Golden path (TV): associate hi, customer mr — spec 18.3. Every outbound message passes the floor checks."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.conftest import make_harness
from tests.helpers import button_ids, check_floor, texts


async def ev_types(sid):
    return [e.type for e in await repo.events_for(sid)]


async def test_golden_path(tmp_path):
    h = await make_harness(tmp_path)
    allm = []

    async def step(fn, *a):
        out = await fn(*a)
        check_floor(out)
        allm.extend(out)
        return out

    try:
        # 1 brief
        out = await step(h.say, "namaste")
        brief = next(m for m in out if m.card and m.card.template == "brief")
        assert any("Meru 55 QLED Pro" in c["text"] for c in brief.card.data["changes"])
        assert [b.title for b in brief.buttons] == ["Clip dekho", "Aaj ka sawaal", "Naya customer"]
        # 2 new customer + language
        out = await step(h.tap, "g:new")
        assert "C1" in texts(out)
        out = await step(h.tap, "s:C1:lang:mr")
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        assert s.home.customer_lang == "mr"
        # 3 voice note → mounting question (Marathi) + Home Card 3/7
        out = await step(h.voice, "assess_1")
        coach = out[0]
        assert coach.coach and "भिंतीवर" in coach.body and "Kyun" in coach.body
        assert [b.id for b in coach.buttons] == [
            "s:C1:slot:mounting:wall",
            "s:C1:slot:mounting:stand",
            "s:C1:slot:mounting:unknown",
        ]
        assert any(m.card and m.card.template == "home" and m.card.data["known"] == 3 for m in out)
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        assert s.home.slots["seat_distance"][-1].value == "d8_10"
        assert s.home.start_level_inr == 50000 and s.home.start_level_source == "associate_voice"
        assert "₹" not in texts(out)
        # 4 mounting → elders question with Options; Home Card 4/7
        out = await step(h.tap, "s:C1:slot:mounting:wall")
        assert "s:C1:options" in button_ids(out) and "s:C1:slot:elders:yes" in button_ids(out)
        assert any(m.card and m.card.template == "home" and m.card.data["known"] == 4 for m in out)
        # 5 room photo → ack + try-on
        await step(h.tap, "s:C1:photo")
        out = await step(h.photo, "room_living.jpg")
        assert "Photo mil gayi" in out[0].body
        tryon = [m for m in out if m.image_bytes]
        assert tryon and "55" in tryon[0].body and "65" in tryon[0].body
        assert "tryon_sent" in await ev_types(s.id)
        # 6 claim → myth line
        out = await step(h.voice, "assess_2_claim")
        assert "बरोबर, बहुतेक चॅनेल HD" in out[0].body
        assert button_ids(out)[0] == "s:C1:proof:tv_4k_useless"
        # 7–8 label → b_motion then b_bright, Benefit Card, no price
        out = await step(h.photo, "label_meru55.jpg")
        assert "120 Hz" in out[0].body and any(m.card and m.card.template == "benefit" for m in out)
        out = await step(h.tap, "s:C1:benefit_next")
        assert "उन्हातही" in out[0].body
        evs = await repo.events_for(s.id, ["benefit_said", "label_read"])
        assert [e.payload.get("rule_id") for e in evs if e.type == "benefit_said"] == ["b_motion", "b_bright"]
        assert next(e for e in evs if e.type == "label_read").payload["sku"] == "TV-MER-55-QL120"
        # 9 offers too early → gate, no numbers
        out = await step(h.say, "offers")
        assert "Daam demo ke baad" in out[0].body and "₹" not in texts(out)
        assert "s:C1:override:price:asked" in button_ids(out)
        # 10 options + glare demo
        out = await step(h.tap, "s:C1:options")
        card = next(m for m in out if m.card and m.card.template == "options")
        assert [o["label"] + o["title"] for o in card.card.data["options"]] == [
            'AArka 55" 4K',
            'BMeru 55" QLED Pro',
            'CMeru 65" QLED Pro',
        ]
        assert "₹" not in texts(out) and "price" not in str(card.card.data)
        evs = await repo.events_for(s.id, ["demo_recipe_sent"])
        assert evs[-1].payload["recipe"] == "glare_test"
        # 11 shown → trial close
        out = await step(h.tap, "s:C1:shown")
        assert "🔓" in out[0].body and "Demo ✓" in out[0].body
        # 12 choose B → Kharcha
        out = await step(h.tap, "s:C1:opt:B")
        k = out[-1]
        assert k.card.template == "kharcha"
        assert k.card.data["zaroori_total_str"] == "₹65,288" and k.card.data["with_suggestions_total_str"] == "₹68,787"
        cost = (await repo.events_for(s.id, ["cost_shared"]))[-1]
        assert cost.payload["zaroori_total"] == 65288 and "charge:TV_WALLMOUNT_S" in cost.sources
        # 13 offers → HDFC → finance sentence
        await step(h.tap, "s:C1:offers")
        out = await step(h.tap, "s:C1:bank:HDFC")
        b = out[0].body
        for x in ("₹4,833", "12", "₹299", "₹58,295", "₹305", "HDFC", "₹5,000", "15 Nov", "₹57,990"):
            assert x in b, x
        fin = (await repo.events_for(s.id, ["finance_shown"]))[-1]
        assert fin.payload["plan_id"] == "FIN-NCE-12"
        # 14 consent by QR → Parivaar Card to the customer
        await step(h.tap, "s:C1:parivaar")
        out = await step(h.tap, "s:C1:qr")
        assert out[0].image_bytes
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        h.as_("customer")
        out = await step(h.say, f"CHANAKYA {s.optin_token}")
        to_cust = [m for m in out if m.to == PHONES["customer"]]
        assert any(m.card and m.card.template == "parivaar" for m in to_cust)
        assert all(b.id.startswith("u:") for m in to_cust for b in m.buttons)
        types = await ev_types(s.id)
        assert "consent_given" in types and "parivaar_sent" in types
        # 15 task
        h.as_("associate")
        await step(h.tap, "s:C1:task")
        out = await step(h.tap, "s:C1:task_when:today_evening")
        assert "task_created" in await ev_types(s.id)
        # 16 khatam → sold → install slot → score
        await step(h.tap, "s:C1:khatam")
        out = await step(h.tap, "s:C1:outcome:sold")
        out = await step(h.tap, "s:C1:islot:t1")
        assert any(m.card and m.card.template == "score" for m in out)
        sc = (await repo.scores_for(PHONES["associate"]))[0]
        assert sc.total == 14
        assert "install_job_created" in await ev_types(s.id)
        # 17 demo clock: D−1 checklist, install check, confirmation
        out = await step(h.clock, "+1d")
        assert any(m.to == PHONES["customer"] and "10–1" in (m.body or "") for m in out)
        out = await step(h.clock, "install")
        assert any(m.to == PHONES["customer"] and "u:" in "".join(button_ids([m])) for m in out)
        h.as_("customer")
        out = await step(h.tap, f"u:{s.id[:8]}:working")
        assert any(m.to == PHONES["associate"] and "✓" in m.body for m in out)
        assert "install_confirmed" in await ev_types(s.id)
        # global: no validator violations during the run
        assert await repo.events_for(None, ["validator_violation"]) == []
    finally:
        await h.close()
