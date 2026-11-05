"""Visheshagya — Ask an expert: library first, else route to an expert, hold, answer, save to library.

The expert's phone is a queue: any greeting shows the open questions (who asked, the product, how long ago); a tap
starts the answer. Each question carries the product being discussed and its facts from the data, and the
customer's home, so the expert answers without asking back. The answer reaches the associate as a clean block: the
question, the answer, and the line to say to the customer when the expert gave one."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from chanakya.ai.client import AIUnavailable
from chanakya.brain import packs as P
from chanakya.channel.limits import truncate
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import bolo_block, coach_reply, do_block, info_reply, kyun_block
from chanakya.core.contracts import Button, ListRow, ListSection, OutMessage, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext


async def library_match(question: str, category: str | None) -> tuple[object | None, float]:
    best, score = None, 0.0
    for e in await repo.library_all(category):
        s = fuzz.token_set_ratio(question.lower(), e.question.lower())
        if s > score:
            best, score = e, s
    return (best, score) if score >= 85 else (None, score)


GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|menu|help|start|queue|हाय|नमस्ते|नमस्कार)\b", re.I)
# the spec a question is about, by its words: these facts lead the question to the expert
ASKS_ABOUT = [
    (r"dolby|hdr|vision|hlg", "hdr_formats"), (r"sound|speaker|audio|atmos|watt", "speaker_w"),
    (r"refresh|hz|gaming|game|motion", "refresh_hz"), (r"bright|nits|sunlight|dhoop", "brightness_nits"),
    (r"hdmi|arc|earc|soundbar", "hdmi_arc"), (r"wifi|wi-fi|bluetooth", "wifi"), (r"\bos\b|google|android|webos|tizen|apps?", "os"),
    (r"panel|qled|oled|led", "panel"), (r"\bton\b|cooling|room size", "ton"), (r"star|bijli|electricity|power|kwh|units?", "kwh_year"),
    (r"inverter", "inverter"), (r"noise|quiet|awaaz", "noise_db"), (r"\bkg\b|capacity|litre|liter", "capacity_kg"),
    (r"rpm|spin", "rpm"), (r"ram|memory", "ram_gb"), (r"battery", "battery_h"), (r"warranty|guarantee", "warranty"),
]


def _home_summary(ctx: SkillContext, lang: str | None = None, n: int = 4) -> str:
    """The customer's home in a few words, the facts that matter first: 'Living room · Sunlight · 8–10 ft · Wall'."""
    s = ctx.session
    if not s or not ctx.pack:
        return "–"
    lang = lang or ctx.lang
    po = ctx.pack.order
    order = [x for x in po if s.home.slots.get(x)] + [x for x in s.home.slots if x not in po]
    parts = []
    for sid in order:
        sd, vals = ctx.pack.slot(sid), s.home.slots.get(sid)
        if sd and vals:
            parts.append(", ".join(sd.value_title(v.value, lang) for v in vals))
        if len(parts) >= n:
            break
    return truncate(" · ".join(parts) or "–", 160)


def _product_for(ctx: SkillContext, question: str):
    """The product the question is about: named in it, else the label scanned, else the best pick."""
    s = ctx.session
    if not s or not ctx.pack:
        return None
    prods = ctx.facts.products_sync(ctx.pack.category)
    q = question.lower()
    best, score = None, 0.0
    for p in prods:
        sc = max(fuzz.token_set_ratio(p.model.lower(), q), fuzz.token_set_ratio((ctx.pick(p.title, "en") or "").lower(), q))
        if sc > score:
            best, score = p, sc
    if best is not None and score >= 85:
        return best
    by = {p.sku: p for p in prods}
    for sku in [s.extra.get("label_scanned")] + list(s.shortlist[:1]):
        if sku and sku in by:
            return by[sku]
    return None


