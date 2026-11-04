"""Visheshagya — Ask an expert: library first, else route to an expert, hold, answer, save to library."""

from __future__ import annotations

from rapidfuzz import fuzz

from chanakya.ai.client import AIUnavailable
from chanakya.brain import packs as P
from chanakya.channel.limits import truncate
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import bolo_block, coach_reply, do_block, info_reply, kyun_block
from chanakya.core.contracts import Button, OutMessage, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext


async def library_match(question: str, category: str | None) -> tuple[object | None, float]:
    best, score = None, 0.0
    for e in await repo.library_all(category):
        s = fuzz.token_set_ratio(question.lower(), e.question.lower())
        if s > score:
            best, score = e, s
    return (best, score) if score >= 85 else (None, score)


def _home_summary(ctx: SkillContext) -> str:
    s = ctx.session
    if not s or not ctx.pack:
        return "–"
    parts = []
    for sid, vals in s.home.slots.items():
        sd = ctx.pack.slot(sid)
        if sd and vals:
            parts.append(f"{ctx.pick(sd.title)}: {', '.join(sd.value_title(v.value, ctx.lang) for v in vals)}")
    return truncate("; ".join(parts) or "–", 200)


async def answer_from_library(ctx: SkillContext, entry) -> Reply:
    ans = entry.answer or {}
    src = f"expert:library:{entry.id}"
    ctx.reg.raw(ans.get("for_associate", "") + " " + (ans.get("bolo") or ""), src)
    ctx.ev("expert_answered", sources=[src], qid=f"L{entry.id}", from_library=True)
    body = ctx.t("expert_library_answer", expert=entry.expert_name, text=ans.get("for_associate", ""))
    if ans.get("bolo"):
        body += "\n\n" + bolo_block(ctx, ans["bolo"])
    btns = [
        ctx.b("b_go", ctx.sid("go") if ctx.session else encode("g", "status")),
        ctx.b("b_status", encode("g", "status")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


async def ask(ctx: SkillContext, question: str, via_truth_check: bool = False, library_id: str | None = None) -> Reply:
    s = ctx.session
    cat = s.home.category if s else (ctx.staff.dept if ctx.staff else None)
    if library_id:
        e = await repo.library_get(int(str(library_id).lstrip("L"))) if str(library_id).lstrip("L").isdigit() else None
        if e:
            return await answer_from_library(ctx, e)
    if question:
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
        msgs.append(info_reply(ctx, bolo_block(ctx, hold) + "\n\n" + kyun_block(ctx, ctx.t("kyun_expert_hold")) + "\n\n"
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
        body += "\n" + ctx.t("expert_home_line", elang, home=ctx.reg.static(_home_summary(ctx)))
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
        ctx.reg.raw(str(q.id), "expert:qid")
    return ctx.reply(*msgs)


async def expert_message(ctx: SkillContext) -> Reply:
    """An expert's text/voice while a question is open is the answer to the oldest open question."""
    open_q = await repo.expert_q_open(ctx.actor_wa_id)
    if not open_q:
        return ctx.reply(info_reply(ctx, ctx.t("expert_idle")))
    picked = await repo.setting_get(f"expert_answering:{ctx.actor_wa_id}")
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
    body = ctx.t("expert_answer", alang, expert=ctx.staff.name, text=ans.for_associate)
    body = f"❓ {q.question[:90]}\n{body}"  # which question this answers
    if ans.bolo:
        body += "\n\n" + bolo_block(ctx, ans.bolo, alang)
    nxt = [Button(id=encode("s", s.code, "go"), title=ctx.t("b_go", alang))] if s else []
    to_assoc = info_reply(ctx, body, nxt, to=q.associate_wa_id)
    to_assoc.session_id = s.id if s else None
    to_expert = info_reply(
        ctx,
        ctx.t("expert_save_ask"),
        [
            Button(id=encode("x", q.id, "save"), title=ctx.t("b_library_save")),
            Button(id=encode("x", q.id, "nosave"), title=ctx.t("b_no")),
        ],
    )
    return ctx.reply(to_assoc, to_expert)


async def expert_button(ctx: SkillContext, qid: str, action: str) -> Reply:
    q = await repo.expert_q_get(int(qid))
    if q is None:
        return ctx.reply(info_reply(ctx, ctx.t("expert_idle")))
    if action == "answer":
        await repo.setting_set(f"expert_answering:{ctx.actor_wa_id}", q.id)
        ctx.reg.static(q.question)
        return ctx.reply(info_reply(ctx, f"{ctx.t('expert_answer_prompt')}\n❓ {q.question[:120]}"))
    if action == "dunno":
        await repo.expert_q_update(q.id, status="expired")
        assoc = await repo.staff_by_wa(q.associate_wa_id)
        return ctx.reply(
            info_reply(ctx, ctx.t("expert_thanks")),
            info_reply(ctx, ctx.t("expert_unknown", assoc.lang if assoc else "hi"), to=q.associate_wa_id),
        )
    if action == "save" and q.answer and not q.answer.get("voice"):
        e = await repo.library_add(
            category=q.category, question=q.question, answer=q.answer, expert_name=ctx.staff.name, approved=True
        )
        ctx.ev("library_saved", qid=q.id, library_id=e.id)
        return ctx.reply(info_reply(ctx, ctx.t("library_saved")))
    return ctx.reply(info_reply(ctx, ctx.t("expert_thanks")))


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
            if q.expert_wa_id:
                ex = await repo.staff_by_wa(q.expert_wa_id)
                await send(
                    OutMessage(to=q.expert_wa_id, kind="text", body=t("expert_reminder", ex.lang if ex else "hi"))
                )
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
