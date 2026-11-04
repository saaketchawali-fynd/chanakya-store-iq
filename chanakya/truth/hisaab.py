"""Hisaab — full cost engine (spec 9.5). Every amount goes through the NumberRegistry."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chanakya.brain.packs import Pack, number, values
from chanakya.core.contracts import HomeCard
from chanakya.i18n.lang import pick
from chanakya.truth.facts import FactsService, ProductWithStock
from chanakya.truth.firewall import NumberRegistry

SOUNDBAR_REASON = {"hi": "dialogue saaf", "mr": "संवाद स्पष्ट", "en": "clear dialogue"}
ACC_REASON = {  # why an accessory is suggested: the answer that calls for it (short, on the card)
    "soundbar_arc": {"hi": "dialogue saaf · HDMI ARC: ek cable", "mr": "संवाद स्पष्ट · HDMI ARC: एक केबल",
                     "en": "clear dialogue · HDMI ARC: one cable"},
    "tv_mount": {"hi": "deewar par lagane ke liye", "mr": "भिंतीवर लावण्यासाठी", "en": "for your wall"},
    "stabiliser": {"hi": "voltage ki dikkat ke liye", "mr": "व्होल्टेजच्या त्रासासाठी", "en": "for the voltage swings"},
    "wm_stand": {"hi": "geeli jagah par, zameen se upar", "mr": "ओल्या जागी, जमिनीपासून वर", "en": "keeps it off a wet floor"},
    "laptop_bag": {"hi": "roz saath le jaane ke liye", "mr": "रोज सोबत नेण्यासाठी", "en": "to carry it every day"},
}


class CostLine(BaseModel):
    code: str
    title: str
    amount_str: str
    amount: int
    kind: Literal["product", "zaroori", "sujhav", "optional", "addon"]  # addon: what they added in "Complete it"
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
    protection: dict | None = None  # the matching resQ plan, shown as a choice (not in the totals)
    notes: list[str] = []  # what the install needs that the data has no price for ("Wall mount: store confirms")


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
    addons: dict | None = None,
) -> CostSheet:
    """`addons`: the "Complete it" step's outcome ({added: [{code, title, amount, source, reason}], declined: [codes]}).
    Once it has happened, what they added is in the full cost and nothing they turned down is suggested again."""
    p = facts.product_sync(sku, store_id)
    assert p is not None, f"unknown sku {sku}"
    cat = p.category
    lines: list[CostLine] = []
    sources: list[str] = []
    notes: list[str] = []

    def add(code, title, amount, kind, source, reason=None, qty=None):
        if kind in ("sujhav", "optional", "addon") and code in exclude_codes:  # the customer removed it
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
        elif c.kind == "suggest" and include_suggestions and addons is None and cond_holds(c.condition, home, pack):
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
    decided = addons is not None
    for x in (addons or {}).get("added") or []:  # added in "Complete it": part of the full cost now
        add(x["code"], x["title"], x["amount"], "addon", x["source"], reason=x.get("reason"))
    if include_suggestions and not decided:
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
    codes = " ".join(ln.code.upper() for ln in lines)
    for kind, why in accessory_needs(p, home, pack):
        if any(w in codes for w in {"tv_mount": ("BRACKET", "MOUNT"), "stabiliser": ("STAB",),
                                    "wm_stand": ("STAND", "TROLLEY")}.get(kind, ())):
            continue  # the charges (or what they added) already carry it
        a = pick_accessory(facts, kind, p, store_id)
        if a is None:
            if kind == "tv_mount":
                notes.append("mount_confirm")  # said honestly, whether or not "Complete it" has happened
            continue
        if include_suggestions and not decided:
            add(a.sku, pick(a.title, lang) or a.model, a.price, "sujhav", a.price_source,
                reason=pick(ACC_REASON.get("soundbar_arc" if why == "arc" else kind) or SOUNDBAR_REASON, lang)
                if kind != "soundbar" or why == "arc" else SOUNDBAR_REASON.get(lang))

    zaroori = sum(line.amount for line in lines if line.kind in ("product", "zaroori", "addon"))
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
        protection=_protection(p, await facts.protection(cat), reg, lang,
                               {ln.code for ln in lines} | set((addons or {}).get("declined") or [])),
        notes=notes,
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


def accessory_needs(p: ProductWithStock, home: HomeCard, pack: Pack | None) -> list[tuple[str, str]]:
    """[(accessory kind, why)] the customer's answers call for, for this product (Complete: the full setup)."""
    cat = p.category
    out: list[tuple[str, str]] = []
    if cat == "tv":
        dist = number(home, "seat_distance", pack) if pack else None
        sbv = values(home, "soundbar")
        if "has" not in sbv and (any(v in ("no", "thinking") for v in sbv) or "yes" in values(home, "elders")
                                 or (dist is not None and dist >= 12) or "sound" in (home.asks or [])):
            out.append(("soundbar", "arc" if p.spec("hdmi_arc") is True else "need"))
        if "wall" in values(home, "mounting"):
            out.append(("tv_mount", "wall"))
    elif cat in ("ac", "fridge"):
        slot = "power_issues" if cat == "ac" else "power_cuts"
        if "yes" in values(home, slot) and p.spec("stabiliser_free") is not True:
            out.append(("stabiliser", slot))
    elif cat == "wm":
        if any(v in ("bathroom", "balcony") for v in values(home, "space")):
            out.append(("wm_stand", "wet"))
    elif cat == "laptop":
        if "daily" in values(home, "carry"):
            out.append(("laptop_bag", "carry"))
    return out


