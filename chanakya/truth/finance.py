"""Finance / EMI math (spec 9.7). The full cost of finance is always shown honestly."""

from __future__ import annotations

import math

from pydantic import BaseModel

from chanakya.i18n.lang import pick
from chanakya.truth.facts import FactsService
from chanakya.truth.firewall import NumberRegistry
from chanakya.truth.models import FinancePlan


class FinanceQuote(BaseModel):
    plan_id: str
    kind: str
    title: str
    months: int
    monthly: int
    fee: int
    total: int
    extra: int
    monthly_str: str = ""
    fee_str: str = ""
    total_str: str = ""
    extra_str: str = ""
    months_str: str = ""


def quote(plan: FinancePlan, payable: int) -> FinanceQuote:
    n = plan.months
    if plan.kind == "no_cost" or not plan.apr_pct:
        monthly = math.ceil(payable / n)
        fee = int(plan.fee_flat or 0)
    else:
        r = float(plan.apr_pct) / 1200
        monthly = math.ceil(payable * r * (1 + r) ** n / ((1 + r) ** n - 1))
        fee = int(round(max(plan.fee_min or 0, payable * float(plan.fee_pct or 0) / 100)))
    total = monthly * n + fee
    return FinanceQuote(
        plan_id=plan.id,
        kind=plan.kind,
        title="",
        months=n,
        monthly=monthly,
        fee=fee,
        total=total,
        extra=total - payable,
    )


def is_eligible(plan: FinancePlan, payable: int, bank: str | None, category: str) -> bool:
    if (plan.min_amount or 0) > payable:
        return False
    if plan.categories and category not in plan.categories:
        return False
    if plan.banks and (not bank or bank.upper() not in [b.upper() for b in plan.banks]):
        return False
    return True


def register(q: FinanceQuote, reg: NumberRegistry, lang: str, plan: FinancePlan) -> FinanceQuote:
    src = f"calc:emi:{q.plan_id}"
    q.title = pick(plan.title, lang, reg)
    q.monthly_str = reg.inr(q.monthly, src)
    q.fee_str = reg.inr(q.fee, src)
    q.total_str = reg.inr(q.total, src)
    q.extra_str = reg.inr(q.extra, src)
    q.months_str = reg.num(q.months, src)
    return q


async def best_plan(
    category: str, payable: int, bank: str | None, facts: FactsService, reg: NumberRegistry, lang: str = "hi"
) -> tuple[FinanceQuote | None, FinanceQuote | None]:
    """Best plan = the longest no-cost plan the customer is eligible for (lowest monthly; ties → smallest
    extra); else the standard plan with the smallest extra. Alternative = the 6-month plan (if different)."""
    plans = [p for p in await facts.finance(category) if is_eligible(p, payable, bank, category)]
    if not plans:
        return None, None
    quotes = {p.id: (p, quote(p, payable)) for p in plans}
    no_cost = [x for x in quotes.values() if x[0].kind == "no_cost"]
    if no_cost:
        plan, q = sorted(no_cost, key=lambda x: (-x[1].months, x[1].extra))[0]
    else:
        plan, q = sorted(quotes.values(), key=lambda x: x[1].extra)[0]
    best = register(q, reg, lang, plan)
    alt = None
    six = next((x for x in quotes.values() if x[0].months == 6 and x[0].id != plan.id), None)
    if six:
        alt = register(six[1], reg, lang, six[0])
    return best, alt
