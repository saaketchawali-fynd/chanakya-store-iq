"""Step 2 — Ghar Samjho (Assess): new customer, language, voice/text → Home Card → next-best question."""

from __future__ import annotations

from chanakya.ai.schemas import Understanding
from chanakya.brain import packs as P
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext

LANG_TITLES = {"hi": "b_lang_hi", "mr": "b_lang_mr", "en": "b_lang_en"}


async def new_session(ctx: SkillContext, customer_lang: str | None = None, ask_lang: bool = True) -> Reply:
    """A new customer. The customer's language starts as the associate's (changeable from the menu); the first
    message is the opener: the welcome line to say and the product list."""
    svc = ctx.svc
    ctx.session = await svc.sessions.create(ctx.actor_wa_id, ctx.now, customer_lang or ctx.lang, None)
    ctx.staff.current_session_id = ctx.session.id
    ctx.session.extra["started_at"] = ctx.now.isoformat()
    ctx.ev("session_started", code=ctx.session.code, category=None, lang=ctx.session.home.customer_lang)
    if not ask_lang:
        return ctx.reply()
    return ctx.reply(C.category_message(ctx))


def category_question(ctx: SkillContext) -> Reply:
    return ctx.reply(C.category_message(ctx))


async def set_category(ctx: SkillContext, cat: str) -> Reply:
    s = ctx.session
    if cat not in ctx.packs.packs:
        return category_question(ctx)
    if s.home.category and s.home.category != cat:  # a different product: the home facts start over
        s.home.slots = {}
        for k in ("photo_asked", "budget_asked", "readback_done", "doubts_asked", "range_bands", "range_id"):
            s.extra.pop(k, None)
    s.home.category = cat  # type: ignore[assignment]
    ctx.ev("category_set", category=cat)
    pending = s.extra.pop("pending_u", None)
    u = Understanding.model_validate(pending) if pending else Understanding()
    return await on_info(ctx, u, "voice" if pending else "tap")


async def customer_lang_picker(ctx: SkillContext) -> Reply:
    btns = [ctx.b(LANG_TITLES[lg], ctx.sid("lang", lg)) for lg in ("en", "hi", "mr")]
    return ctx.reply(info_reply(ctx, ctx.t("clang_pick_body"), btns))


async def set_customer_lang(ctx: SkillContext, lang: str) -> Reply:
    if lang not in ("hi", "mr", "en"):
        lang = ctx.lang
    ctx.session.home.customer_lang = lang  # type: ignore[assignment]
    ctx.ev("customer_lang_set", lang=lang)
    note = ctx.t("clang_set", language=ctx.t(f"lang_name_{lang}"))
    msg = C.next_question_or_options(ctx)
    msg.footer = note[:60]
    return ctx.reply(msg)


def apply_understanding(ctx: SkillContext, u: Understanding, source: str = "voice") -> int:
    """Apply slots/numbers to the Home Card. Returns the number of new slot values."""
    s = ctx.session
    home = s.home
    if u.category and (home.category is None or (home.category != u.category and not home.slots)):
        if ctx.packs.get(u.category):
            home.category = u.category
    if home.category is None:  # product not known yet: keep the note, the opener asks the product first
        s.extra["pending_u"] = u.model_dump()
        return 0
    pack = ctx.pack
    if u.customer_name and not home.customer_name:
        home.customer_name = u.customer_name
    if u.sports_viewer:
        home.sports_viewer = u.sports_viewer
    if u.customer_language and not s.extra.get("lang_tapped"):
        home.customer_lang = u.customer_language  # type: ignore[assignment]
    new = 0
    for sp in u.slots:
        sd = pack.slot(sp.slot)
        if not sd or (sd.values and sp.value not in sd.values):
            continue
        if P.set_slot(home, pack, sp.slot, sp.value, sd.value_number(sp.value) if sd.bands else None, source, ctx.now):
            new += 1
            ctx.ev("slot_filled", slot=sp.slot, value=sp.value, source=source)
    for npair in u.numbers:
        if npair.name == "budget_inr":
            home.start_level_inr = int(npair.value)
            home.start_level_source = "associate_voice"
            continue
        sid = pack.slot_for_number(npair.name)
        if not sid:
            continue
        if P.set_slot(home, pack, sid, "num", float(npair.value), source, ctx.now):
            new += 1
            ctx.ev("slot_filled", slot=sid, value=P.value(home, sid), number=npair.value, source=source)
    if "first_note_slots" not in s.extra and (u.slots or u.numbers):
        s.extra["first_note_slots"] = new
    return new


