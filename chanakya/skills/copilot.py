"""The copilot's one line: a quiet, useful nudge at the right moment, never an extra message.

At most one tip per message, and each tip at most once per customer. A tip appears only when a fact from the data or
from this home triggers it; otherwise nothing is shown (the status line stays). Tips are for the associate (their
language), never words for the customer, and every number in them is registered with the firewall."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.fit import fit, in_band
from chanakya.core import state
from chanakya.core.clock import today
from chanakya.skills.base import SkillContext


def _once(ctx: SkillContext, tip_id: str, text: str | None) -> str | None:
    if not text:
        return None
    shown = ctx.session.extra.setdefault("tips_shown", [])
    if tip_id in shown:
        return None
    shown.append(tip_id)
    return ctx.t("tip_line", tip=text)


def _avail(ctx: SkillContext) -> list:
    return [p for p in ctx.facts.products_sync(ctx.pack.category) if p.qty > 0 or p.nearby_qty > 0]


def _assess(ctx: SkillContext) -> str | None:
    """A budget was said, but nothing in stock that suits this home fits it: talk budget or size before options."""
    s = ctx.session
    budget = s.home.start_level_inr
    if not budget or s.shortlist or s.home.start_level_source == "none":
        return None
    g = ctx.facts.snap.guides
    suits = [p for p in _avail(ctx) if (b := in_band(p, s.home, ctx.pack, g)) is not None and b >= 1]
    if not suits or any(p.price <= budget * 1.1 for p in suits):
        return None
    low = min(suits, key=lambda p: p.price)
    return _once(ctx, "budget_gap", ctx.t("tip_budget_gap", budget=ctx.reg.inr(budget, "card:start_level"),
                                          low=ctx.reg.inr(low.price, low.price_source)))


def _options(ctx: SkillContext) -> str | None:
    """The best pick's offer ends soon, or the best pick is the only one meeting their top need."""
    s = ctx.session
    best = state.best_sku(s)
    p = ctx.facts.product_sync(best) if best else None
    if p is None:
        return None
    from chanakya.skills import product_view as PV
    from chanakya.truth import fmt

    lab = next((k for k, v in s.option_labels.items() if v == best), "")
    offs = PV.live_offers_for(ctx, p)
    soon = [o for o, _ in offs if (o.valid_to - today()).days <= 3]
    if soon:
        o = soon[0]
        return _once(ctx, "offer_soon", ctx.t("tip_offer_soon", label=lab, title=ctx.pick(o.title, "en"),
                                              date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.lang), f"offer:{o.id}:valid_to")))
    g = ctx.facts.snap.guides
    top = fit(p, s.home, ctx.pack, g).top_rule()
    if not top or top not in ctx.pack.needs:
        return None
    others = [ctx.facts.product_sync(v) for k, v in s.option_labels.items() if v != best]
    meets = [o for o in others if o and any(c.rule_id == top and c.match >= 1 for c in fit(o, s.home, ctx.pack, g).contributions)]
    if meets:
        return None
    need = ctx.pick(ctx.pack.needs[top], "en" if ctx.lang == "hi" else ctx.lang)
    if "{" in need:
        return None
    return _once(ctx, "only_best", ctx.t("tip_only_best", label=lab, need=need))


def _cost(ctx: SkillContext) -> str | None:
    """Replacing an old one and the exchange is not asked yet; or a protection plan exists for this product."""
    s = ctx.session
    if "replace" in P.values(s.home, "trigger") and ctx.pack.slot("exchange_old") and not P.known(s.home, "exchange_old"):
        if any(e.category == ctx.pack.category for e in ctx.facts.snap.exchange):
            return _once(ctx, "exchange", ctx.t("tip_exchange"))
    plans = [pl for pl in ctx.facts.snap.protection if pl.category == ctx.pack.category]
    on_bill = {str(ln.get("code", "")) for ln in (s.cost_snapshot or {}).get("lines") or []}
    if plans and not on_bill & {pl.code for pl in plans}:  # not already on the bill as a suggestion
        lo = min(pl.price for pl in plans)
        return _once(ctx, "protection", ctx.t("tip_protection", n=ctx.reg.num(len(plans), f"protection:{ctx.pack.category}"),
                                              low=ctx.reg.inr(lo, f"protection:{ctx.pack.category}")))
    return None


def _close(ctx: SkillContext) -> str | None:
    """The family decides: send the Family Card now; an offer ends before a 'come back' day."""
    s = ctx.session
    if s.extra.get("parivaar_sent"):
        return None
    if any(v in ("family", "together") for v in P.values(s.home, "decider")):
        return _once(ctx, "family_decides", ctx.t("tip_family_card"))
    return None


def tip(ctx: SkillContext) -> str | None:
    """The one line for this message, or None. Cheap: a few fit calls at most."""
    s = ctx.session
    if s is None or ctx.pack is None or ctx.role != "associate":
        return None
    try:
        if s.extra.get("outcome") == "sold":
            if s.extra.get("install_job_id") and ctx.pack.slot("wall_type") and not P.known(s.home, "wall_type") \
                    and "wall" in P.values(s.home, "mounting"):
                return _once(ctx, "wall_type", ctx.t("tip_wall_type"))
            return None
        if s.offer_snapshot or (s.chosen_sku and (s.cost_snapshot or {}).get("sku") == s.chosen_sku):
            return _close(ctx) or _cost(ctx)
        if s.shortlist:
            return _options(ctx)
        return _assess(ctx)
    except Exception:  # a tip must never break a reply
        return None