def pick_accessory(facts: FactsService, kind: str, p: ProductWithStock, store_id: str | None = None):
    """The accessory of this kind on sale that fits this product (a mount or bag only when its stated size range
    covers it): the same brand first, then the lowest price."""
    pool = [a for a in facts.products_sync(kind, store_id) if a.available
            and (not (a.info or {}).get("for") or p.category in (a.info or {}).get("for"))]
    if kind in ("tv_mount", "laptop_bag"):
        size = p.spec("size_in" if kind == "tv_mount" else "screen_in")

        def fits(a) -> bool:
            rng = (a.info or {}).get("fits_in")
            if not rng:
                return kind == "laptop_bag"  # a bag with no stated size still carries it; a mount must say
            return size is not None and float(rng[0]) <= float(size) <= float(rng[1])

        pool = [a for a in pool if fits(a)]
    ranked = rank_accessories(pool, kind, p)
    return ranked[0] if ranked else None


def rank_accessories(pool: list, kind: str, p: ProductWithStock) -> list:
    """Best first, for this product: the same brand; for a soundbar, HDMI ARC when the TV has it (one cable, one
    remote); kept in proportion to the product (at most 40% of its price when such a choice exists); then the
    lowest price."""
    def key(a):
        same = (a.brand or "").lower() == (p.brand or "").lower()
        arc = kind == "soundbar" and p.spec("hdmi_arc") is True and a.spec("hdmi_arc") is True
        fair = a.price <= 0.4 * p.price
        return (not fair, not same, not arc, a.price, a.sku)

    return sorted(pool, key=key)


def _protection(p: ProductWithStock, plans: list, reg: NumberRegistry, lang: str, used: set[str]) -> dict | None:
    """The resQ plan that covers this product (its price band), the longest cover first: years, price and per day.
    Offered as a choice, never added to the totals until the customer adds it."""
    ok = [pl for pl in plans if filter_holds(pl.filter, p) and pl.code not in used]
    if not ok:
        return None
    pl = sorted(ok, key=lambda x: (-int(x.years or 0), x.price))[0]
    days = max(1, int(pl.years or 1) * 365)
    return {"code": pl.code, "title": pick(pl.title, lang, reg), "years": int(pl.years or 0),
            "price_str": reg.inr(pl.price, f"protection:{pl.code}"),
            "per_day_str": reg.inr(max(1, round(pl.price / days)), f"calc:protection_day:{pl.code}")}