async def on_info(
    ctx: SkillContext, u: Understanding, source: str = "voice", applied: bool = False, gate_before: bool | None = None,
    n_new: int | None = None,
) -> Reply:
    """Default rule (7.4 #10): apply, then need-validation or the next question (+ Home Card / budget list)."""
    s = ctx.session
    pack = ctx.pack
    if gate_before is None:
        gate_before = state.options_gate_open(s, pack)
    if not applied:
        n_new = apply_understanding(ctx, u, source)
    if n_new == 0 and not u.customer_request and ctx.x.get("readback_shown") and not ctx.x.get("readback_done") \
            and not s.shortlist and C.pick_next_slot(ctx) is None:
        return ctx.reply(C.readback_nudge(ctx))  # nothing new: don't resend the read-back and its cards
    msgs = []
    if u.customer_request and not s.extra.get("need_validated"):
        s.extra["need_validated"] = True
        ctx.ev("need_validated", text=u.customer_request)
        slot = C.pick_next_slot(ctx)
        say = ctx.pick(ctx.packs.common_line("need_validation"), ctx.clang)
        if slot:  # agree with what they asked for, then the next home question in the same breath
            say = f"{say} {ctx.pick(pack.slot(slot).ask, ctx.clang)}"
            msgs.append(C.question_reply(ctx, slot, say_override=say, why_override=ctx.t("kyun_need_validation")))
        else:
            msgs.append(C.next_question_or_options(ctx, footer=noted_footer(ctx, source)))
    else:
        msgs.append(C.next_question_or_options(ctx, footer=noted_footer(ctx, source)))
    return ctx.reply(*msgs)


def note_facts(ctx: SkillContext) -> list[str]:
    """This note's facts in the associate's words, numbers first: ['10 ft', '₹50,000', 'Dhoop aati hai', …]."""
    pack = ctx.pack
    nums, facts = [], []
    for e in ctx.events:
        if e.type != "slot_filled":
            continue
        sd = pack.slot(e.payload.get("slot")) if pack else None
        if not sd:
            continue
        if e.payload.get("number") is not None and sd.number:
            unit = {"seat_distance_ft": "ft", "room_size_sqft": "sq ft", "pipe_run_ft": "ft", "hours_per_day": "h",
                    "niche_width_cm": "cm", "family_size": ctx.t("unit_people")}.get(sd.number, "")
            nums.append(ctx.reg.num(e.payload["number"], f"card:{sd.id}", unit))
        else:
            facts.append(ctx.reg.static(sd.value_title(e.payload.get("value"), ctx.lang)))
    home = ctx.session.home
    if home.start_level_inr and home.start_level_source == "associate_voice" and not ctx.x.get("budget_noted"):
        ctx.x["budget_noted"] = True  # the budget once, in the note that gave it
        nums.append(ctx.reg.inr(home.start_level_inr, "card:start_level"))
    return list(dict.fromkeys(nums + facts))


def noted_footer(ctx: SkillContext, source: str) -> str | None:
    """After a voice/typed note: show what was understood ("Samjha: 10 ft · Dhoop · Cricket"), so a mishearing
    is caught at once. Facts only from this note; fits WhatsApp's 60-character footer without cutting words."""
    if source == "tap":
        return None
    pack = ctx.pack
    nums, facts = [], []
    for e in ctx.events:
        if e.type != "slot_filled":
            continue
        sd = pack.slot(e.payload.get("slot")) if pack else None
        if not sd:
            continue
        if e.payload.get("number") is not None and sd.number:
            unit = {"seat_distance_ft": "ft", "room_size_sqft": "sq ft", "pipe_run_ft": "ft",
                    "hours_per_day": "h", "niche_width_cm": "cm"}.get(sd.number, "")
            nums.append(ctx.reg.num(e.payload["number"], f"card:{sd.id}", unit))
        else:
            facts.append(ctx.reg.static(sd.value_title(e.payload.get("value"), ctx.lang)))
    home = ctx.session.home
    if home.start_level_inr and home.start_level_source == "associate_voice" and not ctx.x.get("budget_noted"):
        ctx.x["budget_noted"] = True
        nums.append(ctx.reg.inr(home.start_level_inr, "card:start_level"))
    facts = nums + facts  # numbers (distance, size, budget) first: they matter most
    if not facts:
        return None
    out = ""
    for f in facts:
        cand = ctx.t("footer_noted", facts=(out + " · " + f) if out else f)
        if len(cand) > 60:
            break
        out = (out + " · " + f) if out else f
    return ctx.t("footer_noted", facts=out) if out else None


