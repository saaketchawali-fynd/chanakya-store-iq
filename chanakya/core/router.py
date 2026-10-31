"""Router (spec 7.2–7.4): role resolution, commands, button decode, intent dispatch."""

from __future__ import annotations

import re
from datetime import timedelta

from chanakya.ai.client import AIUnavailable
from chanakya.core import clock, repo, state
from chanakya.core.buttons import ButtonId, decode, encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import InboundMessage, ListRow, ListSection, OutMessage, Reply, Step
from chanakya.log import get_log
from chanakya.skills import (
    assess,
    brief,
    close,
    complete,
    customer,
    expert,
    follow,
    manager,
    recommend,
    review,
    translate,
    tryon_flow,
    validate,
    visitlog,
)
from chanakya.skills.base import SkillContext
from chanakya.truth.firewall import NumberRegistry

log = get_log("router")
_unknown_replied: set[str] = set()


def _cmd(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


CMD = {
    "brief": {"namaste", "namaskar", "start", "नमस्ते", "नमस्कार", "suru", "सुरू", "hi chanakya"},
    "new": {"naya", "new", "नया", "नवीन", "naya customer", "new customer", "नया ग्राहक", "नवीन ग्राहक"},
    "switch": {"switch", "customers", "ग्राहक"},
    "status": {"status", "स्थिती"},
    "options": {"options", "option", "पर्याय"},
    "offers": {"offers", "daam", "price", "offer", "दाम", "किंमत"},
    "khatam": {"khatam", "done", "end", "संपलं", "खतम"},
    "help": {"help", "madad", "मदत", "मदद"},
    "floor": {"floor"},
    "digest": {"digest"},
    "visit": {"visit"},
    "export": {"export"},
    "reset": {"reset"},
    "sold": {"sold", "bik gaya"},
    "score": {"score"},
}


def command_of(text: str | None) -> tuple[str | None, str]:
    c = _cmd(text)
    for name, words in CMD.items():
        if c in words:
            return name, ""
    for pre, name in (
        ("expert ", "expert"),
        ("lang ", "lang"),
        ("customer lang ", "customer_lang"),
        ("/role ", "role"),
        ("/clock ", "clock"),
        ("r ", "relay"),
    ):
        if c.startswith(pre):
            return name, (text or "").strip()[len(pre) :].strip()
    return None, ""


async def build_context(msg: InboundMessage, svc) -> SkillContext:
    staff = await repo.staff_by_wa(msg.from_wa_id)
    session = None
    role = "unknown"
    if staff:
        role = staff.role
        if staff.current_session_id:
            session = await svc.sessions.get(staff.current_session_id)
            if session and session.step == Step.CLOSED:
                session = None
    else:
        session = await svc.sessions.by_customer(msg.from_wa_id)
        if session and not session.extra.get("opted_out"):
            role = "customer"
        else:
            session = None
    return SkillContext(
        actor_wa_id=msg.from_wa_id,
        role=role,
        staff=staff,
        session=session,
        inbound=msg,  # type: ignore[arg-type]
        facts=svc.facts,
        packs=svc.packs,
        ai=svc.ai,
        reg=NumberRegistry(),
        now=clock.now(),
        svc=svc,
    )


async def dispatch(ctx: SkillContext) -> Reply:
    msg = ctx.inbound
    if ctx.role == "unknown":
        if (msg.text or "").strip().upper().startswith("CHANAKYA "):
            return await customer.optin(ctx, msg.text.strip()[len("CHANAKYA ") :])
        if ctx.actor_wa_id in _unknown_replied:
            return Reply()
        _unknown_replied.add(ctx.actor_wa_id)
        return Reply(messages=[OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("unknown_number", "hi"))])
    if ctx.role == "customer":
        return await dispatch_customer(ctx)
    # staff: demo + shared commands first
    name, arg = command_of(msg.text) if msg.type == "text" else (None, "")
    if name in ("role", "clock", "lang") or (name == "reset" and ctx.svc.settings.demo_mode):
        r = await staff_command(ctx, name, arg)
        if r is not None:
            return r
    if msg.type in ("button_reply", "list_reply", "template_button"):
        bid = decode(msg.reply_id)
        if bid and bid.scope == "m":
            return await manager_button(ctx, bid)
        if bid and bid.scope == "x":
            return await expert.expert_button(ctx, bid.get(0), bid.get(1))
        if bid and bid.scope == "v":
            if bid.get(1) == "export":
                return await visitlog.export(ctx)
            if bid.get(1) == "new":
                return await visitlog.start(ctx)
            return await visitlog.button(ctx, bid.get(0), bid.get(1), bid.get(2))
    if ctx.role == "manager":
        if name == "floor":
            return await manager.floor(ctx)
        if name == "digest":
            return await manager.digest(ctx)
        if name in ("visit", "export"):
            return await (visitlog.start(ctx) if name == "visit" else visitlog.export(ctx))
        if name == "help" or (msg.type == "text" and not name):
            return ctx.reply(
                info_reply(
                    ctx,
                    ctx.t("manager_help"),
                    [
                        ctx.b("b_digest", encode("m", "digest")),
                        ctx.b("b_team", encode("m", "team")),
                        ctx.b("b_approvals", encode("m", "approvals")),
                    ],
                )
            )
    if ctx.role == "expert" and msg.type in ("text", "audio") and name is None:
        return await expert.expert_message(ctx)
    if ctx.role == "visitor":
        if name == "visit":
            return await visitlog.start(ctx)
        if name == "export":
            return await visitlog.export(ctx)
        if msg.type in ("text", "audio"):
            return await visitlog.on_note(ctx)
    return await dispatch_associate(ctx, name, arg)


