"""Demo recipe selection (spec 10.7): the one difference worth showing between B and its alternatives."""

from __future__ import annotations

from chanakya.brain.fit import fit
from chanakya.brain.options import OptionSet
from chanakya.brain.packs import Demo, Pack, eval_slots_map
from chanakya.core.contracts import HomeCard

BAND_KINDS = {"size_band": "size_in", "ton_band": "ton", "capacity_band": "capacity_kg"}


def _rule_for_spec(pack: Pack, spec_key: str):
    for r in pack.fit_rules:
        if BAND_KINDS.get(r.kind or "") == spec_key:
            return r
        keys = {k for cond in r.spec_any for k in cond} | {k for t in r.tiers for c in t.get("spec_all", []) for k in c}
        if spec_key in keys:
            return r
    return None


def rank_demos(opts: OptionSet, home: HomeCard, pack: Pack, guides: dict) -> list[tuple[Demo, str, float]]:
    """Returns [(demo, other_label, score)] best first."""
    best = opts.by_role("best")
    if not best:
        return []
    others = [o for o in (opts.by_label("A"), opts.by_label("C")) if o and o.role != "best"]
    fb = {c.rule_id: c.match for c in fit(best.product, home, pack, guides).contributions}
    out: list[tuple[Demo, str, float]] = []
    for d in pack.demos:
        if not d.differs_on or not eval_slots_map(home, d.when_slots, pack):
            continue
        rule = _rule_for_spec(pack, d.differs_on)
        top, top_label = 0.0, None
        for o in others:
            if best.product.spec(d.differs_on) == o.product.spec(d.differs_on):
                continue
            fo = {c.rule_id: c.match for c in fit(o.product, home, pack, guides).contributions}
            # signed: a demo only where the best pick really is better (its line says "{best} … better")
            s = (rule.weight * (fb.get(rule.id, 0) - fo.get(rule.id, 0))) if rule else 0.0
            if s > top or top_label is None:
                top, top_label = s, o.label
        if top_label is not None and top > 0:
            out.append((d, top_label, top))
    out.sort(key=lambda x: -x[2])
    # fallbacks
    sizes_differ = any(o.product.spec("size_in") != best.product.spec("size_in") for o in others)
    for fid in (["distance_demo"] if sizes_differ else []) + ["own_content"]:
        d = pack.demo(fid)
        if d and all(x[0].id != fid for x in out):
            alt = next((o.label for o in others), "A")
            out.append((d, alt, 0.0))
    neutral = [d for d in pack.demos if not d.differs_on]  # a last resort that claims nothing about either
    if not out and neutral:
        out.append((neutral[-1], others[0].label if others else "A", 0.0))
    return out


def pick_demo(opts: OptionSet, home: HomeCard, pack: Pack, guides: dict, skip: set[str] | None = None):
    ranked = [r for r in rank_demos(opts, home, pack, guides) if not skip or r[0].id not in skip]
    return ranked[0] if ranked else None