async def tap_slot(ctx: SkillContext, slot: str, val: str) -> Reply:
    pack = ctx.pack
    sd = pack.slot(slot)
    if not sd:
        return ctx.reply(C.next_question_or_options(ctx))
    u = Understanding(transcript="", slots=[])
    from chanakya.ai.schemas import SlotPair

    u.slots = [SlotPair(slot=slot, value=val)]
    if ctx.x.get("pending_cost") and (slot in pack.complete_slots or sd.phase == "site"):
        apply_understanding(ctx, u, "tap")
        from chanakya.skills import complete

        return await complete.show_cost(ctx)
    if ctx.x.get("pending_install") and slot in pack.site_needs:
        apply_understanding(ctx, u, "tap")
        from chanakya.skills import follow

        return await follow.sold(ctx)
    if ctx.x.get("pending_close") and slot == "exchange_old":
        apply_understanding(ctx, u, "tap")
        ctx.x.pop("pending_close", None)
        from chanakya.skills import close

        return await close.offers(ctx)
    return await on_info(ctx, u, "tap")


async def set_budget(ctx: SkillContext, band_id: str) -> Reply:
    pack = ctx.pack
    s = ctx.session
    rb = next((b for b in s.extra.get("range_bands") or [] if b["id"] == band_id), None)
    if rb:  # a comfort range cut from the real prices
        s.home.start_level_inr = int(rb["level"])
        s.home.start_level_source = "associate_tap"
        s.extra["range_id"] = band_id
        s.extra["budget_asked"] = True
        ctx.ev("comfort_range_set", band=band_id, low=rb["a"], high=rb["b"])
        return ctx.reply(C.next_question_or_options(ctx))
    band = next((b for b in pack.budget_bands if b.id == band_id), None)
    if band and band.inr:
        s.home.start_level_inr = int(band.inr)
        s.home.start_level_source = "associate_tap"
    else:
        s.home.start_level_inr = None
        s.home.start_level_source = "none"
    s.extra["budget_asked"] = True
    return ctx.reply(C.next_question_or_options(ctx))


async def readback_ok(ctx: SkillContext) -> Reply:
    ctx.x["readback_done"] = True
    ctx.ev("readback_confirmed")
    return ctx.reply(C.next_question_or_options(ctx))


async def readback_edit(ctx: SkillContext) -> Reply:
    """'Change something': the known answers as a list; the one tapped is asked again, then the read-back repeats."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection, OutMessage

    pack = ctx.pack
    home = ctx.session.home
    rows = []
    for sid in pack.order:
        sd = pack.slot(sid)
        if not sd or not P.known(home, sid):
            continue
        val = ", ".join(sd.value_title(v, ctx.lang) for v in P.values(home, sid))
        rows.append(ListRow(id=ctx.sid("slotask", sid), title=truncate(ctx.pick(sd.title) or sid, 24), description=truncate(val, 72)))
    for r in rows:
        ctx.reg.static(r.title + " " + (r.description or ""))
    ctx.x["readback_done"] = False
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("readback_edit_body"),
                                list_button=truncate_title(ctx.t("list_change")), sections=[ListSection(rows=rows[:10])],
                                allowed_numbers=ctx.reg.allowed()))


async def reask(ctx: SkillContext, slot: str) -> Reply:
    """Ask one known question again (from 'Change something'); the new tap replaces the old answer."""
    pack = ctx.pack
    if not pack or not pack.slot(slot):
        return ctx.reply(C.next_question_or_options(ctx))
    ctx.x["readback_done"] = False
    return ctx.reply(C.question_reply(ctx, slot))


async def doubt_list(ctx: SkillContext) -> Reply:
    """'Yes, a doubt': this category's common beliefs as a list (one tap → the confirm-then-correct line)."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection, OutMessage

    myths = ctx.packs.all_myths(ctx.session.home.category)
    rows = [ListRow(id=ctx.sid("myth", m.id), title=truncate(ctx.pick(getattr(m, "short", None) or {"en": m.belief}), 24))
            for m in myths if getattr(m, "short", None)][:9]
    rows.append(ListRow(id=ctx.sid("doubt_voice"), title=ctx.t("row_other_doubt")))
    for r in rows:
        ctx.reg.static(r.title)
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("doubt_list_body"),
                                list_button=truncate_title(ctx.t("list_doubt")), sections=[ListSection(rows=rows)],
                                allowed_numbers=ctx.reg.allowed()))


