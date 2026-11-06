"""Sabha — department manager view on WhatsApp: Floor, Digest, Team, Approvals, coaching nudges."""

from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta

from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.clock import IST
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, ListRow, ListSection, OutMessage, Reply
from chanakya.core.days import parse_day
from chanakya.core.status import short_name
from chanakya.skills.base import SkillContext
from chanakya.skills.review import STEPS
from chanakya.truth import fmt


async def _day_data(ctx: SkillContext, day: date | None = None) -> dict:
    """One day's data (today by default): the customers started that day, its scores and its events, and the staff."""
    day = day or ctx.now.date()
    start = datetime.combine(day, time.min, tzinfo=IST)
    end = start + timedelta(days=1)

    def inside(at) -> bool:
        at = repo.aware(at) or start
        return start <= at < end

    sessions = [(r, st) for r, st in await ctx.svc.sessions.since(start - timedelta(hours=6)) if inside(r.created_at)]
    scores = [sc for sc in await repo.scores_for(limit=5000) if inside(sc.at)]
    events = await repo.events_for(since=start, until=end)
    staff = {s.wa_id: s for s in await repo.staff_all()}
    return {"day": day, "start": start, "sessions": sessions, "scores": scores, "events": events, "staff": staff}


async def _today_data(ctx: SkillContext) -> dict:
    return await _day_data(ctx)


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
    body = "\n".join(  # 3 lines: right now; today with the follow-ups and install risks; (the tap hint)
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
            + " · "
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
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=body + "\n" + ctx.t("floor_tap"),
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
    now = ctx.t("floor_c_step", step=ctx.t(f"step_{step}"), m=ctx.reg.num(_mins(ctx, row[0].created_at), "calc:floor"))
    lines = [ctx.t("floor_c_head", assoc=who.name if who else "?", who=parts[0] if parts else s.code,
                   cat=ctx.pick(pack.label) if pack and s.home.category else ctx.t("floor_no_cat")),
             " · ".join([now] + parts[2:]),
             ctx.t("tip_line", tip=ctx.t(f"tip_{step}"))]
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


def _day_name(ctx: SkillContext, day: date) -> str:
    """'Aaj', 'Kal', else 'Budhvaar, 5 Nov' (Roman Hinglish day names in Hindi)."""
    today = ctx.now.date()
    if day == today:
        return ctx.t("day_today")
    if day == today - timedelta(days=1):
        return ctx.t("day_yesterday")
    return ctx.reg.raw(f"{fmt.day_name(day, ctx.lang, roman=True)}, {fmt.date_short(day, ctx.lang)}", "clock:day")


def _day_short(ctx: SkillContext, day: date) -> str:
    today = ctx.now.date()
    if day in (today, today - timedelta(days=1)):
        return _day_name(ctx, day)
    return ctx.reg.raw(fmt.date_short(day, ctx.lang), "clock:day")


def _day_buttons(ctx: SkillContext, day: date) -> list[Button]:
    """The day before, and back to today (or the floor, when this is today)."""
    prev = day - timedelta(days=1)
    title = ctx.reg.raw(truncate_title("◀ " + _day_short(ctx, prev)), "clock:day")
    btns = [Button(id=encode("m", "digest", prev.isoformat()), title=title)]
    if day == ctx.now.date():
        btns.append(Button(id=encode("m", "floor"), title=ctx.t("b_floor")))
    else:
        btns.append(Button(id=encode("m", "digest"), title=ctx.t("b_huddle")))
    return btns


def _status(ctx: SkillContext, r, st, today: bool, reason: str | None) -> tuple[str, str, str]:
    """A customer's status from the session and what the associate marked: sold (with the product), follow-up or
    didn't buy (with the reason they picked), still on the floor, left open, or closed."""
    out = st.extra.get("outcome")
    if out == "sold" or st.extra.get("sale_marked"):
        key = "sold"
    elif out in ("followup", "lost"):
        key = out
    elif r.status == "active":
        key = "floor" if today else "open"
    else:
        key = "closed"
    text = ctx.t(f"dg_st_{key}")
    if key == "sold" and st.chosen_sku:
        p = ctx.facts.product_sync(st.chosen_sku)
        if p is not None:
            from chanakya.skills import common as C

            text += " · " + C.product_name(ctx, p)
    elif key in ("followup", "lost") and reason:
        text += " · " + ctx.t(f"obj_row_{reason}")
    return text, {"sold": "g", "followup": "o", "lost": "r", "floor": "b"}.get(key, "n"), key


async def digest(ctx: SkillContext, day: date | None = None, to: str | None = None, heard: str | None = None,
                 future: bool = False) -> Reply:
    """One day's huddle, voice first (today by default; any day the manager asks for): the answer in 3 lines at once
    (the funnel, how the day ended, the gap), then the dashboard card with the buttons: the funnel and how the day
    ended, the quality numbers, the team, each customer and their status, October beside the day, the gap and the
    best. Everything from that day's sessions, scores and events: nothing estimated."""
    today = ctx.now.date()
    day = day or today
    name = _day_name(ctx, day)
    footer = None
    if heard:
        snip = " ".join(heard.split())
        footer = ctx.t("footer_heard", text=ctx.reg.raw(snip if len(snip) <= 38 else snip[:37] + "…", "voice:transcript"))
    if future or day > today:
        ctx.ev("digest_shown", day=day.isoformat(), future=True)
        m = info_reply(ctx, ctx.t("digest_future", day=name), [Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))], to=to)
        m.footer = footer or m.footer
        return ctx.reply(m)
    d = await _day_data(ctx, day)
    n = ctx.reg.num
    sessions = sorted(d["sessions"], key=lambda x: repo.aware(x[0].created_at) or d["start"])
    if not sessions and not d["scores"]:
        ctx.ev("digest_shown", day=day.isoformat(), customers=0)
        m = info_reply(ctx, ctx.t("digest_empty", day=name), _day_buttons(ctx, day), to=to)
        m.footer = footer or m.footer
        return ctx.reply(m)
    ev = d["events"]
    totals = [sc.total for sc in d["scores"]]
    pf = _price_first(ev)
    pf_cust = sum(1 for p in pf.values() if p.get("who") == "customer")
    pf_assoc = sum(1 for p in pf.values() if p.get("who") == "associate")
    qs = _questions_before_product(ev)
    med = statistics.median(totals) if totals else None
    vn = [e for e in ev if e.type == "voice_note"]
    vn_ok = [e for e in vn if (e.payload or {}).get("understood") or (e.payload or {}).get("intents")]
    reasons = {e.session_id: (e.payload or {}).get("objection") for e in ev if e.type == "objection_handled" and e.session_id}
    is_today = day == today
    rows, cust = [], []
    for r, st in sessions:
        text, tone, key = _status(ctx, r, st, is_today, reasons.get(st.id))
        rows.append((r, st, text, tone, key))
    total = len(rows)
    keys = [x[4] for x in rows]
    sold, follow, lost = keys.count("sold"), keys.count("followup"), keys.count("lost")
    demos = sum(1 for x in rows if x[1].demo_done)
    opts = sum(1 for x in rows if x[1].option_labels or x[1].shortlist)
    costed = sum(1 for x in rows if x[1].cost_snapshot)

    def pct(k: int) -> int:
        return round(100 * k / total) if total else 0

    funnel = []  # what the associates did with each customer, from the sessions themselves
    for i, (label, k) in enumerate((("kpi_customers", total), ("fn_options", opts), ("fn_demo", demos),
                                    ("fn_cost", costed), ("kpi_sold", sold))):
        funnel.append({"label": ctx.t(label), "n": n(k, "calc:digest"), "w": max(pct(k), 6) if k else 0,
                       "pct": f"{n(pct(k), 'calc:digest')}%" if i else ""})
    outcomes = [{"label": ctx.t(f"dg_st_{k}"), "n": n(keys.count(k), "calc:digest"), "k": keys.count(k),
                 "tone": {"sold": "g", "followup": "o", "lost": "r", "floor": "b"}.get(k, "n")}
                for k in ("sold", "followup", "lost", "floor", "open", "closed") if keys.count(k)]
    said = [reasons[x[1].id] for x in rows if reasons.get(x[1].id)]
    top = sorted({o: said.count(o) for o in said}.items(), key=lambda kv: -kv[1])[:3]
    why = " · ".join(f"{ctx.t(f'obj_row_{o}')} {n(c, 'calc:digest')}" for o, c in top) or None
    dash = "–"
    b_n, b_of = BASELINE["price_assoc"]
    oct_med, oct_q = ctx.reg.raw(str(BASELINE["median"]), "reference:store_visits_oct"), ctx.reg.raw(str(BASELINE["questions"]), "reference:store_visits_oct")
    oct_pf = ctx.reg.raw(f"{b_n}/{b_of}", "reference:store_visits_oct")
    kpis = [  # the quality numbers, each with October's store visits beside it where the reference has one
        {"label": ctx.t("kpi_median"), "value_str": f"{n(med, 'calc:digest')}/14" if med is not None else dash,
         "sub": ctx.t("kpi_oct", v=oct_med)},
        {"label": ctx.t("kpi_price_first_ca"), "value_str": f"{n(pf_cust, 'calc:digest')} / {n(pf_assoc, 'calc:digest')}",
         "sub": ctx.t("kpi_oct_assoc", v=oct_pf)},
        {"label": ctx.t("kpi_questions"), "value_str": n(statistics.median(qs), "calc:digest") if qs else dash,
         "sub": ctx.t("kpi_oct", v=oct_q)},
        {"label": ctx.t("kpi_voice_ok"), "value_str": f"{n(len(vn_ok), 'calc:digest')}/{n(len(vn), 'calc:digest')}"},
    ]
    staff = d["staff"]
    people = [wa for wa, s in staff.items() if (s.base_role or s.role) == "associate"]  # never a demo role switch
    people += [st.associate_wa_id for _, st in sessions if st.associate_wa_id not in people]
    team = []
    for wa in people:
        mine = [x for x in rows if x[1].associate_wa_id == wa]
        sc = [s.total for s in d["scores"] if s.associate_wa_id == wa]
        st_ = staff.get(wa)
        team.append({
            "name": st_.name if st_ else wa[-4:],
            "c": n(len(mine), "calc:digest"),
            "d": n(sum(1 for x in mine if x[1].demo_done), "calc:digest"),
            "s": n(sum(1 for x in mine if x[4] == "sold"), "calc:digest"),
            "m": f"{n(statistics.median(sc), 'calc:digest')}/14" if sc else dash,
            "v": n(sum(1 for e in vn if e.actor_wa_id == wa), "calc:digest"),
            "low": dash,
            "_wa": wa,
            "_k": (-len(mine), st_.name if st_ else wa),
        })
    team.sort(key=lambda x: x.pop("_k"))
    for r, st, text, tone, _ in rows[:4]:
        who = staff.get(st.associate_wa_id)
        cname = short_name(st.home.customer_name)
        pack = ctx.packs.get(st.home.category) if st.home.category else None
        at = repo.aware(r.created_at)
        cust.append({
            "time": ctx.reg.raw(at.astimezone(IST).strftime("%H:%M"), "session:time") if at else "",
            "cust": ctx.reg.raw(f"{st.code} {cname}" if cname and cname != "–" else st.code, "session:code"),
            "assoc": who.name if who else "?",
            "cat": ctx.pick(pack.label) if pack else dash,
            "demo": "yes" if st.demo_done else "",
            "status": text,
            "tone": tone,
        })
    more = ctx.t("dg_more", n=n(len(rows) - 4, "calc:digest")) if len(rows) > 4 else None
    ctx.reg.static("14 11 4 100")
    by_assoc: dict[str, list] = {}
    for sc in d["scores"]:
        by_assoc.setdefault(sc.associate_wa_id, []).append(sc)
    worst = None
    for wa, srows in by_assoc.items():  # each associate's weakest step that day (the average over their scores)
        st = staff.get(wa)
        avg = {s: sum((r.steps or {}).get(s, 0) for r in srows) / len(srows) for s in STEPS}
        low = min(STEPS, key=lambda s: avg[s])
        if avg[low] == max(avg.values()):
            continue  # every step the same: no weakest step to name
        for t in team:
            if t["_wa"] == wa:
                t["low"] = ctx.t(f"step_short_{low}")
        if worst is None or avg[low] < worst[2]:
            worst = (wa, st.name if st else "", avg[low], low)
    for t in team:
        t.pop("_wa", None)
    best = max(d["scores"], key=lambda sc: sc.total, default=None)
    lines = [ctx.t("digest_funnel_line", day=name, c=n(total, "calc:digest"), o=n(opts, "calc:digest"),
                   d=n(demos, "calc:digest"), k=n(costed, "calc:digest"), s=n(sold, "calc:digest"))]
    ended = [f"{ctx.t(f'dg_st_{k}')} {n(keys.count(k), 'calc:digest')}" for k in ("followup", "lost", "floor", "open", "closed")
             if keys.count(k)]  # how the rest of the day ended (only what happened), and the reason said most
    if top:
        ended.append(ctx.t("digest_top_reason", reason=ctx.t(f"obj_row_{top[0][0]}")))
    if ended:
        lines.append(" · ".join(ended))
    if worst:  # where to coach: the weakest step of the day, and the best score
        line = ctx.t("digest_gap_line", name=worst[1], step=ctx.t(f"step_short_{worst[3]}"))  # as on the card
        if best is not None and len(by_assoc) > 1:  # the best only means something beside someone else
            bst = staff.get(best.associate_wa_id)
            line += " · " + ctx.t("digest_best_short", name=bst.name if bst else "", total=n(best.total, "calc:digest"))
        lines.append(line)
    else:
        lines.append(ctx.t("digest_no_scores"))
    if day == today:
        title = ctx.t("digest_title_today")
    elif day == today - timedelta(days=1):
        title = ctx.t("digest_title_yesterday")
    else:
        title = ctx.t("digest_title_day", day=name)
    full = ctx.reg.raw(f"{fmt.day_name(day, ctx.lang, roman=True)}, {fmt.date_short(day, ctx.lang)}", "clock:day")
    card = Card(
        template="digest",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "labels": {"kicker": f"{ctx.t('b_huddle')} · {full}", "title": title},
            "funnel": funnel,
            "outcomes": outcomes,
            "why": why,
            "kpis": kpis,
            "team": team[:4],  # one screen: at most 4 people and 4 customers (the rest: '+N aur')
            "customers": cust,
            "more": more,
        },
    )
    btns = []
    if worst:
        nudge = truncate_title(ctx.t("b_nudge_clip", name=worst[1]))
        btns = [Button(id=encode("m", "nudge", worst[0], worst[3]), title=nudge)]
    btns += _day_buttons(ctx, day)
    ctx.ev("digest_shown", day=day.isoformat(), customers=total, options=opts, demos=demos, full_cost=costed, sold=sold,
           followup=follow, lost=lost, scores=len(totals), voice_notes=len(vn))
    quick = info_reply(ctx, "\n".join(lines[:3]), to=to)  # the answer first, in words, before the card is drawn
    quick.footer = footer
    m = info_reply(ctx, ctx.t("digest_card_line", title=title), btns[:3], card=card, to=to)
    return ctx.reply(quick, m)


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
        body += "\n" + "\n".join(extra)
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
    "digest": ["digest", "sabha", "report", "aaj kaisa", "summary", "dashboard", "kya hua", "kaisa raha", "kaisa gaya",
               "kaisa tha", "how was", "what happened", "डायजेस्ट", "रिपोर्ट", "अहवाल", "सारांश", "क्या हुआ", "कैसा रहा",
               "काय झालं", "काय झाले", "कसा गेला", "कसा होता", "funnel", "analytics", "फनेल"],
    "floor": ["floor", "abhi kaun", "live", "kaun kya kar", "फ्लोर", "आत्ता कोण"],
    "team": ["team", "sab log", "टीम"],
    "approvals": ["approval", "manzoori", "manjoori", "मंजूरी", "मंजुरी"],
    "voice": ["voice", "awaaz", "recording", "voice log", "voice note", "आवाज", "व्हॉइस"],
    "visits": ["visits", "store visit", "store visits", "mystery", "भेटी", "स्टोअर भेट"],
}


