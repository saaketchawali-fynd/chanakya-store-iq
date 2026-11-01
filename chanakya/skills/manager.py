"""Sabha — department manager view on WhatsApp: Floor, Digest, Team, Approvals, coaching nudges."""

from __future__ import annotations

import statistics
from datetime import timedelta

from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, ListRow, ListSection, OutMessage, Reply
from chanakya.skills.base import SkillContext
from chanakya.skills.review import STEPS
from chanakya.truth import fmt


async def _today_data(ctx: SkillContext) -> dict:
    start = ctx.now.replace(hour=0, minute=0, second=0, microsecond=0)
    sessions = await ctx.svc.sessions.since(start - timedelta(hours=6))
    sessions = [(r, st) for r, st in sessions if (repo.aware(r.created_at) or start) >= start]
    scores = [sc for sc in await repo.scores_for(limit=500) if (repo.aware(sc.at) or start) >= start]
    events = await repo.events_for(since=start)
    staff = {s.wa_id: s for s in await repo.staff_all()}
    return {"start": start, "sessions": sessions, "scores": scores, "events": events, "staff": staff}


async def floor(ctx: SkillContext) -> Reply:
    d = await _today_data(ctx)
    active = [st for r, st in d["sessions"] if r.status == "active"]
    waiting_demo = [st for st in active if st.shortlist and not st.demo_done]
    expert_open = await repo.expert_q_open()
    totals = [sc.total for sc in d["scores"]]
    g = sum(1 for t in totals if t >= 11)
    med = statistics.median(totals) if totals else 0
    tasks = [t for t in await repo.tasks_open() if repo.aware(t.due_at).date() <= ctx.now.date()]
    risk = [
        j
        for j in await repo.install_all(["created", "reminded", "due", "problem"])
        if (j.site_needs or {}).get("_risks") or j.status == "problem"
    ]
    n = ctx.reg.num
    body = "\n".join(
        [
            ctx.t(
                "floor_now",
                a=n(len(active), "calc:floor"),
                d=n(len(waiting_demo), "calc:floor"),
                e=n(len(expert_open), "calc:floor"),
            ),
            ctx.t(
                "floor_today",
                s=n(len(d["sessions"]), "calc:floor"),
                g=n(g, "calc:floor"),
                n=n(len(totals), "calc:floor"),
                m=n(med, "calc:floor"),
            ),
            ctx.t("floor_follow", t=n(len(tasks), "calc:floor"), r=n(len(risk), "calc:floor")),
        ]
    )
    ctx.reg.static("14")
    btns = [
        Button(id=encode("m", "digest"), title=ctx.t("b_digest")),
        Button(id=encode("m", "team"), title=ctx.t("b_team")),
        Button(id=encode("m", "approvals"), title=ctx.t("b_approvals")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


async def digest(ctx: SkillContext, to: str | None = None) -> Reply:
    d = await _today_data(ctx)
    n = ctx.reg.num
    totals = [sc.total for sc in d["scores"]]
    guide = sum(1 for t in totals if t >= 11)
    ev = d["events"]
    pf = [e for e in ev if e.type == "price_first_by" and not (e.payload or {}).get("from_review")]
    pf_cust = sum(1 for e in pf if (e.payload or {}).get("who") == "customer")
    pf_assoc = sum(1 for e in pf if (e.payload or {}).get("who") == "associate")
    minutes = [(e.payload or {}).get("minute") for e in pf if (e.payload or {}).get("minute") is not None]
    parivaar = sum(1 for e in ev if e.type == "parivaar_sent")
    inst_ok = sum(1 for e in ev if e.type == "install_confirmed")
    inst_bad = sum(1 for e in ev if e.type == "install_problem")
    expert_open = len(await repo.expert_q_open())
    kpis = [
        {"label": ctx.t("kpi_conversations"), "value_str": n(len(d["sessions"]), "calc:digest")},
        {"label": ctx.t("kpi_guide"), "value_str": f"{n(guide, 'calc:digest')}/{n(len(totals), 'calc:digest')}"},
        {
            "label": ctx.t("kpi_median"),
            "value_str": f"{n(statistics.median(totals) if totals else 0, 'calc:digest')}/14",
        },
        {
            "label": ctx.t("kpi_price_first"),
            "value_str": ctx.t("kpi_price_first_val", c=n(pf_cust, "calc:digest"), a=n(pf_assoc, "calc:digest")),
        },
        {"label": ctx.t("kpi_minute"), "value_str": n(statistics.median(minutes), "calc:digest") if minutes else "–"},
        {"label": ctx.t("kpi_parivaar"), "value_str": n(parivaar, "calc:digest")},
        {"label": ctx.t("kpi_install"), "value_str": f"{n(inst_ok, 'calc:digest')} ok · {n(inst_bad, 'calc:digest')} issue"},
        {"label": ctx.t("kpi_expert_open"), "value_str": n(expert_open, "calc:digest")},
    ]
    ctx.reg.static("14")
    by_assoc: dict[str, list] = {}
    for sc in d["scores"]:
        by_assoc.setdefault(sc.associate_wa_id, []).append(sc)
    names, cells = [], []
    worst = None
    for wa, rows in by_assoc.items():
        st = d["staff"].get(wa)
        names.append(st.name if st else wa[-4:])
        avg = {s: round(sum((r.steps or {}).get(s, 0) for r in rows) / len(rows), 1) for s in STEPS}
        cells.append([avg[s] for s in STEPS])
        low = min(STEPS, key=lambda s: avg[s])
        if worst is None or avg[low] < worst[2]:
            worst = (wa, st.name if st else "", avg[low], low)
    if worst:
        gaps = ctx.t("digest_gap", name=worst[1], step=ctx.t(f"step_{worst[3]}"))
    else:
        gaps = ctx.t("digest_no_data")
    card = Card(
        template="digest",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "kpis": kpis,
            "heatmap": {"associates": names, "steps": [ctx.t(f"step_{s}") for s in STEPS], "cells": cells},
            "gaps_text": gaps,
        },
    )
    btns = []
    if worst:
        btns = [Button(id=encode("m", "nudge", worst[0], worst[3]), title=ctx.t("b_send_nudge"))]
    m = info_reply(ctx, gaps, btns, card=card, to=to)
    return ctx.reply(m)


async def team(ctx: SkillContext) -> Reply:
    d = await _today_data(ctx)
    rows = []
    for st in d["staff"].values():
        if st.role != "associate" and st.base_role != "associate":
            continue
        mine = [sc.total for sc in d["scores"] if sc.associate_wa_id == st.wa_id]
        sess = sum(1 for r, _ in d["sessions"] if r.associate_wa_id == st.wa_id)
        desc = ctx.t(
            "team_row",
            s=ctx.reg.num(sess, "calc:team"),
            m=ctx.reg.num(statistics.median(mine), "calc:team") if mine else "–",
        )
        rows.append(
            ListRow(id=encode("m", "who", st.wa_id), title=truncate(st.name, 24), description=truncate(desc, 72))
        )
    ctx.reg.static("14")
    if not rows:
        return ctx.reply(info_reply(ctx, ctx.t("digest_no_data")))
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="list",
            body=ctx.t("team_body"),
            list_button=ctx.t("b_team"),
            sections=[ListSection(rows=rows[:10])],
            allowed_numbers=ctx.reg.allowed(),
        )
    )