async def doubt_voice(ctx: SkillContext) -> Reply:
    m = info_reply(ctx, ctx.t("doubt_voice_prompt"))
    m.footer = ctx.t("footer_voice_first")
    return ctx.reply(m)


async def photo_yes(ctx: SkillContext) -> Reply:
    """Customer has a photo: ask for it, nothing else on screen."""
    ctx.session.awaiting = "room_photo"
    ctx.x["photo_asked"] = True
    m = info_reply(ctx, ctx.t("room_photo_send"))
    m.footer = ctx.t("footer_photo_skip")
    return ctx.reply(m)


async def photo_no(ctx: SkillContext) -> Reply:
    """No photo: carry on with what the customer says."""
    ctx.x["photo_asked"] = True
    ctx.session.awaiting = None
    ctx.x.pop("tryon_after_photo", None)
    return ctx.reply(C.next_question_or_options(ctx, footer=ctx.t("footer_photo_none")))


async def photo_prompt(ctx: SkillContext) -> Reply:
    ctx.session.awaiting = "room_photo"
    btns = [
        ctx.b("b_next_q", ctx.sid("go")),
        ctx.b("b_options", ctx.sid("options")),
        ctx.b("b_status", encode("g", "status")),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("room_photo_send"), btns))


async def go(ctx: SkillContext) -> Reply:
    return ctx.reply(C.next_question_or_options(ctx))


async def status(ctx: SkillContext) -> Reply:
    s = ctx.session
    if s is None:
        return ctx.reply(
            info_reply(
                ctx,
                ctx.t("status_none"),
                [ctx.b("b_new_customer", encode("g", "new")), ctx.b("b_help", encode("g", "help"))],
            )
        )
    pack = ctx.pack
    msg = C.home_card_message(ctx, force=True)
    btns: list[Button] = []
    if not s.shortlist:
        btns = [ctx.b("b_next_q", ctx.sid("go")), ctx.b("b_options", ctx.sid("options"))]
    else:
        btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_cost_show", ctx.sid("cost_show"))]
    btns.append(ctx.b("b_khatam", ctx.sid("khatam")))
    from chanakya.core.status import status_line

    msg.body = status_line(ctx) or msg.body
    msg.buttons = btns
    msg.kind = "buttons"
    msg.allowed_numbers = ctx.reg.allowed()
    _ = pack
    return ctx.reply(msg)


async def data_answer(ctx: SkillContext, key: str, choice: str) -> Reply:
    """A data question answered: the chosen value becomes a want (it filters the shortlist); 'any' is fine too."""
    s = ctx.session
    vals = (s.extra.get("dq_vals") or {}).get(key) or []
    done = list(s.extra.get("dq_done") or [])
    if key not in done:
        done.append(key)
    s.extra["dq_done"] = done
    if choice.isdigit() and int(choice) < len(vals):
        s.extra["wants"] = {**(s.extra.get("wants") or {}), key: vals[int(choice)]}
    ctx.ev("data_question_answered", key=key, value=str(vals[int(choice)]) if choice.isdigit() and int(choice) < len(vals) else "any")
    return ctx.reply(C.next_question_or_options(ctx))


async def voice_answer(ctx: SkillContext, slot: str) -> Reply:
    """The voice button on a question: just speak (Hindi, English or Marathi); every tap answer is still here."""
    from chanakya.core.coach import info_reply

    pack = ctx.pack
    sd = pack.slot(slot) if pack else None
    if sd is None:
        return ctx.reply(C.next_question_or_options(ctx))
    ctx.asking.add(slot)
    ctx.x["last_question"] = slot
    q = ctx.pick(sd.ask, ctx.clang)
    m = info_reply(ctx, ctx.t("voiceq_prompt", question=q), C.quick_buttons(ctx, slot))
    m.footer = ctx.t("voiceq_footer")
    return ctx.reply(m)
