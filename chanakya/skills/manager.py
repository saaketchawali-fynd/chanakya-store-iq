"""Sabha — department manager view on WhatsApp: Floor, Digest, Team, Approvals, coaching nudges."""

from __future__ import annotations

import statistics
from datetime import timedelta

from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, ListRow, ListSection, OutMessage, Reply
from chanakya.core.status import short_name
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


# the October store visits (Store_Visit_Findings_Reference_27Oct2026): Reliance Digital, 6 scored conversations
BASELINE = {"median": 6, "price_assoc": (6, 6), "questions": 2}
# where a customer is → the step to coach next (the step's own tip and clip)
COACH_STEP = {"assess": "assess", "recommend": "translate", "demonstrated": "complete", "complete": "close",
              "close": "close", "follow": "follow_through", "closed": "follow_through"}


def _mins(ctx: SkillContext, at) -> int:
    return max(0, int((ctx.now - (repo.aware(at) or ctx.now)).total_seconds() // 60))


def _price_first(events: list) -> dict[str, dict]:
    """One answer per customer: the live one (at the moment the price came up) first, else the end-of-customer one."""
    out: dict[str, dict] = {}
    for e in events:
        if e.type != "price_first_by" or not e.session_id:
            continue
        pl = e.payload or {}
        cur = out.get(e.session_id)
        if cur is None or (cur.get("from_review") and not pl.get("from_review")):
            out[e.session_id] = pl
    return out


def _questions_before_product(events: list) -> list[int]:
    """Home facts the associate learnt before the first options (asked by a question button or heard in a voice
    note), per customer who reached the options: the reference's 'questions before the first product'."""
    learnt: dict[str, set] = {}
    done: dict[str, int] = {}
    for e in events:
        sid = e.session_id
        if not sid or sid in done:
            continue
        if e.type in ("slot_filled", "data_question_answered"):
            learnt.setdefault(sid, set()).add((e.payload or {}).get("slot") or (e.payload or {}).get("key") or e.id)
        elif e.type == "options_shown":
            done[sid] = len(learnt.get(sid, ()))
    return list(done.values())


def _cell(v: float):
    return int(v) if float(v).is_integer() else v


async def floor(ctx: SkillContext) -> Reply:
    """'Over the shoulder': the live customers as a list (associate, customer, category, step, minutes); a tap shows
    that customer's Home Card and a tip for the associate. Above it, the floor and today in numbers."""
    d = await _today_data(ctx)
    live = [(r, st) for r, st in d["sessions"] if r.status == "active"]
    active = [st for _, st in live]
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
    body = "\n\n".join(  # sections: right now, then today (with the follow-ups and install risks)
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
            )
            + "\n"
            + ctx.t("floor_follow", t=n(len(tasks), "calc:floor"), r=n(len(risk), "calc:floor")),
        ]
    )
    ctx.reg.static("14")
    if not live:
        btns = [
            Button(id=encode("m", "digest"), title=ctx.t("b_huddle")),
            Button(id=encode("m", "team"), title=ctx.t("b_team")),
            Button(id=encode("m", "approvals"), title=ctx.t("b_approvals")),
        ]
        return ctx.reply(info_reply(ctx, body, btns))
    rows = []
    for r, st in sorted(live, key=lambda x: repo.aware(x[0].created_at) or ctx.now)[:9]:
        who = d["staff"].get(st.associate_wa_id)
        name = short_name(st.home.customer_name)
        cust = f"{st.code} {name}" if name and name != "–" else st.code
        pack = ctx.packs.get(st.home.category)
        step = COACH_STEP.get(getattr(st.step, "value", str(st.step)), "assess")
        desc = ctx.t("floor_row", cat=ctx.pick(pack.label) if pack and st.home.category else ctx.t("floor_no_cat"), step=ctx.t(f"step_{step}"),
                     demo=ctx.t("st_demo_done" if st.demo_done else "st_demo_pending"),
                     m=n(_mins(ctx, r.created_at), "calc:floor"))
        title = f"{who.name if who else '?'} · {cust}"
        ctx.reg.raw(title, "session:code")
        rows.append(ListRow(id=encode("m", "floorc", st.id), title=truncate(title, 24), description=truncate(desc, 72)))
    rows.append(ListRow(id=encode("m", "digest"), title=truncate(ctx.t("mgr_row_digest"), 24),
                        description=truncate(ctx.t("mgr_row_digest_d"), 72)))
    ctx.reg.static(" ".join(r.title + " " + (r.description or "") for r in rows[-1:]))
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=body + "\n\n" + ctx.t("floor_tap"),
                                list_button=truncate_title(ctx.t("list_floor")), sections=[ListSection(rows=rows)],
                                allowed_numbers=ctx.reg.allowed()))


