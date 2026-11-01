"""Step 2 — Ghar Samjho (Assess): new customer, language, voice/text → Home Card → next-best question."""

from __future__ import annotations

from chanakya.ai.schemas import Understanding
from chanakya.brain import packs as P
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext

LANG_TITLES = {"hi": "b_lang_hi", "mr": "b_lang_mr", "en": "b_lang_en"}


async def new_session(ctx: SkillContext, customer_lang: str | None = None, ask_lang: bool = True) -> Reply:
    svc = ctx.svc
    cat = ctx.staff.dept if ctx.staff and ctx.staff.dept in ctx.packs.packs else None
    ctx.session = await svc.sessions.create(ctx.actor_wa_id, ctx.now, customer_lang or ctx.lang, cat)
    ctx.staff.current_session_id = ctx.session.id
    ctx.ev("session_started", code=ctx.session.code, category=cat, lang=ctx.session.home.customer_lang)
    if not ask_lang:
        return ctx.reply()
    code = ctx.reg.raw(ctx.session.code, "session:code")
    btns = [ctx.b(LANG_TITLES[lg], ctx.sid("lang", lg)) for lg in ("hi", "mr", "en")]
    return ctx.reply(info_reply(ctx, ctx.t("new_customer_lang", code=code), btns))


def category_question(ctx: SkillContext) -> Reply:
    btns = [
        ctx.b("b_cat_tv", ctx.sid("cat", "tv")),
        ctx.b("b_cat_ac", ctx.sid("cat", "ac")),
        ctx.b("b_cat_wm", ctx.sid("cat", "wm")),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("ask_category"), btns))


async def set_category(ctx: SkillContext, cat: str) -> Reply:
    s = ctx.session
    if cat not in ctx.packs.packs:
        return category_question(ctx)
    s.home.category = cat  # type: ignore[assignment]
    pending = s.extra.pop("pending_u", None)
    u = Understanding.model_validate(pending) if pending else Understanding()
    return await on_info(ctx, u, "voice")


async def set_customer_lang(ctx: SkillContext, lang: str) -> Reply:
    if lang not in ("hi", "mr", "en"):
        lang = "hi"
    ctx.session.home.customer_lang = lang  # type: ignore[assignment]
    m = info_reply(ctx, ctx.t("speak_it_back"))
    m.footer = ctx.t("footer_voice_first")
    return ctx.reply(m)


def apply_understanding(ctx: SkillContext, u: Understanding, source: str = "voice") -> int:
    """Apply slots/numbers to the Home Card. Returns the number of new slot values."""
    s = ctx.session
    home = s.home
    if u.category and (home.category is None or (home.category != u.category and not home.slots)):
        if ctx.packs.get(u.category):
            home.category = u.category
    if home.category is None:
        dept = ctx.staff.dept if ctx.staff else None
        if dept not in ctx.packs.packs:  # unknown product: the router asks "Kaunsa product?" first
            s.extra["pending_u"] = u.model_dump()
            return 0
        home.category = dept
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
    ctx: SkillContext, u: Understanding, source: str = "voice", applied: bool = False, gate_before: bool | None = None
) -> Reply:
    """Default rule (7.4 #10): apply, then need-validation or the next question (+ Home Card / budget list)."""
    s = ctx.session
    pack = ctx.pack
    if gate_before is None:
        gate_before = state.options_gate_open(s, pack)
    if not applied:
        apply_understanding(ctx, u, source)
    msgs = []
    if u.customer_request and not s.extra.get("need_validated"):
        s.extra["need_validated"] = True
        ctx.ev("need_validated", text=u.customer_request)
        slot = C.pick_next_slot(ctx)
        say = ctx.pick(ctx.packs.common_line("need_validation"), ctx.clang)
        if slot:
            msgs.append(C.question_reply(ctx, slot, say_override=say, why_override=ctx.t("kyun_need_validation")))
        else:
            cites = C.known_cites(ctx)
            msgs.append(
                coach_reply(ctx, say, ctx.t("kyun_need_validation"), cites, [ctx.b("b_options", ctx.sid("options"))])
                if cites
                else None
            )
    else:
        msgs.append(C.next_question_or_options(ctx, footer=noted_footer(ctx, source)))
    return ctx.reply(*msgs)


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
    if ctx.x.get("pending_cost") and sd.phase == "site":
        apply_understanding(ctx, u, "tap")
        from chanakya.skills import complete

        return await complete.show_cost(ctx)
    return await on_info(ctx, u, "tap")


async def set_budget(ctx: SkillContext, band_id: str) -> Reply:
    pack = ctx.pack
    band = next((b for b in pack.budget_bands if b.id == band_id), None)
    s = ctx.session
    if band and band.inr:
        s.home.start_level_inr = int(band.inr)
        s.home.start_level_source = "associate_tap"
    else:
        s.home.start_level_inr = None
        s.home.start_level_source = "none"
    s.extra["budget_asked"] = True
    return ctx.reply(C.next_question_or_options(ctx))


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
