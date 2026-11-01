"""Next-best question (spec 10.6): the unknown slot whose answer most changes the top 3."""

from __future__ import annotations

from datetime import UTC, datetime

from chanakya.brain.fit import fit
from chanakya.brain.options import candidate_set
from chanakya.brain.packs import Pack, eval_slot_cond, known, set_slot
from chanakya.core.contracts import HomeCard


def _top3(cands: list, home: HomeCard, pack: Pack, guides: dict) -> set[str]:
    scored = sorted(cands, key=lambda p: (-fit(p, home, pack, guides).score, p.price))
    return {p.sku for p in scored[:3]}


def _life_known(home: HomeCard, pack: Pack) -> int:
    return sum(1 for s in pack.slots if s.phase == "life" and known(home, s.id))


def candidates(home: HomeCard, pack: Pack, options_shown: bool) -> list:
    out = []
    life_known = _life_known(home, pack)
    for s in pack.slots:
        if known(home, s.id):
            continue
        if s.ask_if and not eval_slot_cond(
            home, s.ask_if["slot"], {k: v for k, v in s.ask_if.items() if k != "slot"}, pack
        ):
            continue
        if s.phase == "site" and not options_shown:
            continue
        if s.phase == "tech" and life_known < 3:
            continue
        out.append(s)
    return out


def score_slots(
    home: HomeCard,
    pack: Pack,
    products: list,
    guides: dict,
    questions_asked: int,
    options_shown: bool,
    exclude: set[str] | None = None,
) -> list[tuple[str, float]]:
    cands = [s for s in candidates(home, pack, options_shown) if not exclude or s.id not in exclude]
    if not cands:
        return []
    pool = candidate_set(products, home)
    current = _top3(pool, home, pack, guides)
    at = datetime.now(UTC)
    out = []
    for s in cands:
        change = 0.0
        for val, p in (s.priors or {}).items():
            h2 = home.model_copy(deep=True)
            set_slot(h2, pack, s.id, val, s.value_number(val), "default", at)
            t3 = _top3(pool, h2, pack, guides)
            change += p * (1 - len(t3 & current) / 3)
        score = (
            change
            + 0.3 * (s.phase == "life" and questions_asked < 3)
            + 0.2 * (s.id in pack.required_slots)
            + 0.15 * (s.id in pack.cost_slots)
        )
        out.append((s.id, round(score, 4)))
    order = {s.id: i for i, s in enumerate(pack.slots)}
    return sorted(out, key=lambda x: (-x[1], order[x[0]]))


def ordered_next(home: HomeCard, pack: Pack, exclude: set[str] | None = None, phase: str | None = None) -> str | None:
    """The Assess sequence: the first question in the pack's fixed order (why now, then the home) that is not known
    yet and whose condition holds. None once the order is done."""
    for sid in pack.order:
        sd = pack.slot(sid)
        if sd is None or known(home, sid) or (exclude and sid in exclude) or (phase and sd.phase != phase):
            continue
        if sd.ask_if and not eval_slot_cond(home, sd.ask_if["slot"], {k: v for k, v in sd.ask_if.items() if k != "slot"}, pack):
            continue
        return sid
    return None


def next_slot(
    home: HomeCard,
    pack: Pack,
    products: list,
    guides: dict,
    questions_asked: int = 0,
    options_shown: bool = False,
    exclude: set[str] | None = None,
    phase: str | None = None,
) -> str | None:
    ranked = score_slots(home, pack, products, guides, questions_asked, options_shown, exclude)
    if phase:
        ranked = [r for r in ranked if pack.slot(r[0]).phase == phase]
    return ranked[0][0] if ranked else None
