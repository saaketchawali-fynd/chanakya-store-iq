"""Router (spec 7.2–7.4): role resolution, commands, button decode, intent dispatch."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from chanakya.ai.client import AIUnavailable
from chanakya.core import clock, repo, state
from chanakya.core.buttons import ButtonId, decode, encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import (
    CUSTOMER_PHONE_LANG,
    Button,
    InboundMessage,
    ListRow,
    ListSection,
    OutMessage,
    Reply,
    Step,
)
from chanakya.log import get_log
from chanakya.skills import (
    assess,
    brief,
    catalog,
    close,
    complete,
    customer,
    expert,
    facts_qa,
    flowctl,
    follow,
    manager,
    recommend,
    returning,
    review,
    staff_name,
    translate,
    tryon_flow,
    validate,
    view,
    visitlog,
)
from chanakya.skills.base import SkillContext
from chanakya.truth.firewall import NumberRegistry

log = get_log("router")
_unknown_replied: set[str] = set()
FEATURES_OF_OPTION = re.compile(
    r"(?:\boption\s*|\b)([ABCabc])\s*(?:ke|ka|ki|के|का|की|चे|च्या|ची|'s)?\s*"
    r"(?:features?|fayde|faayde|फायदे|फ़ायदे|ख़ूबियाँ|खूबियाँ|वैशिष्ट्ये|specs?)",
    re.IGNORECASE,
)


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
    "help": {"help", "madad", "मदत", "मदद", "menu", "मेनू", "मेन्यू"},
    "exit": {"exit", "band", "band karo", "cancel", "बंद", "बंद करो", "बंद करा", "थांबा"},
    "restart": {
        "restart",
        "start over",
        "shuru se",
        "phir se shuru",
        "fir se shuru",
        "phir se",
        "fresh start",
        "new start",
        "फिर से शुरू",
        "शुरू से",
        "पुन्हा सुरू",
        "सुरुवातीपासून",
    },
    "floor": {"floor"},
    "digest": {"digest"},
    "visit": {"visit"},
    "export": {"export"},
    "reset": {"reset"},
    "sold": {"sold", "bik gaya"},
    "score": {"score"},
    "language": {"language", "bhasha", "भाषा", "lang"},
    "catalog": {"catalog", "catalogue", "products", "models", "सूची", "यादी"},
    "wins": {"wins", "meri jeet", "jeet", "streak", "मेरी जीत", "माझी जीत"},
    "view": {"sofa se", "view", "viewing", "kitna bada lagega", "सोफ्यावरून", "सोफे से"},
    "abwall": {"a vs b", "dono deewar par", "side by side", "दोन्ही भिंतीवर"},
}


def command_of(text: str | None) -> tuple[str | None, str]:
    c = _cmd(text)
    for name, words in CMD.items():
        if c in words:
            return name, ""
    for pre, name in (
        ("expert ", "expert"),
        ("naam ", "myname"),
        ("name ", "myname"),
        ("my name is ", "myname"),
        ("mera naam ", "myname"),
        ("नाव ", "myname"),
        ("lang ", "lang"),
        ("customer lang ", "customer_lang"),
        ("/role ", "role"),
        ("/clock ", "clock"),
        ("r ", "relay"),
        ("catalog ", "catalog"),
        ("catalogue ", "catalog"),
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
            from chanakya.core.orchestrator import AFTER_SALE_AWAIT

            session = await svc.sessions.get(staff.current_session_id)
            if session and session.step == Step.CLOSED and session.awaiting not in AFTER_SALE_AWAIT:
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
        return Reply(messages=[OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("unknown_number", CUSTOMER_PHONE_LANG))])
    if ctx.role == "customer":
        return await dispatch_customer(ctx)
    # staff: demo + shared commands first
    name, arg = command_of(msg.text) if msg.type == "text" else (None, "")
    # first contact (or 'language'): the associate picks a language; everything after uses only that language
    b0 = decode(msg.reply_id) if msg.type in ("button_reply", "list_reply") else None
    if b0 and b0.scope == "g" and b0.get(0) == "alang":
        return await set_staff_language(ctx, b0.get(1, "en"))
    if ctx.staff and (name == "language" or (not ctx.staff.lang_set and name not in ("role", "clock", "reset"))):
        return language_picker(ctx)
    if ctx.role == "associate" and ctx.staff and name in ("myname",):
        return await staff_name.change(ctx, arg)
    if ctx.role == "associate" and ctx.staff and staff_name.needs_name(ctx.staff) and name not in ("role", "clock", "reset"):
        return await staff_name.capture(ctx)  # who is this? asked once, after the language
    if name in ("exit", "restart") and ctx.role != "associate":
        return await (flowctl.exit_ask(ctx) if name == "exit" else flowctl.restart_ask(ctx))
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
        if msg.type == "audio" or (msg.type == "text" and name in (None, "help")):
            return await manager.by_voice(ctx)
    if ctx.role == "expert" and msg.type in ("text", "audio") and name is None:
        return await expert.expert_message(ctx)
    if ctx.role == "visitor":
        if name == "visit":
            return await visitlog.start(ctx)
        if name == "export":
            return await visitlog.export(ctx)
        if msg.type in ("text", "audio"):
            return await visitlog.on_note(ctx)
    if ctx.role in ("manager", "expert", "visitor"):  # never an associate's sales session from another role's phone
        return _role_help(ctx)
    return await dispatch_associate(ctx, name, arg)


def _role_help(ctx: SkillContext) -> Reply:
    if ctx.role == "manager":
        return manager.menu(ctx)
    if ctx.role == "visitor":
        return ctx.reply(info_reply(ctx, ctx.t("visit_start")))
    return ctx.reply(info_reply(ctx, ctx.t("expert_idle")))


LANG_BUTTONS = (("en", "English"), ("hi", "हिंदी"), ("mr", "मराठी"))


def language_picker(ctx: SkillContext) -> Reply:
    """First contact: nothing in any one language before the associate picks one (each line in its own script)."""
    body = "Choose your language\nभाषा चुनें\nभाषा निवडा"
    btns = [Button(id=encode("g", "alang", lg), title=title) for lg, title in LANG_BUTTONS]
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="buttons", body=body, buttons=btns))


async def set_staff_language(ctx: SkillContext, lang: str) -> Reply:
    st = ctx.staff
    st.lang = lang if lang in ("hi", "mr", "en") else "hi"
    st.lang_set = True
    role = ctx.role
    if role == "associate" and not st.name_set:
        return staff_name.ask(ctx)
    if role == "manager":
        return manager.menu(ctx)
    if role == "expert":
        return ctx.reply(info_reply(ctx, ctx.t("expert_idle")))
    if role == "visitor":
        return await visitlog.start(ctx)
    return await brief.brief(ctx)


async def staff_command(ctx: SkillContext, name: str, arg: str) -> Reply | None:
    st = ctx.staff
    settings = ctx.svc.settings
    if name == "lang":
        lg = arg.strip().lower()[:2]
        if lg in ("hi", "mr", "en"):
            st.lang = lg
            st.lang_set = True
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
        return await manager.voice_log(ctx, bid.get(1))
    if a == "vlog":
        return await manager.voice_log(ctx)
    if a == "vnote" and (bid.get(1) or "").isdigit():
        return await manager.voice_detail(ctx, int(bid.get(1)))
    if a == "floor":
        return await manager.floor(ctx)
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


# Follow-through actions keep working after the conversation closed (task reminders, install booking, the
# outcome, a voice note or card for the customer, a call logged days later).
AFTER_SALE = {"parivaar", "qr", "consent_tap", "voice_add", "task", "task_when", "taskdone", "tasksnooze", "khatam",
              "sold", "outcome", "pricefirst", "islot", "called", "sendoffer"}


async def _button_session(ctx: SkillContext, code: str):
    """The session a tapped button belongs to: the exact one its message was sent for (codes C1, C2… restart
    every day, so yesterday's C1 must not act on today's C1); without that link, today's session with the code."""
    sid = await repo.message_session(ctx.inbound.context_message_id)
    s = await ctx.svc.sessions.get(sid) if sid else None
    if s is not None and s.associate_wa_id == ctx.actor_wa_id:
        return s
    return await ctx.svc.sessions.by_code(ctx.actor_wa_id, code)