async def floor_customer(ctx: SkillContext, session_id: str | None) -> Reply:
    """One live customer, over the associate's shoulder: their Home Card, where they stand, and the step's tip,
    one tap from the associate's phone."""
    row = next(((r, st) for r, st in await ctx.svc.sessions.since(ctx.now - timedelta(hours=14)) if st.id == session_id), None)
    s = row[1] if row else None
    if row is None or row[0].status != "active":
        return ctx.reply(info_reply(ctx, ctx.t("floor_c_gone"), [Button(id=encode("m", "floor"), title=ctx.t("b_floor"))]))
    from chanakya.core.status import status_line
    from chanakya.skills import common as C

    who = await repo.staff_by_wa(s.associate_wa_id)
    mine = ctx.session
    ctx.session = s  # read only: the manager's context never saves the associate's customer
    try:
        msg = C.home_card_message(ctx, force=True)
        parts = [x for x in (status_line(ctx) or "").split(" · ") if x]
    finally:
        ctx.session = mine
    step = COACH_STEP.get(getattr(s.step, "value", str(s.step)), "assess")
    pack = ctx.packs.get(s.home.category)
    lines = [ctx.t("floor_c_head", assoc=who.name if who else "?", who=parts[0] if parts else s.code,
                   cat=ctx.pick(pack.label) if pack and s.home.category else ctx.t("floor_no_cat")), ""]
    lines += [f"• {x}" for x in parts[2:]]
    lines.append(ctx.t("floor_c_step", step=ctx.t(f"step_{step}"), m=ctx.reg.num(_mins(ctx, row[0].created_at), "calc:floor")))
    lines += ["", ctx.t("tip_line", tip=ctx.t(f"tip_{step}"))]
    btns = [Button(id=encode("m", "tip", s.associate_wa_id, step), title=ctx.t("b_send_tip")),
            Button(id=encode("m", "floor"), title=ctx.t("b_floor")),
            Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))]
    out = info_reply(ctx, "\n".join(lines), btns, card=msg.card if msg else None)
    return ctx.reply(out)


async def tip(ctx: SkillContext, wa_id: str | None, step: str | None) -> Reply:
    """A whisper to the associate's phone: the step's own tip and its clip, while they are with the customer."""
    staff = await repo.staff_by_wa(wa_id or "")
    step = step if step in STEPS else "assess"
    if staff is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    alang = staff.lang or "en"
    body = ctx.t("tip_whisper", alang, step=ctx.t(f"step_{step}", alang), tip=ctx.t(f"tip_{step}", alang))
    to_assoc = info_reply(ctx, body, [Button(id=encode("g", "clip", step), title=ctx.t("b_clip", alang))], to=staff.wa_id)
    ctx.ev("manager_tip", to=staff.wa_id, coach_step=step)
    return ctx.reply(info_reply(ctx, ctx.t("tip_sent", name=staff.name, tip=ctx.t(f"tip_{step}")),
                                [Button(id=encode("m", "floor"), title=ctx.t("b_floor"))]), to_assoc)


