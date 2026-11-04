"""Load + validate category packs (spec 10.1) and Home Card helpers / condition DSL.

Slot conditions: {in: [..]}, {has_any: [..]}, {has: x}, {eq: x}, {known: true}, {gte|lte|gt|lt: n}
Spec conditions: {key: {gte|lte|gt|lt|eq|in|nonempty|in_band|in_size_band: v}} over specs → dims → energy."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from chanakya.core.contracts import HomeCard, SlotValue

HERE = Path(__file__).resolve().parent / "packs"
LANGS = ("hi", "mr", "en")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
NUMBER_TO_SLOT_FALLBACK = {"wall_width_ft": "wall_width_ft", "niche_width_cm": "niche_width_cm"}


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class Quick(_M):
    id: str
    title: dict[str, str]
    number: float | None = None


class Band(_M):
    max: float
    value: str


class SlotDef(_M):
    id: str
    phase: str = "life"
    multi: bool = False
    exclusive: list[list[str]] = []  # multi slots: values in one group replace each other (e.g. how often)
    title: dict[str, str] = {}
    ask: dict[str, str]
    why: dict[str, str]
    quick: list[Quick] = []
    values: list[str] = []
    value_titles: dict[str, dict[str, str]] = {}
    priors: dict[str, float] = {}
    bands: list[Band] = []
    number: str | None = None
    ask_if: dict | None = None
    keywords: dict[str, list[str]] = {}
    alt: dict | None = None  # {ask, why, quick: [values]}: asked this way when only the products' highlights act on it

    def value_title(self, value: str, lang: str) -> str:
        vt = self.value_titles.get(value)
        if vt:
            return vt.get(lang) or vt.get("en") or value
        for q in self.quick:
            if q.id == value:
                return q.title.get(lang) or value
        return value

    def band_value(self, n: float) -> str | None:
        for b in self.bands:
            if n <= b.max:
                return b.value
        return self.bands[-1].value if self.bands else None

    def value_number(self, value: str) -> float | None:
        """Representative number for a band value (quick number, else band midpoint)."""
        for q in self.quick:
            if q.id == value and q.number is not None:
                return q.number
        prev = 0.0
        for i, b in enumerate(self.bands):
            if b.value == value:
                if i == len(self.bands) - 1 and b.max >= 50:
                    return prev + 2
                return (prev + b.max) / 2 if prev else max(b.max - 1, b.max * 0.8)
            prev = b.max
        return None


class FitRule(_M):
    id: str
    weight: float
    slot: str
    kind: str | None = None
    when: dict | None = None
    spec_any: list[dict] = []
    tiers: list[dict] = []
    hard: bool = False
    else_: float = Field(default=0.0, alias="else")


class Benefit(_M):
    id: str
    weight: float = 1.0
    when_spec: dict = {}
    when_spec_any: list[dict] = []
    when_slots: dict = {}
    cites: list[str] = []
    say: dict[str, str]
    say_who: dict[str, str] = {}
    card: dict[str, str] = {}


class Demo(_M):
    id: str
    title: dict[str, str] = {}
    when_slots: dict = {}
    differs_on: str | None = None
    steps: dict[str, str]
    say: dict[str, str]


class Myth(_M):
    id: str
    belief: str
    proof: str | None = None
    line: dict[str, str]
    keywords: list[str] = []


class QuizAnswer(_M):
    id: str
    title: dict[str, str]


class Quiz(_M):
    id: str
    step: str
    q: dict[str, str]
    answers: list[QuizAnswer]
    correct: str
    explain: dict[str, str]


class BudgetBand(_M):
    id: str
    inr: int | None = None
    title: dict[str, str]


class Pack(_M):
    category: str
    label: dict[str, str]
    required_slots: list[str]
    gate_min_slots: int = 4
    ask_order: list[str] = []  # the fixed question sequence in Assess (why-now first, then the home questions)
    complete_slots: list[str] = []  # asked in Complete, before the cost, only when they change it
    placeable: bool = False  # can be shown in the customer's room photo
    cost_slots: list[str] = []
    budget_bands: list[BudgetBand] = []
    slots: list[SlotDef]
    fit_rules: list[FitRule] = []
    reasons: dict[str, dict[str, str]] = {}
    needs: dict[str, dict[str, str]] = {}  # customer-facing name of each fit rule (options card)
    benefits: list[Benefit] = []
    demos: list[Demo] = []
    myths: list[Myth] = []
    site_needs: list[str] = []
    quiz: list[Quiz] = []

    def model_post_init(self, _ctx) -> None:
        self._slots = {s.id: s for s in self.slots}
        self._by_number = {s.number: s.id for s in self.slots if s.number}

    def slot(self, sid: str) -> SlotDef | None:
        return self._slots.get(sid)

    def slot_for_number(self, name: str) -> str | None:
        if name in self._slots:
            return name
        return self._by_number.get(name) or NUMBER_TO_SLOT_FALLBACK.get(name)

    def demo(self, did: str) -> Demo | None:
        return next((d for d in self.demos if d.id == did), None)

    def myth(self, mid: str) -> Myth | None:
        return next((m for m in self.myths if m.id == mid), None)

    @property
    def order(self) -> list[str]:
        return self.ask_order or self.required_slots

    def known_required(self, home: HomeCard) -> int:
        return sum(1 for s in self.required_slots if known(home, s))

    def missing_required(self, home: HomeCard) -> list[str]:
        return [s for s in self.required_slots if not known(home, s)]


class PackRegistry:
    def __init__(self, packs: dict[str, Pack], common: dict):
        self.packs = packs
        self.common = common

    def get(self, category: str | None) -> Pack | None:
        return self.packs.get(category or "")

    def myth(self, mid: str, category: str | None = None) -> Myth | None:
        p = self.get(category)
        m = p.myth(mid) if p else None
        if m:
            return m
        for c in self.common.get("myths", []):
            if c["id"] == mid:
                return Myth(**c)
        for p in self.packs.values():
            m = p.myth(mid)
            if m:
                return m
        return None

    def all_myths(self, category: str | None) -> list[Myth]:
        out = list(self.get(category).myths) if self.get(category) else []
        out += [Myth(**c) for c in self.common.get("myths", [])]
        return out

    def objection(self, oid: str) -> dict | None:
        return (self.common.get("objections") or {}).get(oid)

    def common_line(self, key: str) -> dict:
        v = self.common.get(key) or {}
        return v.get("line", v) if isinstance(v, dict) else {}

    @property
    def banned(self) -> list[str]:
        return [b.lower() for b in self.common.get("banned_phrases", [])]


def _load_yaml(p: Path) -> Any:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_packs(directory: Path = HERE) -> PackRegistry:
    packs: dict[str, Pack] = {}
    common: dict = {}
    for f in sorted(directory.glob("*.yaml")):
        data = _load_yaml(f)
        if f.stem == "common":
            common = data or {}
            continue
        packs[data["category"]] = Pack(**data)
    errors = validate_packs(packs, common)
    if errors:
        raise ValueError("pack validation failed:\n" + "\n".join(errors[:40]))
    return PackRegistry(packs, common)


def validate_packs(packs: dict[str, Pack], common: dict) -> list[str]:
    errs: list[str] = []
    for cat, p in packs.items():
        ids = [s.id for s in p.slots]
        if len(ids) != len(set(ids)):
            errs.append(f"{cat}: duplicate slot ids")
        for s in p.slots:
            if len(s.quick) > 3:
                errs.append(f"{cat}.{s.id}: > 3 quick answers")
            for q in s.quick:
                for lg in LANGS:
                    title = q.title.get(lg, "")
                    if not title:
                        errs.append(f"{cat}.{s.id}.{q.id}: missing quick title {lg}")
                    if len(title) > 20:
                        errs.append(f"{cat}.{s.id}.{q.id}: quick title > 20 chars ({lg})")
                if s.values and q.id not in s.values:
                    errs.append(f"{cat}.{s.id}: quick {q.id} not in values")
            for key in ("ask", "why"):
                d = getattr(s, key)
                for lg in LANGS:
                    if not d.get(lg):
                        errs.append(f"{cat}.{s.id}: missing {key}.{lg}")
        known_slots = set(ids) | {"wall_width_ft", "niche_width_cm"}
        if len(p.required_slots) != p.gate_min_slots:
            errs.append(f"{cat}: required_slots must be exactly the {p.gate_min_slots} gate questions")
        for ref in p.required_slots:
            if p.ask_order and ref not in p.ask_order:
                errs.append(f"{cat}: required slot {ref} missing from ask_order")
        for m in p.myths:
            if not isinstance(getattr(m, "short", None), dict) or any(len(m.short.get(lg, "")) > 24 or not m.short.get(lg) for lg in LANGS):
                errs.append(f"{cat}.{m.id}: myth short title missing or > 24 chars")
        for ref in p.required_slots + p.cost_slots + p.site_needs + p.ask_order + p.complete_slots:
            if ref not in known_slots:
                errs.append(f"{cat}: unknown slot ref {ref}")
        for b in p.benefits:
            for lg in LANGS:
                if not b.say.get(lg):
                    errs.append(f"{cat}.{b.id}: missing say.{lg}")
            for c in b.cites:
                if c not in known_slots:
                    errs.append(f"{cat}.{b.id}: cites unknown slot {c}")
        for d in p.demos:
            for lg in LANGS:
                if not d.say.get(lg) or not d.steps.get(lg):
                    errs.append(f"{cat}.{d.id}: missing say/steps.{lg}")
        for m in p.myths:
            for lg in LANGS:
                if not m.line.get(lg):
                    errs.append(f"{cat}.{m.id}: missing line.{lg}")
        for r in p.fit_rules:
            if r.slot not in known_slots:
                errs.append(f"{cat}.{r.id}: fit rule slot {r.slot} unknown")
    text = yaml.safe_dump({k: v.model_dump(by_alias=True) for k, v in packs.items()}, allow_unicode=True)
    text += yaml.safe_dump(common, allow_unicode=True)
    if re.search(r"[०-९]", text):
        errs.append("Devanagari digits found in packs")
    return errs


# ---------------------------------------------------------------- Home Card helpers
def known(home: HomeCard, slot: str) -> bool:
    return bool(home.slots.get(slot))


def values(home: HomeCard, slot: str) -> list[str]:
    return [v.value for v in home.slots.get(slot, [])]


def value(home: HomeCard, slot: str) -> str | None:
    v = home.slots.get(slot)
    return v[-1].value if v else None


def number(home: HomeCard, slot: str, pack: Pack | None = None) -> float | None:
    v = home.slots.get(slot)
    if not v:
        return None
    if v[-1].number is not None:
        return v[-1].number
    if pack:
        sd = pack.slot(slot)
        if sd:
            return sd.value_number(v[-1].value)
    return None


def set_slot(
    home: HomeCard, pack: Pack | None, slot: str, val: str, num: float | None, source: str, at: datetime
) -> bool:
    """Set a slot. Returns True if the Home Card changed. Single-valued: latest wins; multi: union."""
    sd = pack.slot(slot) if pack else None
    if sd and num is not None and sd.bands and (val in ("num", "", None) or val not in sd.values):
        val = sd.band_value(num) or val
    if sd and sd.values and val not in sd.values and val != "num":
        return False
    sv = SlotValue(slot=slot, value=val, number=num, source=source, at=at)  # type: ignore[arg-type]
    cur = home.slots.get(slot, [])
    if sd and sd.multi:
        if any(x.value == val for x in cur):
            return False
        rivals = {v for g in sd.exclusive if val in g for v in g if v != val}
        home.slots[slot] = [x for x in cur if x.value not in rivals] + [sv]
        return True
    if cur and cur[-1].value == val and (num is None or cur[-1].number == num):
        return False
    if cur:
        home.notes.append(f"{slot}: {cur[-1].value} → {val}")
    home.slots[slot] = [sv]
    return True


def eval_slot_cond(home: HomeCard, slot: str, cond: dict | None, pack: Pack | None = None) -> bool:
    if not known(home, slot):
        return False
    if not cond:
        return True
    vals = values(home, slot)
    n = number(home, slot, pack)
    for op, arg in cond.items():
        if op == "in" and not any(v in arg for v in vals):
            return False
        if op in ("has_any",) and not any(v in arg for v in vals):
            return False
        if op in ("has", "eq") and str(arg) not in vals:
            return False
        if op == "known" and bool(arg) != known(home, slot):
            return False
        if op in ("gte", "lte", "gt", "lt"):
            if n is None:
                return False
            if (
                (op == "gte" and not n >= arg)
                or (op == "lte" and not n <= arg)
                or (op == "gt" and not n > arg)
                or (op == "lt" and not n < arg)
            ):
                return False
    return True


def eval_slots_map(home: HomeCard, conds: dict, pack: Pack | None = None) -> bool:
    return all(eval_slot_cond(home, s, c, pack) for s, c in (conds or {}).items())


def _cmp(v: Any, op: str, arg: Any) -> bool:
    if op == "nonempty":
        return bool(v) == bool(arg)
    if v is None:
        return False
    try:
        if op == "gte":
            return float(v) >= float(arg)
        if op == "lte":
            return float(v) <= float(arg)
        if op == "gt":
            return float(v) > float(arg)
        if op == "lt":
            return float(v) < float(arg)
    except (TypeError, ValueError):
        return False
    if op == "eq":
        return v == arg or str(v).lower() == str(arg).lower()
    if op == "in":
        return v in arg or str(v) in [str(a) for a in arg]
    return False


def eval_spec_cond(product, home: HomeCard, cond: dict, pack: Pack, guides: dict) -> bool:
    """cond = {key: {op: arg, ...}, ...} — all must hold."""
    from chanakya.brain.fit import in_band  # local import to avoid a cycle

    for key, ops in (cond or {}).items():
        v = product.spec(key)
        for op, arg in (ops or {}).items():
            if op in ("in_band", "in_size_band"):
                r = in_band(product, home, pack, guides)
                if r is None or (r >= 1.0) != bool(arg):
                    return False
            elif not _cmp(v, op, arg):
                return False
    return True


def eval_spec_any(product, home: HomeCard, conds: list[dict], pack: Pack, guides: dict) -> bool:
    return any(eval_spec_cond(product, home, c, pack, guides) for c in conds)


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text or "")