async def _switch_to(ctx: SkillContext, code: str, action: str | None = None) -> bool | str:
    s = await _button_session(ctx, code)
    if s is None:
        return False
    if s.step == Step.CLOSED and action not in AFTER_SALE:
        return "closed"
    if ctx.session and ctx.session.id != s.id:
        await ctx.svc.sessions.save(ctx.session, ctx.now)
    ctx.session = s
    if s.step != Step.CLOSED:
        ctx.staff.current_session_id = s.id
    return True


def _closed_reply(ctx: SkillContext, code: str) -> Reply:
    """A button from a finished conversation: say so, and offer the way forward (never a silent fallback)."""
    from chanakya.core.coach import info_reply

    body = ctx.t("button_from_closed", code=ctx.reg.static(code))
    return ctx.reply(info_reply(ctx, body, [ctx.b("b_new_customer", "g:new"), ctx.b("b_status", "g:status")]))


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
            ok = await _switch_to(ctx, bid.get(0), bid.get(1))
            if ok == "closed":
                return _closed_reply(ctx, bid.get(0))
            if not ok:
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
    if a in ("help", "menu"):
        return await flowctl.menu(ctx)
    if a == "exit":
        return await flowctl.exit_ask(ctx)
    if a == "exitok":
        return await flowctl.exit_ok(ctx)
    if a == "exitnew":
        return await flowctl.exit_and_new(ctx)
    if a == "restart":
        return await flowctl.restart_ask(ctx)
    if a == "restartok":
        return await flowctl.restart_ok(ctx)
    if a == "langpick":
        return language_picker(ctx)
    if a == "clip":
        return await brief.clip(ctx, bid.get(1, "translate"))
    if a == "ready":
        return await brief.ready(ctx, bid.get(1, "translate"))
    if a == "quiz":
        return await brief.quiz(ctx, bid.get(1, "translate"))
    if a == "qa":
        return await brief.answer(ctx, bid.get(1), bid.get(2))
    if a == "lang":
        ctx.staff.lang = bid.get(1, "en")
        return ctx.reply(info_reply(ctx, ctx.t("lang_set")))
    if a == "switch":
        if await _switch_to(ctx, bid.get(1)):
            return await assess.status(ctx)
        return await _fallback(ctx)
    if a == "score":
        return await review.last_score(ctx)
    if a == "brief":
        return await brief.brief(ctx)
    if a == "catalog":
        page = bid.get(2, "0")
        return await catalog.show(ctx, bid.get(1), int(page) if page.isdigit() else 0)
    return await _fallback(ctx)