async def approvals(ctx: SkillContext) -> Reply:
    tasks = await repo.tasks_open(ctx.actor_wa_id, "approval")
    if not tasks:
        return ctx.reply(info_reply(ctx, ctx.t("no_approvals")))
    t = tasks[0]
    p = t.payload or {}
    staff = await repo.staff_by_wa(p.get("associate", ""))
    body = ctx.t(
        "approval_for_manager", name=staff.name if staff else "", code=ctx.reg.raw(p.get("code", ""), "session:code")
    )
    btns = [
        Button(id=encode("m", "appr", t.id, "offers"), title=ctx.t("b_offer_dikhao")),
        Button(id=encode("m", "appr", t.id, "no"), title=ctx.t("b_mana_karo")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


async def approval_action(ctx: SkillContext, task_id: str, action: str) -> Reply:
    t = await repo.task_get(int(task_id))
    if t is None or t.status != "open":
        return ctx.reply(info_reply(ctx, ctx.t("no_approvals")))
    p = t.payload or {}
    assoc = p.get("associate")
    staff = await repo.staff_by_wa(assoc) if assoc else None
    alang = staff.lang if staff else "hi"
    await repo.task_update(t.id, status="done")
    if action == "no":
        return ctx.reply(
            info_reply(ctx, ctx.t("approval_closed")),
            OutMessage(to=assoc, kind="text", body=ctx.t("approval_denied", alang)),
        )
    # Managers can only point to offers that exist in the data.
    from chanakya.core.clock import today

    s = await ctx.svc.sessions.get(t.session_id)
    cat = s.home.category if s else "tv"
    offs = await ctx.facts.offers(cat or "tv", today())
    lines = [
        f"{ctx.pick(o.title, alang)} ({ctx.reg.raw(fmt.date_short(o.valid_to, alang), f'offer:{o.id}')})"
        for o in offs[:2]
    ]
    body = ctx.t("approval_offers", alang, offers=ctx.reg.static("; ".join(lines) or "–"))
    btns = [Button(id=encode("s", s.code, "offers"), title=truncate_title(ctx.t("b_offers", alang)))] if s else []
    return ctx.reply(info_reply(ctx, ctx.t("approval_closed")), info_reply(ctx, body, btns, to=assoc))


async def nudge(ctx: SkillContext, wa_id: str, step: str) -> Reply:
    staff = await repo.staff_by_wa(wa_id)
    if staff is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    lang = staff.lang or "hi"
    body = ctx.t("nudge_body", lang, step=ctx.t(f"step_{step}", lang), tip=ctx.t(f"tip_{step}", lang))
    btns = [
        Button(id=encode("g", "clip", step), title=ctx.t("b_clip", lang)),
        Button(id=encode("g", "quiz", step), title=ctx.t("b_quiz", lang)),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("nudge_sent", name=staff.name)), info_reply(ctx, body, btns, to=wa_id))