def _product_facts(ctx: SkillContext, p, question: str, lang: str, n: int = 4) -> list[str]:
    """Up to four facts from the data, the ones the question is about first ('HDR: HDR10, HLG')."""
    from chanakya.skills import product_view as PV

    keys = [k for pat, k in ASKS_ABOUT if re.search(pat, question, re.I)]
    keys += [k for k in PV.KEY_SPECS.get(p.category, []) if k not in keys]
    out = []
    for k in keys:
        v = PV.fmt_value(ctx, p, k, lang, customer=False)
        if v:
            out.append(f"• {PV.label(k, lang, customer=False)}: {v}")
        if len(out) >= n:
            break
    return out


def _ago(ctx: SkillContext, at) -> str:
    mins = max(0, int((ctx.now - repo.aware(at)).total_seconds() // 60)) if at else 0
    return ctx.t("expert_ago_now") if mins < 1 else ctx.t("expert_ago_min", m=ctx.reg.num(mins, "calc:expert_age"))


async def home(ctx: SkillContext) -> Reply:
    """The expert's phone: the open questions as a list (asker, category, customer, how long ago, the question); a
    tap starts the answer. With none open, what this is and the library count."""
    mine = await repo.expert_q_open(ctx.actor_wa_id)
    unassigned = [q for q in await repo.expert_q_open() if not q.expert_wa_id]
    open_q = mine + [q for q in unassigned if q not in mine]
    lib = len(await repo.library_all(None))
    lib_line = ctx.t("expert_home_lib", n=ctx.reg.num(lib, "calc:library"))
    if not open_q:
        return ctx.reply(info_reply(ctx, ctx.t("expert_home_none") + "\n" + lib_line))
    rows = []
    for q in open_q[:10]:
        a = await repo.staff_by_wa(q.associate_wa_id)
        pack = ctx.packs.get(q.category)
        cat = ctx.pick(pack.label) if pack else (q.category or "").upper()
        title = " · ".join(x for x in ((a.name if a else "?"), cat, _ago(ctx, q.asked_at)) if x)
        rows.append(ListRow(id=encode("x", q.id, "answer"), title=truncate(title, 24), description=truncate(q.question, 72)))
    for r in rows:
        ctx.reg.raw(r.title + " " + (r.description or ""), "expert:queue")
    body = ctx.t("expert_home_some", n=ctx.reg.num(len(open_q), "calc:expert_open")) + "\n" + lib_line
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=body, list_button=truncate(ctx.t("list_expert_q"), 20),
                                sections=[ListSection(rows=rows)], allowed_numbers=ctx.reg.allowed()))


async def followup_hint(ctx: SkillContext) -> Reply:
    btns = [ctx.b("b_go", ctx.sid("go"))] if ctx.session else []
    return ctx.reply(info_reply(ctx, ctx.t("expert_follow_hint"), btns))


async def answer_from_library(ctx: SkillContext, entry) -> Reply:
    """Said plainly: this is a saved answer, by whom and when; the associate can still ask the expert."""
    from chanakya.truth import fmt

    ans = entry.answer or {}
    src = f"expert:library:{entry.id}"
    ctx.reg.raw(ans.get("for_associate", "") + " " + (ans.get("bolo") or "") + " " + entry.question, src)
    ctx.ev("expert_answered", sources=[src], qid=f"L{entry.id}", from_library=True)
    when = ctx.reg.raw(fmt.date_short(repo.aware(entry.created_at), ctx.lang), src) if entry.created_at else ""
    lines = [ctx.t("expert_library_answer", expert=entry.expert_name, date=when, text=ans.get("for_associate", ""))]
    if ans.get("bolo"):
        lines.append(bolo_block(ctx, ans["bolo"]))
    lines.append(ctx.t("expert_q_ctx", question=truncate(entry.question, 120)))
    body = "\n".join(lines)
    if ctx.session:
        ctx.session.extra["last_expert_q"] = entry.question
    btns = [ctx.b("b_go", ctx.sid("go") if ctx.session else encode("g", "status"))]
    if ctx.session:
        btns.append(ctx.b("b_expert_anyway", ctx.sid("expforce")))
    return ctx.reply(info_reply(ctx, body, btns))