async def digest(ctx: SkillContext, to: str | None = None) -> Reply:
    """Today's huddle card: eight numbers from today's data, 'since the store visits' (the October baseline beside
    today), each associate's steps, the biggest gap with its clip, and the best today."""
    d = await _today_data(ctx)
    n = ctx.reg.num
    totals = [sc.total for sc in d["scores"]]
    guide = sum(1 for t in totals if t >= 11)
    ev = d["events"]
    pf = _price_first(ev)
    pf_cust = sum(1 for p in pf.values() if p.get("who") == "customer")
    pf_assoc = sum(1 for p in pf.values() if p.get("who") == "associate")
    minutes = [p["minute"] for p in pf.values() if p.get("minute") is not None]
    qs = _questions_before_product(ev)
    parivaar = sum(1 for e in ev if e.type == "parivaar_sent")
    inst_ok = sum(1 for e in ev if e.type == "install_confirmed")
    inst_bad = sum(1 for e in ev if e.type == "install_problem")
    med = statistics.median(totals) if totals else None
    dash = "–"
    kpis = [
        {"label": ctx.t("kpi_conversations"), "value_str": n(len(d["sessions"]), "calc:digest")},
        {"label": ctx.t("kpi_guide"), "value_str": f"{n(guide, 'calc:digest')}/{n(len(totals), 'calc:digest')}"},
        {"label": ctx.t("kpi_median"), "value_str": f"{n(med, 'calc:digest')}/14" if med is not None else dash},
        {"label": ctx.t("kpi_price_first_ca"), "value_str": f"{n(pf_cust, 'calc:digest')} / {n(pf_assoc, 'calc:digest')}"},
        {"label": ctx.t("kpi_minute"), "value_str": n(statistics.median(minutes), "calc:digest") if minutes else dash},
        {"label": ctx.t("kpi_questions"), "value_str": n(statistics.median(qs), "calc:digest") if qs else dash},
        {"label": ctx.t("kpi_parivaar"), "value_str": n(parivaar, "calc:digest")},
        {"label": ctx.t("kpi_install_ok"), "value_str": f"{n(inst_ok, 'calc:digest')} / {n(inst_bad, 'calc:digest')}"},
    ]
    b_n, b_of = BASELINE["price_assoc"]
    since = [
        {"label": ctx.t("base_median"), "oct": str(BASELINE["median"]), "today": n(med, "calc:digest") if med is not None else dash},
        {"label": ctx.t("base_price_assoc"), "oct": f"{b_n}/{b_of}",
         "today": f"{n(pf_assoc, 'calc:digest')}/{n(len(pf), 'calc:digest')}" if pf else dash},
        {"label": ctx.t("base_questions"), "oct": str(BASELINE["questions"]),
         "today": n(statistics.median(qs), "calc:digest") if qs else dash},
    ]
    for r in since:
        ctx.reg.raw(r["oct"], "reference:store_visits_oct")
    ctx.reg.static("14 11")
    by_assoc: dict[str, list] = {}
    for sc in d["scores"]:
        by_assoc.setdefault(sc.associate_wa_id, []).append(sc)
    names, cells = [], []
    worst = None
    for wa, rows in by_assoc.items():
        st = d["staff"].get(wa)
        names.append(st.name if st else wa[-4:])
        avg = {s: round(sum((r.steps or {}).get(s, 0) for r in rows) / len(rows), 1) for s in STEPS}
        cells.append([_cell(avg[s]) for s in STEPS])
        low = min(STEPS, key=lambda s: avg[s])
        if worst is None or avg[low] < worst[2]:
            worst = (wa, st.name if st else "", avg[low], low)
    best = max(d["scores"], key=lambda sc: sc.total, default=None)
    best_text = None
    if best is not None:
        bst = d["staff"].get(best.associate_wa_id)
        top = max(STEPS, key=lambda s: (best.steps or {}).get(s, 0))
        best_text = ctx.t("digest_best_card", name=bst.name if bst else "", total=n(best.total, "calc:digest"),
                          step=ctx.t(f"step_{top}"))
    if worst:
        caption = ctx.t("digest_gap", name=worst[1], step=ctx.t(f"step_{worst[3]}"))
        gaps = ctx.t("digest_gap_card", name=worst[1], step=ctx.t(f"step_{worst[3]}"))
        if best is not None:
            bst = d["staff"].get(best.associate_wa_id)
            caption += "\n\n" + ctx.t("digest_best_line", name=bst.name if bst else "", total=n(best.total, "calc:digest"))
    else:
        caption = gaps = ctx.t("digest_no_data")
    vn = [e for e in ev if e.type == "voice_note"]
    if vn:  # the associates' voice notes today, and how many were understood (the voice log is one tap away)
        ok = sum(1 for e in vn if (e.payload or {}).get("understood") or (e.payload or {}).get("intents"))
        caption += "\n\n_" + ctx.t("kpi_voice") + ": " + ctx.t("kpi_voice_val", n=n(len(vn), "calc:digest"),
                                                                ok=n(ok, "calc:digest")) + "_"
    card = Card(
        template="digest",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "kpis": kpis,
            "since": since,
            "heatmap": {"associates": names, "steps": [ctx.t(f"step_short_{s}") for s in STEPS], "cells": cells},
            "gaps_text": gaps,
            "best_text": best_text,
        },
    )
    btns = []
    if worst:
        btns = [Button(id=encode("m", "nudge", worst[0], worst[3]), title=ctx.t("b_send_nudge"))]
    if not to:
        btns += [Button(id=encode("m", "floor"), title=ctx.t("b_floor")), Button(id=encode("m", "team"), title=ctx.t("b_team"))]
    m = info_reply(ctx, caption, btns[:3], card=card, to=to)
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
            ListRow(id=encode("m", "profile", st.wa_id), title=truncate(st.name, 24), description=truncate(desc, 72))
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