async def staff_command(ctx: SkillContext, name: str, arg: str) -> Reply | None:
    st = ctx.staff
    settings = ctx.svc.settings
    if name == "lang":
        lg = arg.strip().lower()[:2]
        if lg in ("hi", "mr", "en"):
            st.lang = lg
            return ctx.reply(info_reply(ctx, ctx.t("lang_set")))
        return None
    allowed = settings.demo_mode and (not settings.role_switch_phones or ctx.actor_wa_id in settings.role_switch_phones)
    if not allowed:
        return None
    if name == "role":
        r = arg.strip().lower()
        if r in ("associate", "manager", "expert", "visitor"):
            st.base_role = st.base_role or st.role
            st.role = r
            ctx.role = r  # type: ignore[assignment]
            return ctx.reply(info_reply(ctx, ctx.t("role_set", role=r)))
        return None
    if name == "clock":
        a = arg.strip().lower()
        if a == "reset":
            clock.set_offset(timedelta(0))
        elif a == "install":
            jobs = await repo.install_all(["created", "reminded", "due"])
            if jobs:
                j = jobs[-1]
                end_h = (j.site_needs or {}).get("_end_h", 13)
                target = clock.now().replace(
                    year=j.slot_date.year,
                    month=j.slot_date.month,
                    day=j.slot_date.day,
                    hour=end_h,
                    minute=5,
                    second=0,
                    microsecond=0,
                )
                if target > clock.now():
                    clock.advance(target - clock.now())
        else:
            m = re.match(r"\+(\d+)\s*([dh])", a)
            if m:
                n = int(m.group(1))
                clock.advance(timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n))
        await repo.setting_set("demo_offset_s", clock.offset().total_seconds())
        if ctx.svc.orchestrator and ctx.svc.orchestrator.scheduler:
            await ctx.svc.orchestrator.scheduler.tick()
        from chanakya.truth import fmt

        now = clock.now()
        return ctx.reply(
            info_reply(
                ctx,
                ctx.t(
                    "clock_set",
                    date=ctx.reg.raw(fmt.date_short(now, ctx.lang), "clock"),
                    time=ctx.reg.raw(fmt.time_short(now), "clock"),
                ),
            )
        )
    if name == "reset":
        if ctx.session:
            await ctx.svc.sessions.close(ctx.session, "reset", ctx.now)
            ctx.session = None
        st.current_session_id = None
        return ctx.reply(info_reply(ctx, ctx.t("reset_done"), [ctx.b("b_new_customer", encode("g", "new"))]))
    return None


