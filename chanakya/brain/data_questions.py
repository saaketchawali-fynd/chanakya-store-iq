"""Questions backwards from the products (user, 3 Nov 2026): ask only what today's catalogue can act on.

1. A home question exists to pick products through its fit rules. If no rule of that question can change the pick
   with the data we have (too few models carry the spec, or every model is the same on it), it is skipped. Example
   on the live Jio data: TVs carry no brightness or refresh rate, so "does the sun come in?" and "how often is
   cricket on?" cannot pick a TV; they are not asked.
2. In their place, at most two questions about what the models on sale really differ on (picture type, smart TV
   system, inverter, load type, door type, laptop type, brand …), with the answers taken from the stock itself.
   An answer becomes a 'want' that filters the shortlist.
Questions without fit rules (why now, room, the cost and site questions) are not judged here."""

from __future__ import annotations

from collections import Counter

from chanakya.brain import packs as P

COVER_MIN = 0.3  # at least this share of the models on sale must carry the spec for a question to use it
MAX_DATA_QUESTIONS = 2
ATTRS = {  # what a customer can choose between, in the backend's own terms, best first
    "tv": ["panel", "os", "resolution", "brand"],
    "ac": ["inverter", "star", "brand"],
    "wm": ["load", "automatic", "brand"],
    "fridge": ["door", "frost_free", "brand"],
    "laptop": ["laptop_type", "os", "brand"],
}
BOOL_LABELS = {
    "inverter": ("Inverter", "Non-inverter"), "automatic": ("Fully automatic", "Semi automatic"),
    "frost_free": ("Frost free", "Direct cool"), "wifi": ("Wi-Fi", "No Wi-Fi"),
}


def _value(p, key: str):
    if key == "brand":
        return p.brand or None
    v = p.spec(key)
    return None if v in (None, "", []) else v


def _keys(cond: dict) -> list[str]:
    return [k for k in (cond or {}) if not k.startswith("_")]


def _cover(avail: list, keys: list[str]) -> list:
    return [p for p in avail if any(_value(p, k) is not None for k in keys)]


def rule_informative(rule, avail: list, home, pack, guides: dict) -> bool:
    """Can this rule change which product is picked, with the data on sale today?"""
    if not avail:
        return False
    need = max(2, int(COVER_MIN * len(avail) + 0.999))
    band = {"size_band": "size_in", "ton_band": "ton"}
    if rule.kind in band or rule.kind == "capacity_band":
        key = band.get(rule.kind) or ("capacity_kg" if pack.category == "wm" else "capacity_l")
        have = [p for p in avail if _value(p, key) is not None]
        return len(have) >= need and len({_value(p, key) for p in have}) >= 2
    if rule.kind == "width_fit":
        return len([p for p in avail if p.spec("width_mm")]) >= need
    if rule.tiers:
        keys = [k for t in rule.tiers for c in (t.get("spec_all") or []) for k in _keys(c)]
        return len(_cover(avail, keys)) >= need
    if rule.spec_any:
        keys = [k for c in rule.spec_any for k in _keys(c)]
        known = _cover(avail, keys)
        if len(known) < need:
            return False
        yes = sum(1 for p in known if P.eval_spec_any(p, home, rule.spec_any, pack, guides))
        return 0 < yes < len(known)
    return True


def skip_slots(pack, avail: list, home, guides: dict) -> set[str]:
    """The home questions that cannot pick a product today (every one of their fit rules is uninformative)."""
    by_slot: dict[str, list] = {}
    for r in pack.fit_rules:
        by_slot.setdefault(r.slot, []).append(r)
    return {sid for sid, rules in by_slot.items() if not any(rule_informative(r, avail, home, pack, guides) for r in rules)}


def split(avail: list, key: str) -> list[tuple[object, int]]:
    """[(value, models)] when the stock really differs on this attribute (two or more values with two or more
    models each, carried by enough models); else []."""
    vals = [_value(p, key) for p in avail]
    have = [v for v in vals if v is not None]
    if len(have) < max(4, COVER_MIN * len(avail)):
        return []
    c = Counter(str(v) if not isinstance(v, bool) else v for v in have)
    top = [(v, n) for v, n in c.most_common() if n >= 2]
    return top if len(top) >= 2 else []


def label(key: str, value) -> str:
    if isinstance(value, bool):
        yes, no = BOOL_LABELS.get(key, ("Yes", "No"))
        return yes if value else no
    if key == "star":
        return f"{value} star"
    return str(value)


def next_data_question(pack, avail: list, done: set[str], wants: dict) -> tuple[str, list[tuple[object, int]]] | None:
    """The next question about what the models really differ on, or None (at most MAX_DATA_QUESTIONS)."""
    if len(done) >= MAX_DATA_QUESTIONS:
        return None
    for key in ATTRS.get(pack.category, []):
        if key in done or key in (wants or {}):
            continue
        sp = split(avail, key)
        if sp:
            return key, sp[:2]
    return None