async def ask(ctx: SkillContext, question: str, via_truth_check: bool = False, library_id: str | None = None,
              force: bool = False) -> Reply:
    s = ctx.session
    cat = s.home.category if s else (ctx.staff.dept if ctx.staff else None)
    if s and question:
        s.extra["last_expert_q"] = question[:500]
    if library_id and not force:
        e = await repo.library_get(int(str(library_id).lstrip("L"))) if str(library_id).lstrip("L").isdigit() else None
        if e:
            return await answer_from_library(ctx, e)
    if question and not force:
        e, _ = await library_match(question, cat)
        if e:
            return await answer_from_library(ctx, e)
    experts = [x for x in await repo.staff_by_role("expert") if x.wa_id != ctx.actor_wa_id]
    expert = next((x for x in experts if x.dept in (cat, "all")), experts[0] if experts else None)
    q = await repo.expert_q_create(
        session_id=s.id if s else None,
        associate_wa_id=ctx.actor_wa_id,
        expert_wa_id=expert.wa_id if expert else None,
        question=question[:500],
        category=cat,
        status="open",
        asked_at=ctx.now,
    )
    ctx.ev("expert_asked", qid=q.id)
    msgs: list[OutMessage] = []
    if via_truth_check:
        msgs.append(info_reply(ctx, ctx.t("not_in_data")))
    hold = ctx.pick(ctx.packs.common_line("no_data_holding"), ctx.clang)
    btns = [
        ctx.b("b_go", ctx.sid("go") if s else encode("g", "status")),
        ctx.b("b_status", encode("g", "status")),
        ctx.b("b_options", ctx.sid("options") if s else encode("g", "status")),
    ]
    cites = C.known_cites(ctx) if s else []
    if cites:
        msgs.append(coach_reply(ctx, hold, ctx.t("kyun_expert_hold"), cites, btns, do="do_expert_hold"))
    else:
        msgs.append(info_reply(ctx, bolo_block(ctx, hold) + "\n" + kyun_block(ctx, ctx.t("kyun_expert_hold")) + "\n"
                               + do_block(ctx, "do_expert_hold"), btns))
    if expert:
        elang = expert.lang or "en"
        ctx.reg.raw(question, "associate:question")
        body = ctx.t(
            "expert_question",
            elang,
            associate=ctx.staff.name if ctx.staff else "",
            cat=(ctx.pick(ctx.pack.label, elang) if ctx.pack else cat or ""),
            code=ctx.reg.raw(s.code, "session:code") if s else "-",
            question=question[:300],
        )
        prod = _product_for(ctx, question)
        if prod is not None:
            from chanakya.core.coach import fit_line

            facts = [f.lstrip("• ") for f in _product_facts(ctx, prod, question, elang)]
            body += "\n" + fit_line(facts, ctx.t("expert_q_product", elang, name=C.product_title(ctx, prod, elang)))
        home_footer = None
        if s and s.home.slots:  # the customer's home, in the grey footer (60 characters)
            home_footer = truncate(ctx.t("expert_home_line", elang, home=ctx.reg.static(_home_summary(ctx, elang))).replace("*", ""), 60)
        msgs.append(
            info_reply(
                ctx,
                body,
                [
                    Button(id=encode("x", q.id, "answer"), title=ctx.t("b_expert_answer", elang)),
                    Button(id=encode("x", q.id, "dunno"), title=ctx.t("b_expert_dunno", elang)),
                ],
                to=expert.wa_id,
            )
        )
        msgs[-1].footer = home_footer
        ctx.reg.raw(str(q.id), "expert:qid")
    return ctx.reply(*msgs)