async def manager_button(ctx: SkillContext, bid: ButtonId) -> Reply:
    a = bid.get(0)
    if a == "digest":
        return await manager.digest(ctx)
    if a == "team":
        return await manager.team(ctx)
    if a == "approvals":
        return await manager.approvals(ctx)
    if a == "appr":
        return await manager.approval_action(ctx, bid.get(1), bid.get(2))
    if a == "nudge":
        return await manager.nudge(ctx, bid.get(1), bid.get(2))
    if a == "who":
        return await manager.team(ctx)
    return await manager.floor(ctx)


async def dispatch_customer(ctx: SkillContext) -> Reply:
    msg = ctx.inbound
    if (msg.text or "").strip().upper().startswith("CHANAKYA "):
        return await customer.optin(ctx, msg.text.strip()[len("CHANAKYA ") :])
    if msg.type in ("button_reply", "template_button"):
        bid = decode(msg.reply_id)
        if bid and bid.scope == "u":
            return await customer.button(ctx, bid)
    return await customer.relay(ctx)


async def _switch_to(ctx: SkillContext, code: str) -> bool:
    s = await ctx.svc.sessions.by_code(ctx.actor_wa_id, code)
    if s is None or s.step == Step.CLOSED:
        return False
    if ctx.session and ctx.session.id != s.id:
        await ctx.svc.sessions.save(ctx.session, ctx.now)
    ctx.session = s
    ctx.staff.current_session_id = s.id
    return True


async def dispatch_associate(ctx: SkillContext, name: str | None, arg: str) -> Reply:
    msg = ctx.inbound
    # 1. buttons / list replies
    if msg.type in ("button_reply", "list_reply", "template_button"):
        bid = decode(msg.reply_id)
        if bid is None:
            return await _fallback(ctx)
        if bid.scope == "g":
            return await global_action(ctx, bid)
        if bid.scope == "s":
            if not await _switch_to(ctx, bid.get(0)):
                return await _fallback(ctx)
            return await session_action(ctx, bid.get(1), list(bid.parts[2:]))
        return await _fallback(ctx)
    # 2. commands
    if name:
        return await command(ctx, name, arg)
    # 3. voice / text
    if msg.type in ("audio", "text"):
        return await on_speech(ctx)
    # 4. images
    if msg.type == "image":
        return await on_image(ctx)
    return await _fallback(ctx)


async def _fallback(ctx: SkillContext) -> Reply:
    from chanakya.core.fallback import safe_reply

    return Reply(messages=[safe_reply(ctx.actor_wa_id, ctx.lang, ctx.session)])


async def global_action(ctx: SkillContext, bid: ButtonId) -> Reply:
    a = bid.get(0)
    if a == "new":
        return await assess.new_session(ctx)
    if a == "status":
        return await assess.status(ctx)
    if a == "help":
        return brief.help_reply(ctx)
    if a == "clip":
        return await brief.clip(ctx, bid.get(1, "translate"))
    if a == "quiz":
        return await brief.quiz(ctx, bid.get(1, "translate"))
    if a == "qa":
        return await brief.answer(ctx, bid.get(1), bid.get(2))
    if a == "lang":
        ctx.staff.lang = bid.get(1, "hi")
        return ctx.reply(info_reply(ctx, ctx.t("lang_set")))
    if a == "switch":
        if await _switch_to(ctx, bid.get(1)):
            return await assess.status(ctx)
        return await _fallback(ctx)
    if a == "score":
        return await review.last_score(ctx)
    if a == "brief":
        return await brief.brief(ctx)
    return await _fallback(ctx)