def approval_context(ctx: SkillContext, s, sku: str | None, lang: str) -> list[str]:
    """What the manager decides on: the product, its price and its live offers, all from the data."""
    p = ctx.facts.product_sync(sku) if sku else None
    if p is None:
        return []
    from chanakya.skills import common as C
    from chanakya.skills import product_view as PV

    out = [ctx.t("approval_product", lang, name=C.product_title(ctx, p, lang), price=ctx.reg.inr(p.price, p.price_source))]
    offs = PV.live_offers_for(ctx, p)
    if offs:
        names = [f"{ctx.pick(o.title, lang)} ({ctx.reg.raw(fmt.date_short(o.valid_to, lang), f'offer:{o.id}')})" for o, _ in offs[:2]]
        for o, _ in offs[:2]:
            ctx.reg.raw(ctx.pick(o.title, lang), f"offer:{o.id}")
        out.append(ctx.t("approval_live", lang, offers="; ".join(names)))
    else:
        out.append(ctx.t("approval_live_none", lang))
    return out


async def approvals(ctx: SkillContext) -> Reply:
    tasks = await repo.tasks_open(ctx.actor_wa_id, "approval")
    if not tasks:
        return ctx.reply(info_reply(ctx, ctx.t("no_approvals"), [Button(id=encode("m", "floor"), title=ctx.t("b_floor"))]))
    t = tasks[0]
    p = t.payload or {}
    staff = await repo.staff_by_wa(p.get("associate", ""))
    body = ctx.t(
        "approval_for_manager", name=staff.name if staff else "", code=ctx.reg.raw(p.get("code", ""), "session:code")
    )
    s = await ctx.svc.sessions.get(t.session_id) if t.session_id else None
    extra = approval_context(ctx, s, p.get("sku"), ctx.lang)
    if extra:
        body += "\n\n" + "\n".join(extra)
    btns = [
        Button(id=encode("m", "appr", t.id, "offers"), title=ctx.t("b_offer_dikhao")),
        Button(id=encode("m", "appr", t.id, "no"), title=ctx.t("b_mana_karo")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


async def approval_action(ctx: SkillContext, task_id: str, action: str) -> Reply:
    """The manager's decision, and exactly what the associate was told (only offers in the data)."""
    t = await repo.task_get(int(task_id))
    if t is None or t.status != "open":
        return ctx.reply(info_reply(ctx, ctx.t("no_approvals")))
    p = t.payload or {}
    assoc = p.get("associate")
    staff = await repo.staff_by_wa(assoc) if assoc else None
    alang = staff.lang if staff else "hi"
    name = staff.name if staff else ""
    await repo.task_update(t.id, status="done")
    s = await ctx.svc.sessions.get(t.session_id) if t.session_id else None
    btns = [Button(id=encode("s", s.code, "offers"), title=truncate_title(ctx.t("b_offers", alang)))] if s else []
    back = [Button(id=encode("m", "floor"), title=ctx.t("b_floor"))]
    if action == "no":
        told = ctx.t("approval_denied", alang)
        m = OutMessage(to=assoc, kind="buttons" if btns else "text", body=told, buttons=btns, session_id=s.id if s else None)
        ctx.ev("approval_decided", task_id=t.id, decision="no")
        return ctx.reply(info_reply(ctx, ctx.t("approval_told", name=name, text=ctx.t("approval_denied")), back), m)
    # Managers can only point to offers that exist in the data: the product's own live offers, else the category's.
    from chanakya.core.clock import today
    from chanakya.skills import product_view as PV

    prod = ctx.facts.product_sync(p.get("sku")) if p.get("sku") else None
    offs = [o for o, _ in PV.live_offers_for(ctx, prod)] if prod else await ctx.facts.offers((s.home.category if s else None) or "tv", today())

    def lines(lang):
        return "; ".join(f"{ctx.pick(o.title, lang)} ({ctx.reg.raw(fmt.date_short(o.valid_to, lang), f'offer:{o.id}')})"
                         for o in offs[:2]) or "–"

    for o in offs[:2]:
        ctx.reg.raw(ctx.pick(o.title, alang) + " " + ctx.pick(o.title, ctx.lang), f"offer:{o.id}")
    body = ctx.t("approval_offers", alang, offers=lines(alang))
    ctx.ev("approval_decided", task_id=t.id, decision="offers", offers=[o.id for o in offs[:2]])
    mine = ctx.t("approval_offers", offers=lines(ctx.lang))
    return ctx.reply(info_reply(ctx, ctx.t("approval_told", name=name, text=mine), back), info_reply(ctx, body, btns, to=assoc))


async def nudge(ctx: SkillContext, wa_id: str, step: str) -> Reply:
    staff = await repo.staff_by_wa(wa_id)
    if staff is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    lang = staff.lang or "en"
    body = ctx.t("nudge_body", lang, step=ctx.t(f"step_{step}", lang), tip=ctx.t(f"tip_{step}", lang))
    btns = [
        Button(id=encode("g", "clip", step), title=ctx.t("b_clip", lang)),
        Button(id=encode("g", "quiz", step), title=ctx.t("b_quiz", lang)),
    ]
    ctx.ev("manager_nudge", to=wa_id, coach_step=step)
    return ctx.reply(info_reply(ctx, ctx.t("nudge_sent", name=staff.name) + "\n> " + ctx.t(f"tip_{step}"),
                                [Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))]),
                     info_reply(ctx, body, btns, to=wa_id))


