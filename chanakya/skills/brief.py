"""Step 1 — Taiyari (Before the shift): brief card, 45-s clip (or tip), one quiz question."""

from __future__ import annotations

from datetime import timedelta

from chanakya.channel.limits import truncate_title
from chanakya.config import ROOT
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.clock import today
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, OutMessage, Reply
from chanakya.skills.base import SkillContext
from chanakya.skills.review import STEPS
from chanakya.truth import fmt

CLIPS = ROOT / "data" / "clips"
ICON = {"back_in_stock": "🟢", "out_of_stock": "🔴", "low": "🟡"}


async def focus_step(ctx: SkillContext) -> tuple[str, int | None]:
    rows = await repo.scores_for(ctx.actor_wa_id, limit=5)
    if not rows:
        return "translate", None
    avg = {st: sum((r.steps or {}).get(st, 0) for r in rows) / len(rows) for st in STEPS}
    weakest = min(STEPS, key=lambda st: (avg[st], STEPS.index(st)))
    return weakest, (rows[0].steps or {}).get(weakest)


async def brief(ctx: SkillContext) -> Reply:
    staff = ctx.staff
    msgs: list[OutMessage] = []
    if staff.last_brief_date is None and staff.streak == 0 and not ctx.scratch.get("welcomed"):
        msgs.append(info_reply(ctx, ctx.t("welcome_new_staff", name=staff.name)))
    dept = staff.dept or "tv"
    changes = await ctx.facts.stock_changes(staff.store_id, dept)
    change_rows = []
    for c in changes[:4]:
        key = {"back_in_stock": "brief_back", "out_of_stock": "brief_out", "low": "brief_low"}[c["kind"]]
        change_rows.append(
            {
                "icon": ICON[c["kind"]],
                "text": ctx.t(key, model=ctx.reg.raw(c["model"], c["source"]), qty=ctx.reg.num(c["qty"], c["source"])),
            }
        )
    d = today()
    offer_rows = []
    for o in await ctx.facts.all_offers(d):
        cats = o.categories or []
        if dept not in ("all",) and dept not in cats and not (dept == "tv" and "soundbar" in cats):
            continue
        if (o.valid_to - d).days <= 3:
            offer_rows.append(
                {
                    "text": ctx.t(
                        "brief_offer_ends",
                        title=ctx.pick(o.title),
                        date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.lang), f"offer:{o.id}"),
                    )
                }
            )
        elif (d - o.valid_from).days <= 3:
            offer_rows.append({"text": ctx.t("brief_offer_new", title=ctx.pick(o.title))})
    tasks = [t for t in await repo.tasks_open(ctx.actor_wa_id) if t.due_at and repo.aware(t.due_at).date() <= d]
    focus, last = await focus_step(ctx)
    step_name = ctx.t(f"step_{focus}")
    followups = (
        ctx.t("brief_followups", n=ctx.reg.num(len(tasks), "tasks:today")) if tasks else ctx.t("brief_no_followups")
    )
    focus_text = (
        ctx.t("brief_focus_last", step=step_name, n=ctx.reg.num(last, "score:last"))
        if last is not None
        else ctx.t("brief_focus", step=step_name)
    )
    card = Card(
        template="brief",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "greeting": ctx.t("brief_greeting", name=staff.name),
            "niti_line": ctx.t("niti_brief"),
            "changes": change_rows,
            "offers": offer_rows[:3],
            "followups_text": followups,
            "focus_text": focus_text,
        },
    )
    cat_label = ctx.pick(ctx.packs.get(dept).label) if ctx.packs.get(dept) else dept.upper()
    line1 = ctx.t("brief_caption", name=staff.name, cat=cat_label, n=ctx.reg.num(len(changes), "stock:changes"))
    if tasks:
        line1 += " " + ctx.t("brief_caption_tasks", n=ctx.reg.num(len(tasks), "tasks:today"))
    caption = "\n".join([line1, focus_text])
    btns = [
        ctx.b("b_clip", encode("g", "clip", focus)),
        ctx.b("b_quiz", encode("g", "quiz", focus)),
        ctx.b("b_new_customer", encode("g", "new")),
    ]
    msgs.append(info_reply(ctx, caption, btns, card=card))
    staff.last_brief_date = d
    return ctx.reply(*msgs)


async def clip(ctx: SkillContext, step: str) -> Reply:
    f = CLIPS / f"{step}.mp4"
    btns = [ctx.b("b_quiz", encode("g", "quiz", step)), ctx.b("b_new_customer", encode("g", "new"))]
    if f.exists():
        mid = await ctx.svc.channel.upload_media(f.read_bytes(), "video/mp4", f.name)
        return ctx.reply(
            OutMessage(
                to=ctx.actor_wa_id,
                kind="video",
                media_id=mid,
                caption=ctx.t("clip_caption", step=ctx.t(f"step_{step}")),
            ),
            info_reply(ctx, ctx.t("after_clip"), btns),
        )
    key = f"tip_{step}" if f"tip_{step}" else "tip_translate"
    return ctx.reply(info_reply(ctx, ctx.t("tip_card", step=ctx.t(f"step_{step}"), tip=ctx.t(key)), btns))


def _quiz_for(ctx: SkillContext, step: str):
    pack = ctx.packs.get(ctx.staff.dept if ctx.staff and ctx.staff.dept in ctx.packs.packs else "tv")
    qs = pack.quiz if pack else []
    return next((q for q in qs if q.step == step), qs[0] if qs else None)


async def quiz(ctx: SkillContext, step: str) -> Reply:
    q = _quiz_for(ctx, step)
    if q is None:
        return ctx.reply(info_reply(ctx, ctx.t("tip_" + step if step in STEPS else "tip_translate")))
    btns = [Button(id=encode("g", "qa", q.id, a.id), title=truncate_title(ctx.pick(a.title))) for a in q.answers[:3]]
    return ctx.reply(info_reply(ctx, ctx.pick(q.q), btns))


async def answer(ctx: SkillContext, qid: str, aid: str) -> Reply:
    q = None
    for p in ctx.packs.packs.values():
        q = next((x for x in p.quiz if x.id == qid), None) or q
    if q is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    explain = ctx.pick(q.explain)
    if aid == q.correct:
        ctx.staff.streak = (ctx.staff.streak or 0) + 1
        body = ctx.t("quiz_right", explain=explain, streak=ctx.reg.num(ctx.staff.streak, "staff:streak"))
    else:
        right = next((a for a in q.answers if a.id == q.correct), None)
        body = ctx.t("quiz_wrong", answer=ctx.pick(right.title) if right else "", explain=explain)
    return ctx.reply(info_reply(ctx, body, [ctx.b("b_new_customer", encode("g", "new"))]))


def help_reply(ctx: SkillContext) -> Reply:
    btns = [ctx.b("b_new_customer", encode("g", "new")), ctx.b("b_status", encode("g", "status"))]
    _ = timedelta
    return ctx.reply(info_reply(ctx, ctx.t("help_body"), btns))