async def command(ctx: SkillContext, name: str, arg: str) -> Reply:
    s = ctx.session
    if name == "brief":
        return await brief.brief(ctx)
    if name == "new":
        return await assess.new_session(ctx)
    if name == "help":
        return brief.help_reply(ctx)
    if name == "score":
        return await review.last_score(ctx)
    if name == "switch":
        act = await ctx.svc.sessions.active(ctx.actor_wa_id)
        if not act:
            return await assess.status(ctx)
        rows = [
            ListRow(
                id=encode("g", "switch", x.code),
                title=f"{x.code} · {(x.home.customer_name or '–')[:14]}",
                description=(x.home.category or "").upper(),
            )
            for x in act[:10]
        ]
        for r in rows:
            ctx.reg.raw(r.title, "session:code")
        return ctx.reply(
            OutMessage(
                to=ctx.actor_wa_id,
                kind="list",
                body=ctx.t("switch_body"),
                list_button=ctx.t("list_choose"),
                sections=[ListSection(rows=rows)],
                allowed_numbers=ctx.reg.allowed(),
            )
        )
    if name == "status":
        return await assess.status(ctx)
    if name == "expert":
        return await expert.ask(ctx, arg)
    if name == "relay":
        return await customer.associate_reply(ctx, arg)
    if name in ("visit", "export"):
        return await (visitlog.start(ctx) if name == "visit" else visitlog.export(ctx))
    if name in ("floor", "digest"):
        return await (manager.floor(ctx) if name == "floor" else manager.digest(ctx))
    if s is None:
        if name in ("options", "offers", "khatam", "sold"):
            return ctx.reply(info_reply(ctx, ctx.t("status_none"), [ctx.b("b_new_customer", encode("g", "new"))]))
    if name == "customer_lang" and s:
        return await assess.set_customer_lang(ctx, arg.strip().lower()[:2])
    if name == "options":
        return await recommend.show_options(ctx)
    if name == "offers":
        if not state.price_gate_open(s, ctx.pack) and not s.shortlist and not s.home.slots:
            return await validate.price_rescue(ctx)
        return await close.offers(ctx)
    if name == "khatam":
        return await review.khatam(ctx)
    if name == "sold":
        return await follow.sold(ctx)
    return await _fallback(ctx)