async def command(ctx: SkillContext, name: str, arg: str) -> Reply:
    s = ctx.session
    if name == "wins":
        from chanakya.skills import wins

        return await wins.show(ctx)
    if name == "view" and s and s.option_labels:
        return await view.show(ctx)
    if name == "abwall" and s and len(s.option_labels) >= 2:
        return await tryon_flow.choose(ctx, "ab", None)
    if name == "brief":
        return await brief.brief(ctx)
    if name == "new":
        return await assess.new_session(ctx)
    if name == "help":
        return await flowctl.menu(ctx)
    if name == "exit":
        return await flowctl.exit_ask(ctx)
    if name == "restart":
        return await flowctl.restart_ask(ctx)
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
    if name == "catalog":
        cat = {"washing": "wm", "washing machine": "wm", "wm": "wm", "ac": "ac", "tv": "tv", "laptop": "laptop", "fridge": "fridge", "refrigerator": "fridge"}.get(
            (arg or "").strip().lower()
        )
        return await catalog.show(ctx, cat)
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
        "photo_yes": lambda: assess.photo_yes(ctx),
        "photo_no": lambda: assess.photo_no(ctx),
        "options": lambda: recommend.show_options(ctx),
        "shown": lambda: recommend.shown(ctx),
        "demo2": lambda: recommend.demo2(ctx),
        "tryon": lambda: tryon_flow.tryon_request(ctx),
        "tmenu": lambda: tryon_flow.menu(ctx),
        "benefit_next": lambda: translate.benefit(ctx),
        "readback_ok": lambda: assess.readback_ok(ctx),
        "readback_edit": lambda: assess.readback_edit(ctx),
        "doubt": lambda: assess.doubt_list(ctx),
        "doubt_voice": lambda: assess.doubt_voice(ctx),
        "pricefirst_q": lambda: validate.price_rescue(ctx),
        "demo": lambda: recommend.demo(ctx),
        "prefer": lambda: recommend.prefer(ctx),
        "nonefit": lambda: recommend.none_fit(ctx),
        "cost_rm": lambda: complete.remove_menu(ctx),
        "nottoday": lambda: close.not_today(ctx),
        "presale": lambda: follow.parivaar(ctx, presale=True),
        "clang": lambda: assess.customer_lang_picker(ctx),
        "compare": lambda: translate.compare_smart(ctx),
        "compare2": lambda: translate.compare_prompt(ctx),
        "cost_show": lambda: complete.resend_card(ctx) if ctx.session.cost_snapshot else complete.show_cost(ctx),
        "cost_drop_optional": lambda: complete.toggle_suggestions(ctx),
        "offers": lambda: close.offers(ctx),
        "ofs": lambda: recommend.offers_view(ctx),
        "upgrade": lambda: recommend.upgrade_view(ctx),
        "more": lambda: recommend.more_menu(ctx),
        "askdq": lambda: recommend.ask_more(ctx, None),
        "returning": lambda: returning.list_view(ctx),
        "view": lambda: view.show(ctx),
        "abwall": lambda: tryon_flow.choose(ctx, "ab", None),
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
        "sendoffer": lambda: close.send_to_customer(ctx),
    }
    if action in table:
        return await table[action]()
    if action == "lang":
        ctx.session.extra["lang_tapped"] = True
        return await assess.set_customer_lang(ctx, a0 or "hi")
    if action == "retpick" and a0:
        return await returning.resume(ctx, ":".join(args))
    if action == "swap" and a0:
        return await returning.swap(ctx, a0)
    if action == "askq" and a0:
        return await recommend.ask_more(ctx, a0)
    if action == "voiceq" and a0:
        return await assess.voice_answer(ctx, a0)
    if action == "dq" and a0 and a1:
        return await assess.data_answer(ctx, a0, a1)
    if action == "dqx" and a0:
        return await assess.data_explain(ctx, a0)
    if action == "ofd" and a0:
        if a0 == "none":
            return await recommend.offers_view(ctx)
        return await recommend.offer_detail(ctx, a0, a1 or "")
    if action == "slot" and a0 and a1:
        return await assess.tap_slot(ctx, a0, a1)
    if action == "cat" and a0:
        return await assess.set_category(ctx, a0)
    if action == "slotask" and a0:
        return await assess.reask(ctx, a0)
    if action == "myth" and a0:
        return await validate.myth(ctx, a0)
    if action == "rerank" and a0:
        return await recommend.rerank(ctx, a0)
    if action == "bt" and a0:
        return await translate.benefit_reply(ctx, a0, a1)
    if action == "cost_drop" and a0:
        return await complete.drop_line(ctx, a0)
    if action == "pay" and a0:
        return await close.set_pay(ctx, a0)
    if action == "obj" and a0:
        return await close.objection(ctx, a0)
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
    if action in ("taskdone", "tasksnooze") and a0 and a0.isdigit():
        return await follow.task_action(ctx, int(a0), action == "taskdone")
    if action == "oldtv":
        return await tryon_flow.set_old_tv(ctx, a0 or "none")
    if action == "wallw":
        return await tryon_flow.set_wall_width(ctx, a0 or "w9")
    if action == "tmode" and a0:
        return await tryon_flow.choose(ctx, a0, a1)
    if action == "tpick" and a0:
        return await tryon_flow.pick(ctx, a0)
    if action == "label" and a0:
        return await translate.show_sku(ctx, a0)
    if action == "imgkind" and a0:
        return await classified_image(ctx, a0)
    if action == "called":
        return ctx.reply(info_reply(ctx, ctx.t("noted")))
    return await _fallback(ctx)


