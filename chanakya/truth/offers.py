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


BUNDLE_KEYS = ("with_tv_panel_in", "with_tv_size_min", "with_tv_size_max")


def bundle_holds(o: Offer, main) -> bool:
    """A bundle offer's rule about the TV in the same bill ('with_tv_panel_in', 'with_tv_size_min' …), checked
    against the TV being bought with it. A rule we cannot check is never claimed."""
    flt = o.filter or {}
    keys = [k for k in flt if k.startswith("with_tv")]
    if not keys or main is None or main.category != "tv" or any(k not in BUNDLE_KEYS for k in keys):
        return False
    size = main.spec("size_in")
    if "with_tv_panel_in" in flt and str(main.spec("panel")) not in [str(x) for x in flt["with_tv_panel_in"]]:
        return False
    if "with_tv_size_min" in flt and (size is None or float(size) < float(flt["with_tv_size_min"])):
        return False
    if "with_tv_size_max" in flt and (size is None or float(size) > float(flt["with_tv_size_max"])):
        return False
    return True


def eligible(o: Offer, product, home: HomeCard, bank: str | None, emi: bool, today: date, with_main=None,
             bill_total: int | None = None) -> bool:
    """`with_main`: the main product in the same bill (a bundle offer on an add-on names it); `bill_total`: the
    bill's amount in this offer's categories, for an 'on bills over ₹X' offer (else this product's price)."""
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
    if any(k.startswith("with_tv") for k in flt) and not bundle_holds(o, with_main):  # a TV in the same bill
        return False
    if flt.get("skus") and product.sku not in flt["skus"]:
        return False
    if flt.get("brands") and product.brand not in flt["brands"]:
        return False
    if (o.min_amount or 0) > (bill_total if bill_total is not None else product.price):
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


def combo_offers(offers: list[Offer], main, addons: list, home: HomeCard, today: date) -> list[dict]:
    """Offers that only apply when the products are bought together, from the offer rows alone:
      - a bundle offer on an add-on whose rule names the TV in the same bill ("₹3,000 off a soundbar with the TV");
      - an 'on bills over ₹X' offer the main product alone does not reach, but the bill with the add-ons in that
        offer's own categories does (items outside its categories never count).
    Bank offers say which bank. [{offer, amount, on: [sku], kind: 'bundle' | 'bill', bank}] biggest first, one per
    stack group."""
    best: dict[str, dict] = {}
    for o in offers:
        bank = o.bank if (o.kind in ("bank", "cashback") or o.bank) else None
        for a in addons:  # bundles: the add-on, with this TV
            if any(k.startswith("with_tv") for k in (o.filter or {})) and \
                    eligible(o, a, home, bank, False, today, with_main=main):
                d = discount(o, a.price)
                if d > 0:
                    cand = {"offer": o, "amount": d, "on": [a.sku], "kind": "bundle", "bank": bank}
                    if o.stack_group not in best or d > best[o.stack_group]["amount"]:
                        best[o.stack_group] = cand
        if (o.min_amount or 0) > main.price and main.category in (o.categories or []):  # a bill the combo crosses
            items = [main] + [a for a in addons if a.category in (o.categories or [])]
            total = sum(x.price for x in items)
            if len(items) > 1 and eligible(o, main, home, bank, False, today, bill_total=total):
                d = discount(o, total)
                if d > 0 and (o.stack_group not in best or d > best[o.stack_group]["amount"]):
                    best[o.stack_group] = {"offer": o, "amount": d, "on": [x.sku for x in items], "kind": "bill",
                                           "bank": bank}
    return sorted(best.values(), key=lambda c: -c["amount"])


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
