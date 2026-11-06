"""Golden path (TV): associate hi, customer mr — spec 18.3. Every outbound message passes the floor checks."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.conftest import make_harness
from tests.helpers import button_ids, check_floor, open_brief, options_view, texts


async def ev_types(sid):
    return [e.type for e in await repo.events_for(sid)]


async def test_golden_path(tmp_path):
    h = await make_harness(tmp_path, lang_set=False)
    allm = []

    async def step(fn, *a):
        out = await fn(*a)
        check_floor(out)
        allm.extend(out)
        return out

    try:
        # 0 first contact: a neutral language picker (no greeting in any one language), nothing else
        out = await step(h.say, "namaste")
        assert [b.id for b in out[0].buttons] == ["g:alang:en", "g:alang:hi", "g:alang:mr"]
        assert not any(m.card for m in out) and "Namaste" not in out[0].body and "Bhasha" not in out[0].body
        # 1 shift start, one step at a time (Hindi chosen): the pre-read (stock, offers, keep in mind) → the clip →
        #   ready → only then the customer button
        out = await step(h.tap, "g:alang:hi")
        assert "naam" in out[0].body.lower() and not any(m.card for m in out)  # then: who is this? (no dummy name)
        out = await step(h.say, "mera naam Amit hai")  # then which product: today's stock and offers for it
        out = await step(open_brief, h, out, "tv")
        brief = next(m for m in out if m.card and m.card.template == "brief")
        assert "Amit" in brief.card.data["greeting"]
        assert brief.card.lang == "en"  # one card, in English
        assert any("Meru 55 QLED Pro" in c["text"] for c in brief.card.data["changes"])
        assert brief.card.data["stock"]["n"] and brief.card.data["stock"]["tiles"]
        offs = brief.card.data["offers"]
        assert offs and all(o["badge"] and o["title"] and o["till"] for o in offs)
        assert offs[0]["kind"] == "ends"  # the offers ending soonest come first
        assert [b.title for b in brief.buttons] == ["Aaj ki training", "Doosra product"]  # the next step, or another product
        out = await step(h.tap, brief.buttons[0].id)
        assert any(m.kind == "video" for m in out) and button_ids(out) == ["g:ready:translate"]
        out = await step(h.tap, "g:ready:translate")
        assert button_ids(out) == ["g:new"] and out[0].footer  # ready: the customer button, and voice any time
        # 2 new customer: the opener (welcome line + product list, the associate's department first)
        out = await step(h.tap, "g:new")
        rows = [r.id for sec in out[0].sections for r in sec.rows]
        assert out[0].kind == "list" and rows[0] == "s:C1:cat:tv" and "s:C1:cat:fridge" in rows
        assert "C1" in texts(out)
        # 3 the customer speaks Marathi (menu → customer language): only the lines to say follow the customer
        out = await step(h.tap, "s:C1:lang:mr")
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        assert s.home.customer_lang == "mr"
        # 4 a short voice note (room, distance, budget) → ONE message: why now? (Marathi line to say), what was
        #   understood in the footer. (A note that tells all the needs goes straight to the products: see
        #   test_note_shortlist.)
        out = await step(h.voice, "assess_short")
        assert len(out) == 1
        coach = out[0]
        assert coach.coach and "जुना टीव्ही" in coach.body and coach.body.startswith("*Bolo*:") and "*Kyun*:" in coach.body
        assert coach.footer.startswith("Samjha: 10 ft")
        assert button_ids(out) == ["s:C1:slot:trigger:replace", "s:C1:slot:trigger:new_home", "s:C1:voiceq:trigger"]
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        assert s.home.slots["seat_distance"][-1].value == "d8_10" and s.home.slots["room"][-1].value == "living"
        assert s.home.start_level_inr == 50000 and s.home.start_level_source == "associate_voice"
        assert "₹" not in texts(out, footer=False)
        # 5 replacing → the old TV's size → the 4 home facts are known: room photo? (2 buttons only)
        out = await step(h.tap, "s:C1:slot:trigger:replace")
        assert button_ids(out)[0] == "s:C1:slot:old_size:o32"
        out = await step(h.tap, "s:C1:slot:old_size:o43")
        assert button_ids(out)[0] == "s:C1:slot:room_light:sunny"  # the home questions the note did not answer
        out = await step(h.tap, "s:C1:slot:room_light:sunny")
        out = await step(h.say, "papa cricket dekhte hain, bachche cartoon")  # the answer said, not tapped
        assert len(out) == 1 and button_ids(out) == ["s:C1:photo_yes", "s:C1:photo_no"]
        # 6 photo → read it back (budget already said, so no range question) with the Home Card + try-on
        out = await step(h.tap, "s:C1:photo_yes")
        assert len(out) == 1 and not out[0].buttons
        out = await step(h.photo, "room_living.jpg")
        q = out[0]
        assert button_ids([q]) == ["s:C1:readback_ok", "s:C1:readback_edit"]
        assert q.card and q.card.template == "home" and q.card.data["known"] == 4
        assert "हॉल" in q.body and "10 ft" in q.body  # the home said back, in the customer's Marathi
        assert not [m for m in out if m.image_bytes]  # no picture dropped into the questions: it waits for the options
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        assert s.extra.get("room_read")  # the room was read in the background, ready for 'Ghar mein dekho'
        # 7 read-back confirmed → any doubts? → a doubt from the list → agree, then correct
        out = await step(h.tap, "s:C1:readback_ok")
        assert button_ids(out) == ["s:C1:options", "s:C1:doubt", "s:C1:pricefirst_q"]
        out = await step(h.tap, "s:C1:doubt")
        assert "s:C1:myth:tv_4k_useless" in [r.id for sec in out[0].sections for r in sec.rows]
        out = await step(h.pick, "s:C1:myth:tv_4k_useless")
        assert "बरोबर, बहुतेक चॅनेल HD" in out[0].body
        assert button_ids(out)[:2] == ["s:C1:go", "s:C1:proof:tv_4k_useless"]
        # 8 label → b_motion with a trial close → 'matters' → b_bright, product card, no price
        out = await step(h.photo, "label_meru55.jpg")
        assert "120 Hz" in out[0].body and out[0].card and out[0].card.template == "product"
        assert "हे तुमच्या कामाचं आहे का?" in out[0].body and button_ids(out)[0] == "s:C1:bt:yes:b_motion"
        out = await step(h.tap, "s:C1:bt:yes:b_motion")
        assert "उन्हातही" in out[0].body
        evs = await repo.events_for(s.id, ["benefit_said", "label_read"])
        assert [e.payload.get("rule_id") for e in evs if e.type == "benefit_said"] == ["b_motion", "b_bright"]
        assert next(e for e in evs if e.type == "label_read").payload["sku"] == "TV-MER-55-QL120"
        # 9 offers too early → gate, no numbers
        out = await step(h.say, "offers")
        assert "Daam demo ke baad" in out[0].body and "₹" not in texts(out, footer=False)
        assert "s:C1:override:price:asked" in button_ids(out)
        # 10 options: one product page per option, then the next steps → the demo on tap
        out = await step(h.tap, "s:C1:options")
        assert len(out) == 5 and not out[0].card and len(out[0].body.split("\n")) <= 2  # how many, for this home
        assert all(m.card and m.card.template == "product" for m in out[1:4]) and out[4].buttons  # a page each, then next
        card = options_view(out)
        assert [o["label"] + o["title"] for o in card.data["options"]] == [
            'AArka 55" 4K',
            'BMeru 55" QLED Pro',
            'CMeru 65" QLED Pro',
        ]
        assert button_ids(out) == ["s:C1:cmpall", "s:C1:demo", "s:C1:more"]  # side by side, demo guide, tell me more
        assert all(o["offer"]["text"] for o in card.data["options"])  # every option shows its offer (or none)
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        prices = [f"₹{p.price:,}" for p in (h.rt.facts.product_sync(x) for x in s.option_labels.values())]
        assert prices
        assert not any(pr in texts(out) for pr in prices) and "price" not in str(card.data)  # offers yes, prices no
        assert card.data["options"][1]["points"]  # built from this home's answers
        # 10a side by side: their room photo is here, so each option is drawn on their wall, then one detailed table
        out = await step(h.tap, "s:C1:cmpall")
        sbs = next(m.card for m in out if m.card and m.card.template == "homecompare")
        assert sbs.data["in_room"] and len(sbs.data["cols"]) == 3
        assert {sec["title"] for sec in sbs.data["sections"]} >= {"For your needs", "Features", "Cost and offers"}
        cost = next(sec for sec in sbs.data["sections"] if sec["title"] == "Cost and offers")
        assert all(c["value"] == "After the demo" for c in cost["rows"][0]["cells"])  # the price is still after the demo
        # 10b Ghar mein dekho → straight to the picture: B solid, C as the outline
        out = await step(h.tap, "s:C1:tryon")
        tryon = [m for m in out if m.image_bytes]
        assert len(tryon) == 1 and "55" in tryon[0].body and "65" in tryon[0].body
        assert "tryon_sent" in await ev_types(s.id)
        out = await step(h.tap, "s:C1:demo")
        evs = await repo.events_for(s.id, ["demo_recipe_sent"])
        assert evs[-1].payload["recipe"] == "glare_test"
        # 11 shown → which felt right? (the three + none of these)
        out = await step(h.tap, "s:C1:shown")
        rows = [r.id for sec in out[0].sections for r in sec.rows]
        assert rows == ["s:C1:opt:A", "s:C1:opt:B", "s:C1:opt:C", "s:C1:abwall", "s:C1:view", "s:C1:upgrade",
                        "s:C1:nonefit"]  # + both on their wall, how big from the sofa, is the extra money worth it
        out2 = await step(h.pick, "s:C1:upgrade")  # is the extra money worth it? (the price is open after the demo)
        assert out2[0].body.count("→") >= 1 and "roz lagbhag" in out2[0].body
        assert "Daam khula" in out[0].footer and "Demo ho gaya" in out[0].footer  # where they stand, in the footer
        # 12 B → the questions that change the cost (wall, wall type, soundbar) → Kharcha
        out = await step(h.pick, "s:C1:opt:B")
        assert button_ids(out)[0] == "s:C1:slot:mounting:wall"
        await step(h.tap, "s:C1:slot:mounting:wall")
        await step(h.tap, "s:C1:slot:wall_type:concrete")
        out = await step(h.tap, "s:C1:slot:soundbar:has")
        ao = out[-1]  # "Complete it": what goes with it for this home (the kids: protection), a step up, before the cost
        assert ao.card.template == "addons" and ao.card.data["items"] and ao.card.data["step_up"]["label"] == "C"
        assert button_ids(out) == ["s:C1:addons:all", "s:C1:addons:pick", "s:C1:addons:none"]
        out = await step(h.tap, "s:C1:addons:none")  # not now: nothing more is suggested on the bill
        k = out[-1]
        assert k.card.template == "kharcha"
        assert k.card.data["zaroori_total_str"] == "₹65,288" and not k.card.data["with_suggestions_total_str"]
        assert button_ids(out) == ["s:C1:offers", "s:C1:cost_rm", "s:C1:cost_show"]
        cost = (await repo.events_for(s.id, ["cost_shared"]))[-1]
        assert cost.payload["zaroori_total"] == 65288 and "charge:TV_WALLMOUNT_S" in cost.sources
        # 13 looks good → old TV working? → how will they pay? EMI → HDFC → offer card + finance in one sentence
        out = await step(h.tap, "s:C1:offers")
        assert button_ids(out)[0] == "s:C1:slot:exchange_old:yes_working"
        out = await step(h.tap, "s:C1:slot:exchange_old:yes_working")
        assert button_ids(out) == ["s:C1:pay:card", "s:C1:pay:upi", "s:C1:pay:emi"]
        out = await step(h.tap, "s:C1:pay:emi")
        assert "s:C1:bank:HDFC" in [r.id for sec in out[0].sections for r in sec.rows]
        out = await step(h.pick, "s:C1:bank:HDFC")
        b = out[0].body
        oc = out[0].card
        assert oc.template == "offer" and [o["amount_str"] for o in oc.data["offers"]] == ["−₹5,000", "−₹2,000"]
        assert oc.data["payable_str"] == "₹55,990" and oc.data["total_str"] == "₹58,288"
        for x in ("₹4,666", "12", "₹299", "₹56,291", "₹301"):
            assert x in b, x
        fin = (await repo.events_for(s.id, ["finance_shown"]))[-1]
        assert fin.payload["plan_id"] == "FIN-NCE-12"
        assert button_ids(out) == ["s:C1:delivery", "s:C1:nottoday", "s:C1:sendoffer"]
        # 14 the close as a choice of two days → a day = sold → consent by QR → Parivaar Card to the customer
        out = await step(h.tap, "s:C1:delivery")
        day = button_ids(out)[0]
        assert day.startswith("s:C1:dday:") and button_ids(out)[2] == "s:C1:nottoday"
        out = await step(h.tap, day)
        assert "s:C1:qr" in button_ids(out)
        out = await step(h.tap, "s:C1:qr")
        assert out[0].image_bytes
        s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
        h.as_("customer")
        out = await step(h.say, f"CHANAKYA {s.optin_token}")
        to_cust = [m for m in out if m.to == PHONES["customer"]]
        assert any(m.card and m.card.template == "parivaar" for m in to_cust)
        assert all(b.id.startswith("u:") for m in to_cust for b in m.buttons)
        to_assoc = [m for m in out if m.to == PHONES["associate"] and m.buttons]
        assert button_ids(to_assoc) == ["s:C1:sold", "s:C1:task", "s:C1:khatam"]  # install first, then a task
        types = await ev_types(s.id)
        assert "consent_given" in types and "parivaar_sent" in types and "delivery_booked" in types
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
        assert any(m.to == PHONES["associate"] and "Meru 55 QLED Pro" in m.body for m in out)
        assert "install_confirmed" in await ev_types(s.id)
        # global: no validator violations during the run
        assert await repo.events_for(None, ["validator_violation"]) == []
    finally:
        await h.close()