GREET = {"hi", "hii", "hello", "hey", "helo", "good morning", "gm", "morning", "namaste", "namaskar", "हाय", "हॅलो",
         "हेलो", "नमस्ते", "नमस्कार", "सुप्रभात"}


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
    asked = parse_day(text, ctx.now.date())  # 'kal ka digest', '3 Nov kaisa raha', 'सोमवारचा अहवाल'
    if asked and (what in (None, "digest") or (what in ("floor", "team") and asked.day != ctx.now.date())):
        what = "digest"  # a day said on its own, or a past day's floor or team, is that day's huddle
    if what is None and not who and " ".join(text.lower().strip(" .!?,").split()) in GREET:
        what = "digest"  # 'hi', 'good morning': voice first, today's numbers at once
    ctx.ev("manager_voice" if msg.type == "audio" else "manager_text", what=what,
           day=asked.day.isoformat() if asked else None, transcript=" ".join(text.split())[:200])
    if what == "digest":
        return await digest(ctx, asked.day if asked else None, heard=text if msg.type == "audio" else None,
                            future=bool(asked and asked.future))
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
    if today:
        first = ctx.t("profile_today", s=n(sess, "calc:profile"), m=n(statistics.median(today), "calc:profile"),
                      g=n(sum(1 for x in today if x >= 11), "calc:profile"), n=n(len(today), "calc:profile"))
    else:
        first = ctx.t("profile_today_none", s=n(sess, "calc:profile"))
    lines = [ctx.t("profile_head", name=st.name) + " · " + first]
    week = [sc for sc in await repo.scores_for(st.wa_id, limit=100)
            if (repo.aware(sc.at) or ctx.now) >= ctx.now - timedelta(days=7)]
    weakest = None
    if week:
        avg = {s: round(sum((r.steps or {}).get(s, 0) for r in week) / len(week), 1) for s in STEPS}
        hi_s, lo_s = max(STEPS, key=lambda s: avg[s]), min(STEPS, key=lambda s: avg[s])
        weakest = lo_s
        lines.append(ctx.t("profile_steps", best=ctx.t(f"step_{hi_s}"), b=n(_cell(avg[hi_s]), "calc:profile"),
                           worst=ctx.t(f"step_{lo_s}"), w=n(_cell(avg[lo_s]), "calc:profile"))
                     + " · " + ctx.t("profile_steps_note", n=n(len(week), "calc:profile")))
    c = await wins.counts(ctx, wa=st.wa_id)
    parts = []
    for key, k in (("wins_cards", "cards"), ("wins_sales", "sales")):
        if c[k]:
            parts.append(ctx.t(wins._one(key, c[k]), n=n(c[k], f"wins:{k}")))
    if c["streak"] >= 2:
        parts.append(ctx.t("wins_streak", n=n(c["streak"], "wins:streak")))
    vn = [e for e in d["events"] if e.type == "voice_note" and e.actor_wa_id == st.wa_id]
    third = [ctx.t("profile_wins", what=" · ".join(parts))] if parts else []
    if vn:
        third.append(ctx.t("profile_voice", n=n(len(vn), "calc:profile")))
    if third:
        lines.append(" · ".join(third))
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
    lines = [f"*{head}*", "> " + " ".join((pl.get("transcript") or ctx.t("vlog_empty")).split())]
    got = [x.lstrip("• ") for x in _understood_lines(ctx, pl, pack)]
    asked = [ctx.t(f"intent_{i}") for i in pl.get("intents") or [] if i != "other"]
    third = []
    if got:
        third.append(ctx.t("vlog_got", lines=", ".join(got[:4])))
    if asked:
        third.append(ctx.t("vlog_asked", what=", ".join(asked[:3])))
    lines.append(" · ".join(third) if third else ctx.t("vlog_nothing"))
    ctx.reg.raw("\n".join(lines), "voice:log")
    btns = [Button(id=encode("m", "vlog"), title=ctx.t("b_vlog")), Button(id=encode("m", "digest"), title=ctx.t("b_huddle"))]
    if st:
        btns.insert(1, Button(id=encode("m", "whov", st.wa_id), title=truncate_title(ctx.t("b_vlog_who", name=st.name))))
    return ctx.reply(info_reply(ctx, "\n".join(lines), btns[:3]))
