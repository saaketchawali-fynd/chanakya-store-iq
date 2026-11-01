"""Fit score per SKU (spec 10.4): weighted match of pack fit_rules against the Home Card."""

from __future__ import annotations

from dataclasses import dataclass

from chanakya.brain.packs import Pack, eval_slot_cond, eval_spec_any, eval_spec_cond, known, number, values
from chanakya.core.contracts import HomeCard

TV_SIZE_LADDER = [32, 43, 50, 55, 65, 75, 85]


@dataclass
class Contribution:
    rule_id: str
    weight: float
    match: float


@dataclass
class FitResult:
    score: int
    contributions: list[Contribution]
    excluded: bool = False

    def top_rule(self) -> str | None:
        best = sorted((c for c in self.contributions if c.match > 0), key=lambda c: -(c.weight * c.match))
        return best[0].rule_id if best else None


def tv_size_range(home: HomeCard, pack: Pack, guides: dict) -> tuple[float, float] | None:
    d = number(home, "seat_distance", pack)
    if d is None:
        return None
    for row in guides.get("size_tv") or []:
        if d <= row["max_ft"]:
            return float(row["min_in"]), float(row["max_in"])
    return None


def ac_tons_needed(home: HomeCard, pack: Pack, guides: dict) -> float | None:
    sq = number(home, "room_size", pack)
    if sq is None:
        return None
    ton = None
    for row in guides.get("tonnage_ac") or []:
        if sq <= row["max_sqft"]:
            ton = float(row["ton"])
            break
    if ton is None:
        return None
    if "yes" in values(home, "sun_exposure"):
        ton = min(2.0, ton + 0.5)
    return ton


def wm_kg_range(home: HomeCard, pack: Pack, guides: dict) -> tuple[float, float] | None:
    n = number(home, "family_size", pack)
    if n is None:
        return None
    for row in guides.get("capacity_wm") or []:
        if n <= row["max_people"]:
            lo, hi = float(row["min_kg"]), float(row["max_kg"])
            if "bedsheets" in values(home, "wash_load"):
                lo, hi = lo + 1, hi + 1
            return lo, hi
    return None


def fridge_l_range(home: HomeCard, pack: Pack, guides: dict) -> tuple[float, float] | None:
    n = number(home, "family_size", pack)
    if n is None:
        return None
    for row in guides.get("capacity_fridge") or []:
        if n <= row["max_people"]:
            lo, hi = float(row["min_l"]), float(row["max_l"])
            if any(v in ("weekly", "frozen") for v in values(home, "shopping")):
                lo, hi = lo + 30, hi + 40
            return lo, hi
    return None


def in_band(product, home: HomeCard, pack: Pack, guides: dict) -> float | None:
    """1 inside the guide band, 0.5 one step outside, 0 else; None if the home fact is unknown."""
    cat = pack.category
    if cat == "tv":
        rng = tv_size_range(home, pack, guides)
        size = product.spec("size_in")
        if rng is None or size is None:
            return None
        lo, hi = rng
        if lo <= size <= hi:
            return 1.0
        below = [s for s in TV_SIZE_LADDER if s < lo]
        above = [s for s in TV_SIZE_LADDER if s > hi]
        if (below and size == below[-1]) or (above and size == above[0]):
            return 0.5
        return 0.0
    if cat == "ac":
        need = ac_tons_needed(home, pack, guides)
        ton = product.spec("ton")
        if need is None or ton is None:
            return None
        diff = abs(float(ton) - need)
        return 1.0 if diff < 0.01 else 0.5 if diff <= 0.5 else 0.0
    if cat == "wm":
        rng = wm_kg_range(home, pack, guides)
        kg = product.spec("capacity_kg")
        if rng is None or kg is None:
            return None
        lo, hi = rng
        if lo <= kg <= hi:
            return 1.0
        return 0.5 if (lo - 1.5) <= kg <= (hi + 1.5) else 0.0
    if cat == "fridge":
        rng = fridge_l_range(home, pack, guides)
        litres = product.spec("capacity_l")
        if rng is None or litres is None:
            return None
        lo, hi = rng
        if lo <= litres <= hi:
            return 1.0
        return 0.5 if (lo - 60) <= litres <= (hi + 80) else 0.0
    return None


def _rule_applies(rule, home: HomeCard, pack: Pack) -> bool:
    if rule.kind == "width_fit":
        return number(home, rule.slot, pack) is not None
    if not known(home, rule.slot):
        return False
    return eval_slot_cond(home, rule.slot, rule.when, pack) if rule.when else True


def _evaluate(rule, product, home: HomeCard, pack: Pack, guides: dict) -> float | None:
    if rule.kind in ("size_band", "ton_band", "capacity_band"):
        return in_band(product, home, pack, guides)
    if rule.kind == "width_fit":
        niche = number(home, rule.slot, pack)
        w = product.spec("width_mm")
        if niche is None or w is None:
            return None
        return 1.0 if float(w) <= niche * 10 else 0.0
    if rule.tiers:
        for tier in rule.tiers:
            if all(eval_spec_cond(product, home, c, pack, guides) for c in tier.get("spec_all", [])):
                return float(tier.get("score", 1.0))
        return rule.else_
    if rule.spec_any:
        return 1.0 if eval_spec_any(product, home, rule.spec_any, pack, guides) else rule.else_
    return None


def fit(product, home: HomeCard, pack: Pack, guides: dict) -> FitResult:
    num = den = 0.0
    contribs: list[Contribution] = []
    excluded = False
    for rule in pack.fit_rules:
        if not _rule_applies(rule, home, pack):
            continue
        m = _evaluate(rule, product, home, pack, guides)
        if m is None:
            continue
        if rule.hard and m <= 0:
            excluded = True
        num += rule.weight * m
        den += rule.weight
        contribs.append(Contribution(rule.id, rule.weight, m))
    score = round(100 * num / den) if den else 50
    return FitResult(score=score, contributions=contribs, excluded=excluded)