async def expert_message(ctx: SkillContext) -> Reply:
    """An expert's text/voice while a question is open is the answer to the one they tapped, else the oldest one.
    A greeting (or nothing open) shows the queue."""
    open_q = await repo.expert_q_open(ctx.actor_wa_id)
    picked = await repo.setting_get(f"expert_answering:{ctx.actor_wa_id}")
    text = ctx.inbound.text or ""
    if not open_q or (ctx.inbound.type == "text" and not picked and GREETING.match(text) and len(text.split()) <= 3):
        return await home(ctx)
    q = next((x for x in open_q if x.id == picked), open_q[0])  # the question they tapped 'answer' on
    await repo.setting_set(f"expert_answering:{ctx.actor_wa_id}", None)
    assoc = await repo.staff_by_wa(q.associate_wa_id)
    alang = assoc.lang if assoc else "hi"
    s = await ctx.svc.sessions.get(q.session_id) if q.session_id else None
    clang = s.home.customer_lang if s else alang
    audio, amime = None, None
    if ctx.inbound.type == "audio" and ctx.inbound.media_id:
        audio, amime = await ctx.svc.channel.download_media(ctx.inbound.media_id)
    try:
        ans = await ctx.ai.format_expert_answer(ctx.inbound.text, audio, alang, clang, mime=amime)
    except AIUnavailable:
        if audio is not None:  # no transcription model: forward the expert's voice note as-is
            mid = await ctx.svc.channel.upload_media(audio, "audio/ogg", "expert.ogg")
            await repo.expert_q_update(q.id, status="answered", answered_at=ctx.now, answer={"voice": True})
            ctx.ev("expert_answered", qid=q.id, voice=True)
            ctx.session = s
            return ctx.reply(
                info_reply(ctx, ctx.t("expert_voice_answer", alang, expert=ctx.staff.name), to=q.associate_wa_id),
                OutMessage(to=q.associate_wa_id, kind="audio", media_id=mid, voice=True),
                info_reply(ctx, ctx.t("expert_thanks")),
            )
        raise
    src = f"expert:{q.id}"
    for n in ans.numbers_used:
        ctx.reg.raw(n, src)
    ctx.reg.raw(ans.for_associate + " " + (ans.bolo or ""), src)
    await repo.expert_q_update(q.id, status="answered", answered_at=ctx.now, answer=ans.model_dump())
    ctx.ev("expert_answered", sources=[src], qid=q.id)
    ctx.reg.static(q.question)
    pack = ctx.packs.get(q.category)
    who = f"{ctx.staff.name} ({ctx.pick(pack.label, alang)})" if pack else ctx.staff.name
    lines = [ctx.t("expert_answer", alang, expert=who, text=ans.for_associate)]
    if ans.bolo:
        lines.append(bolo_block(ctx, ans.bolo, alang))
    lines.append(ctx.t("expert_q_ctx", alang, question=truncate(q.question, 120)))
    body = "\n".join(lines)
    nxt = [Button(id=encode("s", s.code, "go"), title=ctx.t("b_go", alang)),
           Button(id=encode("s", s.code, "expfollow"), title=ctx.t("b_expert_follow", alang))] if s else []
    to_assoc = info_reply(ctx, body, nxt, to=q.associate_wa_id)
    to_assoc.session_id = s.id if s else None
    name = assoc.name if assoc else ""
    to_expert = info_reply(
        ctx,
        ctx.t("expert_sent", name=name) + "\n" + ctx.t("expert_save_ask"),
        [
            Button(id=encode("x", q.id, "save"), title=ctx.t("b_library_save")),
            Button(id=encode("x", q.id, "nosave"), title=ctx.t("b_no")),
        ],
    )
    return ctx.reply(to_assoc, to_expert)