async def _understand_ctx(ctx: SkillContext) -> dict:
    s = ctx.session
    pack = ctx.pack or ctx.packs.get("tv")
    known = {k: [v.value for v in vals] for k, vals in (s.home.slots.items() if s else [])}
    lib = await repo.library_all(pack.category)
    opts = {}
    for k, sku in s.option_labels.items() if s else []:
        p = ctx.facts.product_sync(sku)
        opts[k] = p.model if p else sku
    return {
        "category": (s.home.category if s else None) or (ctx.staff.dept if ctx.staff else "tv"),
        "known_slots": known,
        "slots": [
            {
                "id": sd.id,
                "hint": sd.ask.get("en", ""),
                "number_name": sd.number,
                "values": [{"id": v, "means": sd.value_title(v, "en")} for v in sd.values],
            }
            for sd in pack.slots
        ],
        "myths": [{"id": m.id, "belief": m.belief} for m in ctx.packs.all_myths(pack.category)][:30],
        "brands": sorted({p.brand for p in ctx.facts.products_sync(pack.category) if p.brand})[:40],  # spelling help
        "library": [{"id": f"L{e.id}", "q": e.question} for e in lib][:30],
        "options_shown": opts,
        "themes": __import__("chanakya.brain.highlights", fromlist=["catalogue"]).catalogue(pack.category),
    }


