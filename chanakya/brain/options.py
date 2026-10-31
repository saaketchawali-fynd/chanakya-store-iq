"""Options (spec 10.5): Best fit / Value fit / Step up. Labels A = value, B = best, C = step up."""

from __future__ import annotations

from dataclasses import dataclass

from chanakya.brain.fit import FitResult, fit
from chanakya.brain.packs import Pack
from chanakya.core.contracts import HomeCard


@dataclass
class Option:
    label: str  # A | B | C
    role: str  # value | best | step
    product: object  # ProductWithStock
    fit: FitResult
    reason_key: str  # key in pack.reasons


@dataclass
class OptionSet:
    options: list[Option]

    def by_label(self, label: str) -> Option | None:
        return next((o for o in self.options if o.label == label), None)

    def by_role(self, role: str) -> Option | None:
        return next((o for o in self.options if o.role == role), None)


def candidate_set(products: list, home: HomeCard, stretch: float = 1.75) -> list:
    L = home.start_level_inr
    avail = [p for p in products if p.qty > 0 or p.nearby_qty > 0]
    return [p for p in avail if L is None or p.price <= stretch * L]


def compute_options(products: list, home: HomeCard, pack: Pack, guides: dict) -> OptionSet | None:
    L = home.start_level_inr
    in_stock = [p for p in products if p.qty > 0 or p.nearby_qty > 0]
    fits = {p.sku: fit(p, home, pack, guides) for p in in_stock}
    in_stock = [p for p in in_stock if not fits[p.sku].excluded]
    band = [p for p in in_stock if L is None or p.price <= 1.3 * L]
    if not band:
        band = sorted(in_stock, key=lambda p: p.price)[:3]
    if not band:
        return None
    best = sorted(band, key=lambda p: (-fits[p.sku].score, p.price))[0]
    rest = [p for p in band if p.sku != best.sku]
    good = [p for p in rest if fits[p.sku].score >= 0.6 * fits[best.sku].score]
    value = (
        sorted(good, key=lambda p: (p.price, -fits[p.sku].score))[0]
        if good
        else (sorted(rest, key=lambda p: (-fits[p.sku].score, p.price))[0] if rest else None)
    )
    used = {best.sku} | ({value.sku} if value else set())
    step_pool = [
        p for p in in_stock if p.price > best.price and p.sku not in used and (L is None or p.price <= 1.75 * L)
    ]
    step = (
        sorted(step_pool, key=lambda p: (-fits[p.sku].score, -(p.spec("size_in") or 0), p.price))[0]
        if step_pool
        else None
    )

    opts: list[Option] = []
    if value:
        opts.append(Option("A", "value", value, fits[value.sku], "value_fit"))
    opts.append(Option("B", "best", best, fits[best.sku], _best_reason(best, value, fits, pack)))
    if step:
        opts.append(Option("C", "step", step, fits[step.sku], "step_up"))
    # relabel if A is missing so labels stay A/B/C in order
    for opt, lab in zip(opts, "ABC", strict=False):
        opt.label = lab
    return OptionSet(opts)


def _best_reason(best, value, fits: dict[str, FitResult], pack: Pack) -> str:
    """Top contributing rule; prefer one where best beats the value option (the difference to show)."""
    fb = fits[best.sku]
    if value:
        fv = {c.rule_id: c.match for c in fits[value.sku].contributions}
        diffs = sorted(
            (c for c in fb.contributions if c.match > fv.get(c.rule_id, 0)),
            key=lambda c: -(c.weight * (c.match - fv.get(c.rule_id, 0))),
        )
        if diffs and diffs[0].rule_id in pack.reasons:
            return diffs[0].rule_id
    top = fb.top_rule()
    return top if top in pack.reasons else "value_fit"