async def expert_button(ctx: SkillContext, qid: str, action: str) -> Reply:
    if action == "list":
        return await home(ctx)
    q = await repo.expert_q_get(int(qid)) if str(qid).isdigit() else None
    if q is None:
        return await home(ctx)
    if action == "answer":
        if q.status != "open":
            return await home(ctx)
        await repo.setting_set(f"expert_answering:{ctx.actor_wa_id}", q.id)
        if not q.expert_wa_id:
            await repo.expert_q_update(q.id, expert_wa_id=ctx.actor_wa_id)  # picked from the shared queue
        a = await repo.staff_by_wa(q.associate_wa_id)
        ctx.reg.static(q.question)
        return ctx.reply(info_reply(ctx, ctx.t("expert_answering", name=a.name if a else "", question=truncate(q.question, 200))
                                    + "\n" + ctx.t("expert_answer_prompt")))
    if action == "dunno":
        await repo.expert_q_update(q.id, status="expired")
        assoc = await repo.staff_by_wa(q.associate_wa_id)
        s = await ctx.svc.sessions.get(q.session_id) if q.session_id else None
        alang = assoc.lang if assoc else "hi"
        btns = [Button(id=encode("s", s.code, "task"), title=ctx.t("b_task", alang))] if s else []
        m = info_reply(ctx, ctx.t("expert_unknown", alang), btns, to=q.associate_wa_id)
        m.session_id = s.id if s else None
        return ctx.reply(info_reply(ctx, ctx.t("expert_thanks_dunno")), m)
    if action == "save" and q.answer and not q.answer.get("voice"):
        e = await repo.library_add(
            category=q.category, question=q.question, answer=q.answer, expert_name=ctx.staff.name, approved=True
        )
        ctx.ev("library_saved", qid=q.id, library_id=e.id)
        n = ctx.reg.num(len(await repo.library_all(None)), "calc:library")
        return ctx.reply(info_reply(ctx, ctx.t("library_saved", n=n), [Button(id=encode("x", 0, "list"), title=ctx.t("b_expert_queue"))]))
    if action == "list":
        return await home(ctx)
    return ctx.reply(info_reply(ctx, ctx.t("expert_thanks"), [Button(id=encode("x", 0, "list"), title=ctx.t("b_expert_queue"))]))


async def sla_tick(now, send) -> None:
    """90 s → remind the expert once and tell the associate; 15 min → expire."""
    from chanakya.i18n.lang import t

    told: set[tuple[str, str | None]] = set()  # one 'expert is busy' per associate and customer per tick
    for q in await repo.expert_q_open():
        age = (now - repo.aware(q.asked_at)).total_seconds()
        assoc = await repo.staff_by_wa(q.associate_wa_id)
        alang = assoc.lang if assoc else "hi"
        if age >= 900:
            await repo.expert_q_update(q.id, status="expired")
            await send(OutMessage(to=q.associate_wa_id, kind="text",
                                  body=t("expert_expired", alang, question=q.question[:80])))
            continue
        if age >= 90 and not q.reminded and (q.associate_wa_id, q.session_id) in told:
            await repo.expert_q_update(q.id, reminded=True)
            continue
        if age >= 90 and not q.reminded:
            told.add((q.associate_wa_id, q.session_id))
            await repo.expert_q_update(q.id, reminded=True)
            if q.expert_wa_id:  # the question itself, and the two answers, one tap away
                ex = await repo.staff_by_wa(q.expert_wa_id)
                elang = ex.lang if ex else "hi"
                body = t("expert_reminder", elang, name=assoc.name if assoc else "", m=str(int(age // 60)),
                         question=truncate(q.question, 200))
                await send(OutMessage(to=q.expert_wa_id, kind="buttons", body=body, buttons=[
                    Button(id=encode("x", q.id, "answer"), title=t("b_expert_answer", elang)),
                    Button(id=encode("x", q.id, "dunno"), title=t("b_expert_dunno", elang))]))
            s_code = None
            if q.session_id:
                from chanakya.core.session import SessionRepo

                st = await SessionRepo().get(q.session_id)
                s_code = st.code if st else None
            btns = [Button(id=encode("s", s_code, "task"), title=t("b_task", alang))] if s_code else []
            await send(
                OutMessage(
                    to=q.associate_wa_id, kind="buttons" if btns else "text", body=t("expert_busy", alang), buttons=btns
                )
            )
    _ = P