async def on_speech(ctx: SkillContext) -> Reply:
    r = await _on_speech(ctx)
    s = ctx.session
    if s is not None and s.home.customer_name and not s.extra.get("ret_offered") and ctx.role == "associate":
        m = await returning.name_match(ctx)  # 'Shah ji came 3 days ago: liked B…' — carry on, no re-asking
        if m is not None:
            r.messages.insert(0, m)
    return r


async def _on_speech(ctx: SkillContext) -> Reply:
    msg = ctx.inbound
    s = ctx.session
    text = msg.text if msg.type == "text" else None
    # awaiting states
    if s and msg.type == "audio" and s.awaiting == "voice_for_parivaar":
        return await follow.forward_voice(ctx)
    if s and s.awaiting == "lost_reason":
        s.awaiting = None
        s.home.notes.append("lost_reason: " + (text or "[voice note]"))
        ctx.ev("lost_reason", text=(text or "[voice note]")[:200])  # for the manager's Digest and the review
        if ctx.staff and ctx.staff.current_session_id == s.id:
            ctx.staff.current_session_id = None
        return ctx.reply(info_reply(ctx, ctx.t("noted"), [ctx.b("b_new_customer", encode("g", "new"))]))
    # guards on what the associate typed
    if text and not text.startswith("FIX:"):
        phrase = validate.banned_in(ctx, text)
        if phrase:
            return await validate.honest_version(ctx, phrase)
        if validate.looks_bait(ctx, text):
            return await validate.no_looks(ctx)
    if text and not text.startswith("FIX:") and facts_qa.looks_like_product_question(text):
        r = await facts_qa.answer(ctx, text)
        if r is not None:
            return r
    audio, amime = None, None
    if msg.type == "audio" and msg.media_id:
        audio, amime = await ctx.svc.channel.download_media(msg.media_id)
    try:
        u = await ctx.ai.understand(text, audio, await _understand_ctx(ctx), mime=amime or msg.mime_type)
    except AIUnavailable:
        btns = [
            ctx.b("b_next_q", ctx.sid("go")) if s else ctx.b("b_new_customer", encode("g", "new")),
            ctx.b("b_status", encode("g", "status")),
            ctx.b("b_help", encode("g", "help")),
        ]
        if not ctx.ai.live:
            return ctx.reply(info_reply(ctx, ctx.t("voice_unavailable"), btns))
        if audio is not None and not msg.wa_message_id.endswith(RETRY_TAG):
            # the note was fine; Google's voice service was not: keep the note and answer it as soon as it can
            ctx.ev("voice_deferred")
            ctx.svc.orchestrator.spawn(retry_voice(ctx.svc, msg, audio, amime or msg.mime_type, ctx.lang))
            return ctx.reply(info_reply(ctx, ctx.t("voice_busy"), btns[:2]))
        return ctx.reply(info_reply(ctx, ctx.t("voice_failed_final"), btns))
    tr = u.transcript or text or ""
    if msg.type == "audio" or (text or "").startswith("FIX:"):  # the manager's voice log
        ctx.ev("voice_note", code=s.code if s else None, lang=u.language, transcript=" ".join(tr.split())[:300],
               understood=[f"{x.slot}={x.value}" for x in u.slots][:8] + [f"{x.name}={x.value}" for x in u.numbers][:4],
               intents=[i for i in u.intents if i != "other"][:4])
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
    if s.home.category is None and u.category and ctx.packs.get(u.category):
        s.home.category = u.category
    if s.home.category is None:  # product first: keep what was said, ask the product (the note is applied after)
        s.extra["pending_u"] = u.model_dump()
        return assess.category_question(ctx)
    gate_before = state.options_gate_open(s, ctx.pack)
    n_new = assess.apply_understanding(ctx, u, "voice" if msg.type == "audio" or (text or "").startswith("FIX:") else "text")
    s.extra["last_claim_text"] = tr[:200]
    intents = set(u.intents)
    m = FEATURES_OF_OPTION.search(tr)
    if m and s.option_labels.get(m.group(1).upper()):  # "B ke features" → Translate for that option
        return await translate.show_sku(ctx, s.option_labels[m.group(1).upper()])
    if "price_asked" in intents:
        if s.chosen_sku and (s.cost_snapshot or {}).get("sku") == s.chosen_sku:
            return await complete.show_cost(ctx, resend=True)  # the full cost they already saw
        if s.shortlist and state.price_gate_open(s, ctx.pack):
            return await recommend.prefer(ctx)  # which one? then its full cost
        return await validate.price_rescue(ctx)
    if u.claim_id:
        return await validate.myth(ctx, u.claim_id)
    if u.objection:
        return await close.objection(ctx, u.objection)
    if u.option_choice and s.option_labels:
        return await complete.choose(ctx, u.option_choice)
    if u.question_for_expert and not u.library_id:  # data first; the expert only when the data has no answer
        r = await facts_qa.answer(ctx, u.question_for_expert or tr)
        if r is not None:
            return r
    if u.library_id or u.question_for_expert:
        return await expert.ask(
            ctx, u.question_for_expert or tr, via_truth_check=bool(u.question_for_expert), library_id=u.library_id
        )
    note = _requirements_note(ctx, tr, n_new, intents, u)
    if note is not None:  # the needs were told in one note: no more fixed questions, the right products now
        return await recommend.show_options(ctx, note=note)
    if "demo_done" in intents and s.shortlist:
        return await recommend.shown(ctx)
    if "request_options" in intents:
        return await recommend.show_options(ctx)
    if "request_cost" in intents:
        return await complete.show_cost(ctx)
    if "request_offers" in intents:
        return await close.offers(ctx)
    r = await voice_step(ctx, u, intents)  # the later steps by voice, with the same guards as their buttons
    if r is not None:
        return r
    r = await assess.on_info(ctx, u, applied=True, gate_before=gate_before, n_new=n_new)
    if msg.type == "audio" and not n_new and tr.strip() and r.messages:  # nothing new: show what was heard
        snip = " ".join(tr.split())
        snip = snip if len(snip) <= 38 else snip[:37].rstrip() + "…"
        r.messages[0].footer = ctx.t("footer_heard", text=ctx.reg.raw(snip, "voice:transcript"))
    return r