async def session_action(ctx: SkillContext, action: str, args: list[str]) -> Reply:
    a0 = args[0] if args else None
    a1 = args[1] if len(args) > 1 else None
    table = {
        "go": lambda: assess.go(ctx),
        "photo": lambda: assess.photo_prompt(ctx),
        "options": lambda: recommend.show_options(ctx),
        "shown": lambda: recommend.shown(ctx),
        "demo2": lambda: recommend.demo2(ctx),
        "tryon": lambda: tryon_flow.tryon_request(ctx),
        "benefit_next": lambda: translate.benefit(ctx),
        "compare": lambda: translate.compare_prompt(ctx),
        "cost_show": lambda: complete.resend_card(ctx) if ctx.session.cost_snapshot else complete.show_cost(ctx),
        "cost_drop_optional": lambda: complete.toggle_suggestions(ctx),
        "offers": lambda: close.offers(ctx),
        "emi": lambda: close.quote(ctx, alt=True),
        "delivery": lambda: close.delivery(ctx),
        "parivaar": lambda: follow.parivaar(ctx),
        "qr": lambda: follow.show_qr(ctx),
        "consent_tap": lambda: follow.consent_tap(ctx),
        "voice_add": lambda: follow.voice_add(ctx),
        "task": lambda: follow.task_menu(ctx),
        "khatam": lambda: review.khatam(ctx),
        "sold": lambda: follow.sold(ctx),
        "expert": lambda: expert.ask(ctx, ctx.session.extra.get("last_claim_text") or ctx.inbound.text or ""),
        "askmgr": lambda: close.ask_manager(ctx),
    }
    if action in table:
        return await table[action]()
    if action == "lang":
        ctx.session.extra["lang_tapped"] = True
        return await assess.set_customer_lang(ctx, a0 or "hi")
    if action == "slot" and a0 and a1:
        return await assess.tap_slot(ctx, a0, a1)
    if action == "slotask" and a0:
        from chanakya.skills import common

        if ctx.pack and ctx.pack.slot(a0):
            return ctx.reply(common.question_reply(ctx, a0))
    if action == "budget":
        return await assess.set_budget(ctx, a0 or "none")
    if action == "opt" and a0:
        return await complete.choose(ctx, a0)
    if action == "override" and a0:
        return await recommend.override(ctx, a0, a1 or "hurry")
    if action == "proof":
        return await validate.proof(ctx, a0)
    if action == "bank":
        return await close.set_bank(ctx, a0 or "other")
    if action == "dday" and a0:
        return await close.set_delivery(ctx, a0)
    if action == "task_when":
        return await follow.create_task(ctx, a0 or "tomorrow")
    if action == "outcome":
        return await review.outcome(ctx, a0 or "followup")
    if action == "pricefirst":
        return await review.price_first(ctx, a0 or "none")
    if action == "islot":
        return await follow.create_install(ctx, a0 or "t1")
    if action == "wallw":
        return await tryon_flow.set_wall_width(ctx, a0 or "w9")
    if action == "tpick" and a0:
        return await tryon_flow.pick(ctx, a0)
    if action == "label" and a0:
        return await translate.show_sku(ctx, a0)
    if action == "imgkind" and a0:
        return await classified_image(ctx, a0)
    if action == "called":
        return ctx.reply(info_reply(ctx, ctx.t("noted")))
    return await _fallback(ctx)


def _understand_ctx(ctx: SkillContext) -> dict:
    s = ctx.session
    pack = ctx.pack or ctx.packs.get("tv")
    known = {k: [v.value for v in vals] for k, vals in (s.home.slots.items() if s else [])}
    return {
        "category": (s.home.category if s else None) or (ctx.staff.dept if ctx.staff else "tv"),
        "known_slots": known,
        "slots": [{"id": sd.id, "values": sd.values} for sd in pack.slots],
        "myths": [{"id": m.id, "belief": m.belief} for m in ctx.packs.all_myths(pack.category)][:30],
        "options_shown": {k: v for k, v in (s.option_labels.items() if s else [])},
    }


