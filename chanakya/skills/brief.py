"""Step 1 — Taiyari (Before the shift): brief card, 45-s clip (or tip), one quiz question."""

from __future__ import annotations

import re
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
ICON = {"back_in_stock": "up", "out_of_stock": "down", "low": "low"}


async def focus_step(ctx: SkillContext) -> tuple[str, int | None]:
    rows = await repo.scores_for(ctx.actor_wa_id, limit=5)
    if not rows:
        return "translate", None
    avg = {st: sum((r.steps or {}).get(st, 0) for r in rows) / len(rows) for st in STEPS}
    weakest = min(STEPS, key=lambda st: (avg[st], STEPS.index(st)))
    return weakest, (rows[0].steps or {}).get(weakest)


def _offer_detail(ctx: SkillContext, o) -> str:
    """'10% off · up to ₹5,000 · HDFC cards · on bills over ₹30,000' — every number from the offer row."""
    src = f"offer:{o.id}"
    parts = []
    if o.pct:
        parts.append(ctx.t("brief_offer_pct", pct=ctx.reg.num(float(o.pct), src)))
        if o.max_discount:
            parts.append(ctx.t("brief_offer_max", amt=ctx.reg.inr(o.max_discount, src)))
    elif o.flat:
        key = "brief_offer_cashback" if o.kind == "cashback" else "brief_offer_flat"
        parts.append(ctx.t(key, amt=ctx.reg.inr(o.flat, src)))
    if o.bank:
        parts.append(ctx.t("brief_offer_bank", bank=ctx.reg.raw(o.bank, src)))
    if o.kind == "exchange_bonus":
        parts.append(ctx.t("brief_offer_exchange"))
    if o.min_amount:
        parts.append(ctx.t("brief_offer_min", amt=ctx.reg.inr(o.min_amount, src)))
    title = ctx.pick(o.title).lower()

    def repeats(part: str) -> bool:  # never repeat what the offer's title already says (any script)
        nums = re.findall(r"\d[\d,.]*%?", part)
        if nums:
            return all(n in title for n in nums)
        return bool(o.bank) and o.bank.lower() in part.lower() and o.bank.lower() in title

    return " · ".join(x for x in parts if not repeats(x))


def _offer_rows(ctx: SkillContext, dept: str, d) -> tuple[list[dict], int, int]:
    """Live offers for the department: ending within 3 days first, then new ones, then the rest."""
    rows = []
    for o in ctx.facts.snap.offers:
        if not (o.valid_from <= d <= o.valid_to):
            continue
        cats = o.categories or []
        if dept not in cats and not (dept == "tv" and "soundbar" in cats):
            continue
        if any(k.startswith("with_tv") for k in (o.filter or {})):
            continue
        left = (o.valid_to - d).days
        kind = "ends" if left <= 7 else "new" if (d - o.valid_from).days <= 3 else "live"
        rows.append((("ends", "new", "live").index(kind), o.valid_to, o, kind))
    rows.sort(key=lambda r: (r[0], r[1]))
    out = []
    for _, _, o, kind in rows:
        out.append({
            "kind": kind,
            "badge": ctx.t(f"brief_badge_{kind}"),
            "title": ctx.pick(o.title),
            "detail": _offer_detail(ctx, o),
            "till": ctx.t("brief_offer_till", date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.lang), f"offer:{o.id}:valid_to")),
            "_o": o,
        })
    n_end = sum(1 for r in out if r["kind"] == "ends")
    return out, len(out), n_end


async def brief(ctx: SkillContext) -> Reply:
    """Before the shift: today's focus, the live offers (clearly, one row each), what to keep in mind, stock."""
    staff = ctx.staff
    msgs: list[OutMessage] = []
    if staff.last_brief_date is None and staff.streak == 0 and not ctx.scratch.get("welcomed"):
        msgs.append(info_reply(ctx, ctx.t("welcome_new_staff", name=staff.name)))
    dept = staff.dept if staff.dept in ctx.packs.packs else "tv"
    d = today()
    changes = await ctx.facts.stock_changes(staff.store_id, dept)
    order = {"out_of_stock": 0, "back_in_stock": 1, "low": 2}
    changes = sorted(changes, key=lambda c: order[c["kind"]])
    change_rows = []
    for c in changes[:3]:
        key = {"back_in_stock": "brief_back", "out_of_stock": "brief_out", "low": "brief_low"}[c["kind"]]
        change_rows.append({"icon": ICON[c["kind"]],
                            "text": ctx.t(key, model=ctx.reg.raw(c["model"], c["source"]), qty=ctx.reg.num(c["qty"], c["source"]))})
    offers, n_off, n_end = _offer_rows(ctx, dept, d)
    shown = offers[:4]
    keep = []
    ending = next((o for o in offers if o["kind"] == "ends"), None)
    if ending:
        o = ending["_o"]
        keep.append(ctx.t("brief_rule_offer_end", title=ctx.pick(o.title),
                          date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.lang), f"offer:{o.id}:valid_to")))
    if any(c["kind"] == "low" for c in changes):
        keep.append(ctx.t("brief_rule_stock"))
    keep.append(ctx.t("brief_rule_price"))
    for r in offers:
        r.pop("_o", None)
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
    store = ctx.facts.store_name(staff.store_id).replace(" (TEST)", "")
    date_line = ctx.t("brief_date_line", day=ctx.reg.raw(f"{fmt.day_name(d, ctx.lang, roman=True)} {fmt.date_short(d, ctx.lang)}", "clock:date"),
                      store=ctx.reg.raw(store.replace("Reliance Digital ", ""), "store"))
    date_line = f"{date_line} · {followups}"
    more = ctx.t("brief_offers_more", n=ctx.reg.num(n_off - len(shown), "offers:more")) if n_off > len(shown) else None
    card = Card(
        template="brief",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "greeting": ctx.t("brief_greeting", name=staff.name),
            "date_line": date_line,
            "focus_step": step_name,
            "focus_text": focus_text,
            "focus_tip": ctx.t(f"tip_{focus}"),
            "offers": shown,
            "offers_more": more,
            "no_offers": None if offers else ctx.t("brief_no_offers"),
            "keep": keep[:3],
            "changes": change_rows,
        },
    )
    line1 = ctx.t("brief_caption2", n_off=ctx.reg.num(n_off, "offers:live"), n_end=ctx.reg.num(n_end, "offers:ending"),
                  n_stock=ctx.reg.num(len(changes), "stock:changes"))
    if tasks:
        line1 += " · " + ctx.t("brief_followups", n=ctx.reg.num(len(tasks), "tasks:today"))
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
    f = CLIPS / f"{step}_{ctx.lang}.mp4"
    if not f.exists():
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