def _requirements_note(ctx: SkillContext, tr: str, n_new: int, intents: set, u=None) -> dict | None:
    """Did this note describe what the customer needs (several facts, or a clear ask such as '55 inch Sony')?
    Then the shortlist comes straight from it. Explicit product asks are kept for the shortlist ('wants'); what
    they have now never is (assess.apply_product_meaning)."""
    from chanakya.ai.schemas import WantPair
    from chanakya.brain import wants as W

    s = ctx.session
    pack = ctx.pack
    if pack is None or not ctx.facts.snap.products or s.chosen_sku:
        return None
    new = {wp.key: wp.value for wp in (u.wants if u is not None else [])}
    if u is not None and not (u.wants or u.existing or u.avoid or u.asks or u.upgrade):
        # an understanding without the product fields (a recorded note): read the words clause by clause
        brands = sorted({p.brand for p in ctx.facts.products_sync(pack.category) if p.brand})
        parsed = W.parse_note(tr, pack.category, brands)
        keys = set(WantPair.model_fields["key"].annotation.__args__)
        for name in ("wants", "existing", "avoid"):
            setattr(u, name, [WantPair(key=k, value=str(v)) for k, v in parsed[name].items() if k in keys])
        u.upgrade = parsed["upgrade"]
        from chanakya.brain import highlights as HL

        u.asks = HL.asks_in(tr, pack.category)
        n_new = (n_new or 0) + assess.apply_product_meaning(ctx, u)
        new = parsed["wants"]
    n_new = n_new or 0
    new = new or {}
    asked = "request_options" in intents
    rich = n_new >= 3 or (new and (n_new >= 1 or len(new) >= 2)) or (asked and (n_new >= 1 or new))
    if not rich or (s.shortlist and not (n_new >= 2 or new)):
        return None
    slots = list(dict.fromkeys(e.payload.get("slot") for e in ctx.events if e.type == "slot_filled"))
    return {"slots": [x for x in slots if x], "wants": s.extra.get("wants") or {}}


