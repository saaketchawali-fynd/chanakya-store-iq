"""Step 9 — Seekh (Review): outcome → 7-step score (Section 15), one praise, one tip."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Card, Event, Reply, Step
from chanakya.skills.base import SkillContext

STEPS = ["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
PRICE_EVENTS = {"price_range_given", "cost_shared", "offers_shown", "finance_shown"}
FIRST_PRODUCT = {"options_shown", "label_read"}


def score(events: list[Event], extra: dict) -> tuple[dict[str, int], dict]:
    types = [e.type for e in events]
    facts: dict = {}
    # Validate
    if "claim_validated" in types or "need_validated" in types:
        v = 2
    elif (extra.get("first_note_slots") or 0) >= 3:
        v = 1
    else:
        v = 0
    # Assess: slot_filled before the first product event
    n = 0
    for t in types:
        if t in FIRST_PRODUCT:
            break
        if t == "slot_filled":
            n += 1
    facts["slots_before_product"] = n
    a = 2 if n >= 4 else 1 if n >= 2 else 0
    # Translate
    nb = types.count("benefit_said")
    tr = 2 if nb >= 2 else 1 if nb == 1 else 0
    # Demonstrate
    d = 2 if "demo_shown" in types else 1 if "tryon_sent" in types else 0
    # Complete
    c = 2 if "cost_shared" in types else 0
    # Close
    demo_idx = types.index("demo_shown") if "demo_shown" in types else None
    price_idx = next((i for i, t in enumerate(types) if t in PRICE_EVENTS), None)
    pfb = extra.get("price_first_by")
    overrides_asked = extra.get("price_first_reason") == "asked"
    if price_idx is None:
        cl = 1
    elif demo_idx is not None and price_idx > demo_idx and ("offers_shown" in types or "finance_shown" in types):
        cl = 2
    elif demo_idx is None or price_idx < demo_idx:
        cl = 0 if (pfb == "associate" and not overrides_asked) else 1
    else:
        cl = 1
    # Follow through
    pv = "parivaar_sent" in types
    tk = "task_created" in types or "install_job_created" in types
    f = 2 if (pv and tk) else 1 if (pv or tk) else 0
    facts["benefits"] = nb
    return {
        "validate": v,
        "assess": a,
        "translate": tr,
        "demonstrate": d,
        "complete": c,
        "close": cl,
        "follow_through": f,
    }, facts


async def khatam(ctx: SkillContext) -> Reply:
    if ctx.x.get("outcome") == "sold":  # already known (a delivery day was booked): don't ask again
        return await outcome(ctx, "sold")
    btns = [
        ctx.b("b_sold", ctx.sid("outcome", "sold")),
        ctx.b("b_followup", ctx.sid("outcome", "followup")),
        ctx.b("b_lost", ctx.sid("outcome", "lost")),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("review_ask"), btns))


async def outcome(ctx: SkillContext, out: str) -> Reply:
    ctx.x["outcome"] = out
    if "price_first_by" not in ctx.x:
        btns = [
            ctx.b("b_pf_customer", ctx.sid("pricefirst", "customer")),
            ctx.b("b_pf_me", ctx.sid("pricefirst", "associate")),
            ctx.b("b_pf_none", ctx.sid("pricefirst", "none")),
        ]
        return ctx.reply(info_reply(ctx, ctx.t("price_first_ask"), btns))
    return await _after_outcome(ctx)


async def price_first(ctx: SkillContext, who: str) -> Reply:
    ctx.x["price_first_by"] = who
    ctx.ev("price_first_by", who=who, minute=None, from_review=True)
    return await _after_outcome(ctx)


async def _after_outcome(ctx: SkillContext) -> Reply:
    from chanakya.skills import follow

    out = ctx.x.get("outcome")
    if out == "sold":
        return await follow.sold(ctx)
    if out == "lost":
        ctx.session.awaiting = "lost_reason"
        r = await finish(ctx)
        r.messages.append(info_reply(ctx, ctx.t("lost_reason_ask")))
        return r
    return await finish(ctx)


def _praise(ctx: SkillContext, steps: dict[str, int], facts: dict) -> str:
    order = ["assess", "demonstrate", "translate", "complete", "close", "follow_through", "validate"]
    best = max(order, key=lambda s: (steps[s], -order.index(s)))
    if best == "assess":
        return ctx.t("praise_assess", n=ctx.reg.num(facts.get("slots_before_product", 0), "calc:score"))
    if best == "translate":
        return ctx.t("praise_translate", n=ctx.reg.num(facts.get("benefits", 0), "calc:score"))
    if best == "demonstrate" and ctx.session.demo_recipe:
        demo = ctx.pack.demo(ctx.session.demo_recipe) if ctx.pack else None
        return ctx.t("praise_demonstrate", demo=ctx.pick(demo.title) if demo else "")
    return ctx.t(f"praise_{best}")


async def finish(ctx: SkillContext) -> Reply:
    s = ctx.session
    prior = await repo.events_for(s.id)
    all_events = [Event(type=e.type, payload=e.payload or {}) for e in prior] + list(ctx.events)
    steps, facts = score(all_events, s.extra)
    total = sum(steps.values())
    lowest = min(STEPS, key=lambda st: (steps[st], STEPS.index(st)))
    tip = ctx.t("tip_perfect") if total >= 14 else ctx.t(f"tip_{lowest}")
    praise = _praise(ctx, steps, facts)
    score_str = ctx.reg.num(total, "calc:score")
    ctx.reg.static("14")
    level = ctx.t("score_guide" if total >= 11 else "score_plain", score=score_str)
    card = Card(
        template="score",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "score_str": f"{score_str}/14",
            "steps": [{"name": ctx.t(f"step_{st}"), "points": steps[st]} for st in STEPS],
            "level_text": level,
            "praise": praise,
            "tip": tip,
        },
    )
    caption = "\n".join([level, ctx.t("review_best", text=praise), ctx.t("review_next", text=tip)])
    btns = [
        ctx.b("b_new_customer", encode("g", "new")),
        ctx.b("b_score", encode("g", "score")),
        ctx.b("b_help", encode("g", "help")),
    ]
    await repo.score_add(s.id, s.associate_wa_id, steps, total, ctx.now)
    ctx.ev("score_computed", steps=steps, total=total)
    ctx.ev("session_closed", outcome=ctx.x.get("outcome"))
    s.step = Step.CLOSED
    from chanakya.core.orchestrator import AFTER_SALE_AWAIT

    if ctx.staff and ctx.staff.current_session_id == s.id and s.awaiting not in AFTER_SALE_AWAIT:
        ctx.staff.current_session_id = None  # (kept while the lost reason / a voice note is awaited)
    ctx.scratch["close_session"] = True
    return ctx.reply(info_reply(ctx, caption, btns, card=card))


async def last_score(ctx: SkillContext) -> Reply:
    rows = await repo.scores_for(ctx.actor_wa_id, limit=5)
    if not rows:
        return ctx.reply(info_reply(ctx, ctx.t("no_scores"), [ctx.b("b_new_customer", encode("g", "new"))]))
    totals = ", ".join(ctx.reg.num(r.total, f"score:{r.id}") for r in rows)
    ctx.reg.static("14")
    return ctx.reply(
        info_reply(ctx, ctx.t("score_history", totals=totals), [ctx.b("b_new_customer", encode("g", "new"))])
    )
