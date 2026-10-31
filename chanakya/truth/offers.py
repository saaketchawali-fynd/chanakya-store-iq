"""Offers engine (spec 9.6): eligibility + best stack (≤ 1 per stack_group). Never invents a discount."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from chanakya.brain.packs import values
from chanakya.core.contracts import HomeCard
from chanakya.i18n.lang import pick
from chanakya.truth import fmt
from chanakya.truth.facts import FactsService
from chanakya.truth.firewall import NumberRegistry
from chanakya.truth.models import Offer


class AppliedOffer(BaseModel):
    id: str
    title: str
    amount: int
    amount_str: str
    valid_to: date
    valid_to_str: str
    stack_group: str


class OfferResult(BaseModel):
    sku: str
    price: int
    price_str: str
    applied: list[AppliedOffer]
    payable: int
    payable_str: str
    discount_total: int


def eligible(o: Offer, product, home: HomeCard, bank: str | None, emi: bool, today: date) -> bool:
    if not (o.valid_from <= today <= o.valid_to):
        return False
    if product.category not in (o.categories or []):
        return False
    flt = o.filter or {}
    size = product.spec("size_in")
    if "size_in_min" in flt and (size is None or size < flt["size_in_min"]):
        return False
    if "size_in_max" in flt and (size is None or size > flt["size_in_max"]):
        return False
    if any(k.startswith("with_tv") for k in flt):  # bundle offers need a TV in the same bill (not modelled)
        return False
    if flt.get("skus") and product.sku not in flt["skus"]:
        return False
    if flt.get("brands") and product.brand not in flt["brands"]:
        return False
    if (o.min_amount or 0) > product.price:
        return False
    req = o.requires or {}
    if req.get("slot"):
        vals = values(home, req["slot"])
        if not any(v in req.get("in", []) for v in vals):
            return False
    if o.kind in ("bank", "cashback") or o.bank:
        if not bank or not o.bank or bank.upper() != o.bank.upper():
            return False
    if emi and not o.with_emi:
        return False
    return True


def discount(o: Offer, price: int) -> int:
    if o.pct:
        d = price * float(o.pct) / 100
        if o.max_discount:
            d = min(d, o.max_discount)
        return int(round(d))
    return int(o.flat or 0)


async def best_stack(
    sku: str,
    home: HomeCard,
    facts: FactsService,
    reg: NumberRegistry,
    today: date,
    bank: str | None = None,
    emi: bool = False,
    lang: str = "hi",
) -> OfferResult:
    p = facts.product_sync(sku)
    assert p is not None
    best: dict[str, tuple[Offer, int]] = {}
    for o in await facts.offers(p.category, today):
        if not eligible(o, p, home, bank, emi, today):
            continue
        d = discount(o, p.price)
        if d > 0 and (o.stack_group not in best or d > best[o.stack_group][1]):
            best[o.stack_group] = (o, d)
    applied = []
    for o, d in sorted(best.values(), key=lambda x: -x[1]):
        applied.append(
            AppliedOffer(
                id=o.id,
                title=pick(o.title, lang, reg),
                amount=d,
                amount_str=reg.inr(d, f"offer:{o.id}"),
                valid_to=o.valid_to,
                valid_to_str=reg.raw(fmt.date_short(o.valid_to, lang), f"offer:{o.id}:valid_to"),
                stack_group=o.stack_group,
            )
        )
    total = sum(a.amount for a in applied)
    payable = p.price - total
    return OfferResult(
        sku=sku,
        price=p.price,
        price_str=reg.inr(p.price, p.price_source),
        applied=applied,
        payable=payable,
        payable_str=reg.inr(payable, f"calc:payable:{sku}"),
        discount_total=total,
    )