# ---------------------------------------------------------------- voice first for the manager, and the voice log
MGR_WORDS = {
    "digest": ["digest", "sabha", "report", "aaj kaisa", "summary", "डायजेस्ट", "रिपोर्ट", "अहवाल", "सारांश"],
    "floor": ["floor", "abhi kaun", "live", "kaun kya kar", "फ्लोर", "आत्ता कोण"],
    "team": ["team", "sab log", "टीम"],
    "approvals": ["approval", "manzoori", "manjoori", "मंजूरी", "मंजुरी"],
    "voice": ["voice", "awaaz", "recording", "voice log", "voice note", "आवाज", "व्हॉइस"],
    "visits": ["visits", "store visit", "store visits", "mystery", "भेटी", "स्टोअर भेट"],
}


def menu(ctx: SkillContext, heard: str | None = None) -> Reply:
    """The manager's menu, one list: Floor, Huddle, Team, Approvals, Voice log, Store visits."""
    rows = [ListRow(id=encode("m", k), title=truncate(ctx.t(f"mgr_row_{k}"), 24), description=truncate(ctx.t(f"mgr_row_{k}_d"), 72))
            for k in ("floor", "digest", "team", "approvals", "vlog", "visits")]
    ctx.reg.static(" ".join(r.title + " " + (r.description or "") for r in rows))
    m = OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("manager_help"), list_button=truncate_title(ctx.t("list_mgr")),
                   sections=[ListSection(rows=rows)], allowed_numbers=ctx.reg.allowed())
    if heard:
        m.footer = ctx.t("footer_heard", text=ctx.reg.raw(heard, "voice:transcript"))
    return ctx.reply(m)


def _match(text: str, staff: dict) -> tuple[str | None, str | None]:
    t = " " + " ".join((text or "").lower().split()) + " "
    who = next((wa for wa, st in staff.items() if st.role == "associate" and st.name and f" {st.name.lower()} " in t), None)
    for k, words in MGR_WORDS.items():
        if any((" " + w + " ") in t if w.isascii() else w in t for w in words):
            return k, who
    return None, who


