"""Hisaab — full cost engine (spec 9.5). Every amount goes through the NumberRegistry."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chanakya.brain.packs import Pack, number, values
from chanakya.core.contracts import HomeCard
from chanakya.i18n.lang import pick
from chanakya.truth.facts import FactsService, ProductWithStock
from chanakya.truth.firewall import NumberRegistry

SOUNDBAR_SKU = "SB-MER-21"
SOUNDBAR_REASON = {"hi": "dialogue saaf", "mr": "संवाद स्पष्ट", "en": "clear dialogue"}


class CostLine(BaseModel):
    code: str
    title: str
    amount_str: str
    amount: int
    kind: Literal["product", "zaroori", "sujhav", "optional"]
    reason: str | None = None
    qty: float | None = None


class CostSheet(BaseModel):
    sku: str
    lines: list[CostLine]
    zaroori_total: int
    with_suggestions_total: int
    zaroori_total_str: str
    with_suggestions_total_str: str
    exchange_range: tuple[str, str] | None = None
    lifetime: dict | None = None
    sources: list[str] = []
    optional_count: int = 0


def _num(home: HomeCard, pack: Pack | None, name: str) -> float | None:
    if pack is None:
        return None
    sid = pack.slot_for_number(name) or name
    return number(home, sid, pack)


def cond_holds(cond: dict | None, home: HomeCard, pack: Pack | None) -> bool:
    if not cond:
        return True
    if "slot" in cond:
        vals = values(home, cond["slot"])
        if not vals:
            return False
        if "eq" in cond and str(cond["eq"]) not in vals:
            return False
        if "in" in cond and not any(v in cond["in"] for v in vals):
            return False
        if "has" in cond and cond["has"] not in vals:
            return False
        return True
    if "number" in cond:
        n = _num(home, pack, cond["number"])
        if n is None:
            return False
        for op in ("gt", "gte", "lt", "lte"):
            if op in cond:
                a = cond[op]
                if not {"gt": n > a, "gte": n >= a, "lt": n < a, "lte": n <= a}[op]:
                    return False
        return True
    return True


def filter_holds(flt: dict | None, p: ProductWithStock) -> bool:
    if not flt:
        return True
    size = p.spec("size_in")
    if "size_in_max" in flt and (size is None or size > flt["size_in_max"]):
        return False
    if "size_in_min" in flt and (size is None or size < flt["size_in_min"]):
        return False
    for k, v in (flt.get("spec") or {}).items():  # e.g. {spec: {stabiliser_free: false}}
        if p.spec(k) != v:
            return False
    if "price_min" in flt and p.price < flt["price_min"]:  # e.g. a resQ plan for products of ₹20,000–30,000
        return False
    if "price_max" in flt and p.price > flt["price_max"]:
        return False
    return True


async def compute(
    sku: str,
    home: HomeCard,
    facts: FactsService,
    pack: Pack | None,
    reg: NumberRegistry,
    lang: str = "hi",
    include_optional: bool = False,
    include_suggestions: bool = True,
    store_id: str | None = None,
    exclude_codes: list[str] | tuple[str, ...] = (),
) -> CostSheet:
    p = facts.product_sync(sku, store_id)
    assert p is not None, f"unknown sku {sku}"
    cat = p.category
    lines: list[CostLine] = []
    sources: list[str] = []

    def add(code, title, amount, kind, source, reason=None, qty=None):
        if kind in ("sujhav", "optional") and code in exclude_codes:  # the customer removed it
            return
        lines.append(
            CostLine(
                code=code,
                title=title,
                amount=int(round(amount)),
                amount_str=reg.inr(amount, source),
                kind=kind,
                reason=reason,
                qty=qty,
            )
        )
        sources.append(source)

    add(p.sku, pick(p.title, lang) or p.model, p.price, "product", p.price_source)
    optional_count = 0
    for c in await facts.charges(cat):
        if not filter_holds(c.filter, p):
            continue
        title = pick(c.title, lang, reg)
        reason = pick(c.reason, lang, reg) if c.reason else None
        if c.kind == "mandatory":
            add(c.code, title, c.amount, "zaroori", f"charge:{c.code}")
        elif c.kind == "conditional" and cond_holds(c.condition, home, pack):
            if c.unit == "per_ft":
                n = _num(home, pack, (c.condition or {}).get("number", "pipe_run_ft")) or 0
                q = max(0.0, n - (c.included_qty or 0))
                if q <= 0:
                    continue
                reg.num(q, f"card:{(c.condition or {}).get('number')}", "ft")
                add(c.code, title, q * c.amount, "zaroori", f"charge:{c.code}", qty=q)
            else:
                add(c.code, title, c.amount, "zaroori", f"charge:{c.code}")
        elif c.kind == "suggest" and include_suggestions and cond_holds(c.condition, home, pack):
            add(c.code, title, c.amount, "sujhav", f"charge:{c.code}", reason=reason)
        elif c.kind == "optional":
            optional_count += 1
            if include_optional:
                add(c.code, title, c.amount, "optional", f"charge:{c.code}")
    for pl in await facts.protection(cat):  # plans from the catalogue (no coaching reason): offered, not pushed
        if not pl.suggest_if and filter_holds(pl.filter, p):
            optional_count += 1
            if include_optional:
                add(pl.code, pick(pl.title, lang, reg), pl.price, "optional", f"protection:{pl.code}")
    if include_suggestions:
        for pl in await facts.protection(cat):
            if filter_holds(pl.filter, p) and pl.suggest_if and cond_holds(pl.suggest_if, home, pack):
                add(
                    pl.code,
                    pick(pl.title, lang, reg),
                    pl.price,
                    "sujhav",
                    f"protection:{pl.code}",
                    reason=pick(pl.reason, lang, reg),
                )
        if cat == "tv":
            dist = number(home, "seat_distance", pack) if pack else None
            sbv = values(home, "soundbar")
            wants = any(v in ("no", "thinking") for v in sbv)
            if "has" not in sbv and (wants or "yes" in values(home, "elders") or (dist is not None and dist >= 12)):
                sb = facts.product_sync(SOUNDBAR_SKU, store_id)
                if sb and sb.available:
                    add(
                        sb.sku,
                        pick(sb.title, lang) or sb.model,
                        sb.price,
                        "sujhav",
                        sb.price_source,
                        reason=SOUNDBAR_REASON.get(lang),
                    )

    zaroori = sum(line.amount for line in lines if line.kind in ("product", "zaroori"))
    with_sug = zaroori + sum(line.amount for line in lines if line.kind == "sujhav")
    exch = None
    old = values(home, "exchange_old")
    if any(v in ("yes_working", "yes_dead") for v in old):
        cond = "working" if "yes_working" in old else "not_working"
        r = await facts.exchange(cat, cond, p.spec("size_in"))
        if r:
            exch = (reg.inr(r[0], f"exchange:{r[2]}"), reg.inr(r[1], f"exchange:{r[2]}"))
            sources.append(f"exchange:{r[2]}")
    lifetime = await lifetime_cost(p, facts, reg, store_id) if cat in ("ac", "wm", "fridge") else None
    return CostSheet(
        sku=sku,
        lines=lines,
        zaroori_total=zaroori,
        with_suggestions_total=with_sug,
        zaroori_total_str=reg.inr(zaroori, f"calc:hisaab:{sku}:zaroori"),
        with_suggestions_total_str=reg.inr(with_sug, f"calc:hisaab:{sku}:with_suggestions"),
        exchange_range=exch,
        lifetime=lifetime,
        sources=sources,
        optional_count=optional_count,
    )


async def lifetime_cost(
    p: ProductWithStock, facts: FactsService, reg: NumberRegistry, store_id: str | None = None
) -> dict | None:
    kwh = p.spec("kwh_year")
    tariff = (facts.guide_sync("tariff") or {}).get("rupees_per_unit")
    if not kwh or not tariff:
        return None
    monthly = kwh * tariff / 12
    five = kwh * tariff * 5
    out = {
        "monthly": round(monthly),
        "five_year": round(five),
        "monthly_str": reg.inr(monthly, f"calc:energy:{p.sku}"),
        "five_year_str": reg.inr(five, f"calc:energy:{p.sku}"),
    }
    key = {"ac": "ton", "fridge": "capacity_l"}.get(p.category, "capacity_kg")
    alts = [
        q
        for q in facts.products_sync(p.category, store_id)
        if q.sku != p.sku and q.spec(key) == p.spec(key) and q.price < p.price and q.spec("kwh_year")
    ]
    if alts:
        alt = min(alts, key=lambda q: q.price)
        alt_five = alt.spec("kwh_year") * tariff * 5
        saving = alt_five - five
        out.update(
            alt_sku=alt.sku,
            alt_model=alt.model,
            alt_five_year=round(alt_five),
            alt_five_year_str=reg.inr(alt_five, f"calc:energy:{alt.sku}"),
            five_year_saving=round(saving),
            five_year_saving_str=reg.inr(abs(saving), f"calc:energy_diff:{p.sku}"),
            price_diff_str=reg.inr(p.price - alt.price, f"calc:price_diff:{p.sku}:{alt.sku}"),
        )
    return out