RETRY_TAG = ":retry"
VOICE_RETRY_WAITS = (12.0, 25.0, 40.0)  # seconds between tries while Google's voice service is busy


async def retry_voice(svc, msg: InboundMessage, audio: bytes, mime: str | None, lang: str) -> None:
    """A voice note that could not be heard because the model was busy: try again in the background; when the
    words come through, handle the note as if it had just arrived. If it never does, say so plainly."""
    from chanakya.app import diag
    from chanakya.i18n.lang import t as tr

    for wait in VOICE_RETRY_WAITS:
        await asyncio.sleep(wait)
        try:
            await svc.ai.hear(audio, mime)  # cached: the replay below does not ask the model again
        except AIUnavailable:
            continue
        diag.bump("voice_retried_ok")
        await svc.orchestrator.handle_inbound(msg.model_copy(update={"wa_message_id": msg.wa_message_id + RETRY_TAG}))
        return
    diag.bump("voice_retry_gave_up")
    await svc.deliver(OutMessage(to=msg.from_wa_id, kind="text", body=tr("voice_failed_final", lang)))


async def voice_step(ctx: SkillContext, u, intents: set) -> Reply | None:
    """Voice first at every step: what the associate says maps to the button that step would offer."""
    s = ctx.session
    when = u.remind_when
    if "sold" in intents:
        return await follow.sold(ctx)
    if "lost" in intents:
        return await review.outcome(ctx, "lost")
    if "set_reminder" in intents:
        return await (follow.create_task(ctx, when) if when else follow.task_menu(ctx))
    if "not_today" in intents:
        if when:
            return await follow.create_task(ctx, when)
        return await close.not_today(ctx) if s.shortlist else await review.outcome(ctx, "followup")
    if "send_family_card" in intents:
        return await follow.parivaar(ctx, presale=s.extra.get("outcome") != "sold")
    if "request_tryon" in intents and ctx.pack and ctx.pack.placeable:
        return await tryon_flow.tryon_request(ctx)
    if "demo_next" in intents:
        return await (recommend.demo(ctx) if s.shortlist else recommend.show_options(ctx))
    if "request_emi" in intents:
        ctx.x["pay"] = "emi"
        return await close.offers(ctx)
    if "request_delivery" in intents:
        if s.chosen_sku and ctx.x.get("pay"):
            return await close.delivery(ctx)
        return await close.offers(ctx) if s.chosen_sku else await recommend.shown(ctx) if s.shortlist else None
    if "next_step" in intents:
        from chanakya.skills import common as C

        return ctx.reply(C.next_question_or_options(ctx)) if not s.shortlist else await assess.status(ctx)
    return None


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