async def by_voice(ctx: SkillContext) -> Reply:
    """What the manager said or typed: 'aaj ka digest', 'Ravi ke voice notes', 'team kaisi hai', 'approvals'."""
    from chanakya.ai.gemini import AIUnavailable

    msg = ctx.inbound
    text = msg.text or ""
    if msg.type == "audio" and msg.media_id:
        try:
            audio, mime = await ctx.svc.channel.download_media(msg.media_id)
            text = await ctx.ai.transcribe(audio, mime or msg.mime_type)
        except AIUnavailable:
            return ctx.reply(info_reply(ctx, ctx.t("voice_retry" if ctx.ai.live else "voice_unavailable")))
    staff = {s.wa_id: s for s in await repo.staff_all()}
    what, who = _match(text, staff)
    ctx.ev("manager_voice" if msg.type == "audio" else "manager_text", what=what, transcript=" ".join(text.split())[:200])
    if what == "digest":
        return await digest(ctx)
    if what == "floor":
        return await floor(ctx)
    if what == "team":
        return await team(ctx)
    if what == "approvals":
        return await approvals(ctx)
    if what == "voice":
        return await voice_log(ctx, who)
    if what == "visits":
        from chanakya.skills import visitlog

        return await visitlog.summary(ctx)
    if who:
        return await profile(ctx, who)
    snip = " ".join(text.split())
    return menu(ctx, heard=(snip if len(snip) <= 38 else snip[:37] + "…") if snip else None)


def _when(e) -> str:
    from chanakya.core.clock import IST

    at = repo.aware(e.at) or repo.aware(e.created_at)
    return at.astimezone(IST).strftime("%d %b %H:%M") if at else ""


async def voice_log(ctx: SkillContext, who: str | None = None) -> Reply:
    """The associates' voice notes, newest first (14 days): who, which customer, when, and what was said."""
    since = ctx.now - timedelta(days=14)
    rows_ev = [e for e in await repo.events_for(types=["voice_note"], since=since) if not who or e.actor_wa_id == who]
    staff = {s.wa_id: s for s in await repo.staff_all()}
    if not rows_ev:
        return ctx.reply(info_reply(ctx, ctx.t("vlog_none"), [Button(id=encode("m", "digest"), title=ctx.t("b_digest"))]))
    rows = []
    for e in reversed(rows_ev[-10:]):
        pl = e.payload or {}
        st = staff.get(e.actor_wa_id or "")
        title = " · ".join(x for x in ((st.name if st else "?"), pl.get("code") or "", _when(e)) if x)
        rows.append(ListRow(id=encode("m", "vnote", e.id), title=truncate(title, 24),
                            description=truncate(pl.get("transcript") or ctx.t("vlog_empty"), 72)))
    for r in rows:
        ctx.reg.raw(r.title + " " + (r.description or ""), "voice:log")
    n = ctx.reg.num(len(rows_ev), "voice:log:count")
    name = staff[who].name if who and who in staff else None
    body = ctx.t("vlog_body_who", n=n, name=name) if name else ctx.t("vlog_body", n=n)
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=body, list_button=truncate_title(ctx.t("list_vlog")),
                                sections=[ListSection(rows=rows)], allowed_numbers=ctx.reg.allowed()))


UNITS = (("_sqft", " sq ft"), ("_ft", " ft"), ("_kg", " kg"), ("_l", " L"), ("_in", " inch"), ("_h", " h"))


def _understood_lines(ctx: SkillContext, pl: dict, pack) -> list[str]:
    """'sofa_distance=8_10' → 'Sofa distance: 8–10 ft', from the pack's own titles and values."""
    out = []
    for item in pl.get("understood") or []:
        k, _, v = str(item).partition("=")
        sd = pack.slot(k) if pack else None
        if sd is None and pack:
            sid = pack.slot_for_number(k)
            sd = pack.slot(sid) if sid else None
            unit = next((u for suf, u in UNITS if k.endswith(suf)), "")
            if k.endswith("_inr"):
                v = ctx.reg.inr(float(v), "voice:log") if v.replace(".", "", 1).isdigit() else v
            elif unit:
                v = f"{v}{unit}"
            if sd:
                out.append(f"• {ctx.pick(sd.title)}: {v}")
                continue
        if sd:
            out.append(f"• {ctx.pick(sd.title)}: {sd.value_title(v, ctx.lang)}")
        else:
            out.append(f"• {k.replace('_', ' ').capitalize()}: {v.replace('_', ' ')}")
    return out