async def on_speech(ctx: SkillContext) -> Reply:
    msg = ctx.inbound
    s = ctx.session
    text = msg.text if msg.type == "text" else None
    # awaiting states
    if s and msg.type == "audio" and s.awaiting == "voice_for_parivaar":
        return await follow.forward_voice(ctx)
    if s and s.awaiting == "lost_reason":
        s.awaiting = None
        s.home.notes.append("lost_reason: " + (text or "[voice note]"))
        return ctx.reply(info_reply(ctx, ctx.t("noted"), [ctx.b("b_new_customer", encode("g", "new"))]))
    # guards on what the associate typed
    if text and not text.startswith("FIX:"):
        phrase = validate.banned_in(ctx, text)
        if phrase:
            return await validate.honest_version(ctx, phrase)
        if validate.looks_bait(ctx, text):
            return await validate.no_looks(ctx)
    audio = None
    if msg.type == "audio" and msg.media_id:
        audio, _ = await ctx.svc.channel.download_media(msg.media_id)
    try:
        u = await ctx.ai.understand(text, audio, _understand_ctx(ctx))
    except AIUnavailable:
        btns = [
            ctx.b("b_next_q", ctx.sid("go")) if s else ctx.b("b_new_customer", encode("g", "new")),
            ctx.b("b_status", encode("g", "status")),
            ctx.b("b_help", encode("g", "help")),
        ]
        return ctx.reply(info_reply(ctx, ctx.t("voice_unavailable"), btns))
    tr = u.transcript or text or ""
    # guards on the transcript (fixtures / future voice model)
    if validate.looks_bait(ctx, tr):
        return await validate.no_looks(ctx)
    if close.is_discount_ask(ctx, tr):
        if s is None:
            return await close.no_invented_discount(ctx)
        return await close.no_invented_discount(ctx)
    # 1. new customer
    if s is None:
        if not (u.slots or u.numbers or u.intents and set(u.intents) - {"other"}):
            return await _fallback(ctx)
        await assess.new_session(ctx, customer_lang=u.customer_language or ctx.lang, ask_lang=False)
        s = ctx.session
        if u.category:
            s.home.category = u.category
    # 2. apply slots and numbers
    gate_before = state.options_gate_open(s, ctx.pack)
    assess.apply_understanding(ctx, u, "voice" if msg.type == "audio" or (text or "").startswith("FIX:") else "text")
    s.extra["last_claim_text"] = tr[:200]
    intents = set(u.intents)
    if "price_asked" in intents:
        return await validate.price_rescue(ctx)
    if u.claim_id:
        return await validate.myth(ctx, u.claim_id)
    if u.objection:
        return await close.objection(ctx, u.objection)
    if u.option_choice and s.option_labels:
        return await complete.choose(ctx, u.option_choice)
    if u.library_id or u.question_for_expert:
        return await expert.ask(
            ctx, u.question_for_expert or tr, via_truth_check=bool(u.question_for_expert), library_id=u.library_id
        )
    if "demo_done" in intents and s.shortlist:
        return await recommend.shown(ctx)
    if "request_options" in intents:
        return await recommend.show_options(ctx)
    if "request_cost" in intents:
        return await complete.show_cost(ctx)
    if "request_offers" in intents:
        return await close.offers(ctx)
    return await assess.on_info(ctx, u, applied=True, gate_before=gate_before)


async def on_image(ctx: SkillContext) -> Reply:
    msg = ctx.inbound
    s = ctx.session
    data, mime = await ctx.svc.channel.download_media(msg.media_id)
    fname = ctx.svc.media_name(msg.media_id)
    if s is None:
        await assess.new_session(ctx, ask_lang=False)
        s = ctx.session
    if s.awaiting == "room_photo":
        return await tryon_flow.on_room_photo(ctx, data, fname)
    read = await ctx.ai.read_image(data, fname, msg.text)
    if read.kind == "label":
        return await translate.on_label(ctx, read)
    if read.kind == "room":
        return await tryon_flow.on_room_photo(ctx, data, fname)
    await repo.media_put(s.id, "pending_image", data, mime or "image/jpeg", ctx.now, days=1)
    s.extra["pending_image_name"] = fname
    btns = [ctx.b("b_is_label", ctx.sid("imgkind", "label")), ctx.b("b_is_room", ctx.sid("imgkind", "room"))]
    return ctx.reply(info_reply(ctx, ctx.t("image_other"), btns))


async def classified_image(ctx: SkillContext, kind: str) -> Reply:
    s = ctx.session
    pending = await repo.media_latest(s.id, "pending_image")
    if pending is None:
        return ctx.reply(info_reply(ctx, ctx.t("image_other")))
    if kind == "room":
        return await tryon_flow.on_room_photo(ctx, pending.data, s.extra.get("pending_image_name"))
    pack = ctx.pack
    rows = [ListRow(id=ctx.sid("label", p.sku), title=p.model[:24]) for p in ctx.facts.products_sync(pack.category)][
        :10
    ]
    for r in rows:
        ctx.reg.raw(r.title, "product:list")
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="list",
            body=ctx.t("which_model"),
            list_button=ctx.t("list_choose"),
            sections=[ListSection(rows=rows)],
            allowed_numbers=ctx.reg.allowed(),
        )
    )