async def profile(ctx: SkillContext, wa: str | None) -> Reply:
    """One associate: today's customers and scores, their steps over the last 7 days (strongest and weakest), their
    wins, and the coaching one tap away: the clip for the weakest step, their voice notes, the huddle."""
    from chanakya.skills import wins

    st = await repo.staff_by_wa(wa or "")
    if st is None:
        return await team(ctx)
    d = await _today_data(ctx)
    n = ctx.reg.num
    sess = sum(1 for r, _ in d["sessions"] if r.associate_wa_id == st.wa_id)
    today = [sc.total for sc in d["scores"] if sc.associate_wa_id == st.wa_id]
    lines = [ctx.t("profile_head", name=st.name), ""]
    if today:
        lines.append(ctx.t("profile_today", s=n(sess, "calc:profile"), m=n(statistics.median(today), "calc:profile"),
                           g=n(sum(1 for x in today if x >= 11), "calc:profile"), n=n(len(today), "calc:profile")))
    else:
        lines.append(ctx.t("profile_today_none", s=n(sess, "calc:profile")))
    week = [sc for sc in await repo.scores_for(st.wa_id, limit=100)
            if (repo.aware(sc.at) or ctx.now) >= ctx.now - timedelta(days=7)]
    weakest = None
    if week:
        avg = {s: round(sum((r.steps or {}).get(s, 0) for r in week) / len(week), 1) for s in STEPS}
        hi_s, lo_s = max(STEPS, key=lambda s: avg[s]), min(STEPS, key=lambda s: avg[s])
        weakest = lo_s
        lines.append(ctx.t("profile_steps", best=ctx.t(f"step_{hi_s}"), b=n(_cell(avg[hi_s]), "calc:profile"),
                           worst=ctx.t(f"step_{lo_s}"), w=n(_cell(avg[lo_s]), "calc:profile")))
    c = await wins.counts(ctx, wa=st.wa_id)
    parts = []
    for key, k in (("wins_cards", "cards"), ("wins_sales", "sales")):
        if c[k]:
            parts.append(ctx.t(wins._one(key, c[k]), n=n(c[k], f"wins:{k}")))
    if c["streak"] >= 2:
        parts.append(ctx.t("wins_streak", n=n(c["streak"], "wins:streak")))
    if parts:
        lines.append(ctx.t("profile_wins", what=" · ".join(parts)))
    vn = [e for e in d["events"] if e.type == "voice_note" and e.actor_wa_id == st.wa_id]
    if vn:
        lines.append(ctx.t("profile_voice", n=n(len(vn), "calc:profile")))
    if week:
        lines += ["", ctx.t("profile_steps_note", n=n(len(week), "calc:profile"))]
    ctx.reg.static("14 11 7 2")
    btns = []
    if weakest:
        btns.append(Button(id=encode("m", "nudge", st.wa_id, weakest),
                           title=truncate_title(ctx.t("b_clip_for", step=ctx.t(f"step_{weakest}")))))
    btns += [Button(id=encode("m", "whov", st.wa_id), title=ctx.t("b_voice_notes")),
             Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))]
    return ctx.reply(info_reply(ctx, "\n".join(lines), btns[:3]))


async def voice_detail(ctx: SkillContext, event_id: int) -> Reply:
    """One voice note in plain words: who, which customer, when; what was heard; what Chanakya understood (the
    pack's own titles and values) and what was asked for."""
    e = await repo.event_get(event_id)
    if e is None or e.type != "voice_note":
        return await voice_log(ctx)
    pl = e.payload or {}
    st = await repo.staff_by_wa(e.actor_wa_id or "")
    s = await ctx.svc.sessions.get(e.session_id) if e.session_id else None
    pack = ctx.packs.get(s.home.category) if s else None
    head = " · ".join(x for x in ((st.name if st else "?"), pl.get("code") or "", _when(e), (pl.get("lang") or "").upper()) if x)
    lines = [f"*{head}*", "", ctx.t("vlog_heard", text=pl.get("transcript") or ctx.t("vlog_empty"))]
    got = _understood_lines(ctx, pl, pack)
    asked = [ctx.t(f"intent_{i}") for i in pl.get("intents") or [] if i != "other"]
    if got:
        lines += ["", ctx.t("vlog_got", lines="\n".join(got[:6]))]
    if asked:
        lines += ["", ctx.t("vlog_asked", what=", ".join(asked[:4]))]
    if not got and not asked:
        lines += ["", ctx.t("vlog_nothing")]
    ctx.reg.raw("\n".join(lines), "voice:log")
    btns = [Button(id=encode("m", "vlog"), title=ctx.t("b_vlog")), Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))]
    if st:
        btns.insert(1, Button(id=encode("m", "whov", st.wa_id), title=truncate_title(ctx.t("b_vlog_who", name=st.name))))
    return ctx.reply(info_reply(ctx, "\n".join(lines), btns[:3]))
