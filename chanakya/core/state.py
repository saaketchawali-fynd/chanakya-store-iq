"""Step state machine, gates and overrides (spec 7.5)."""

from __future__ import annotations

from datetime import datetime

from chanakya.core.contracts import SessionState, Step

ORDER = [Step.ASSESS, Step.RECOMMEND, Step.DEMONSTRATED, Step.COMPLETE, Step.CLOSE, Step.FOLLOW, Step.CLOSED]
OVERRIDE_REASONS = {"hurry": "b_override_hurry", "return": "b_override_return", "asked": "b_override_asked"}


def advance(s: SessionState, to: Step) -> None:
    """Move forward only (never backwards)."""
    if ORDER.index(to) > ORDER.index(s.step):
        s.step = to


def has_override(s: SessionState, gate: str) -> bool:
    return any(o.get("gate") == gate for o in s.overrides)


def options_gate_open(s: SessionState, pack) -> bool:
    if has_override(s, "options") or has_override(s, "price"):
        return True
    if s.extra.get("from_note"):  # the needs were told in one note: the shortlist came from it
        return True
    if pack is None:
        return False
    skip = set(s.extra.get("skip_slots") or [])
    if not skip:
        return pack.known_required(s.home) >= pack.gate_min_slots
    # some home questions cannot act on today's catalogue: the data questions stand in for them
    eff = [r for r in pack.required_slots if r not in skip]
    known = sum(1 for r in eff if r in s.home.slots and s.home.slots[r]) + len(s.extra.get("dq_done") or [])
    need = min(pack.gate_min_slots, len(eff) + int(s.extra.get("dq_avail") or 0))
    return known >= need


def required_progress(s: SessionState, pack) -> tuple[int, int]:
    """(known, total) of the home questions that count today (those the catalogue cannot act on left out, the
    data questions standing in for them)."""
    skip = set(s.extra.get("skip_slots") or [])
    if not skip:
        return pack.known_required(s.home), len(pack.required_slots)
    eff = [r for r in pack.required_slots if r not in skip]
    known = sum(1 for r in eff if r in s.home.slots and s.home.slots[r]) + len(s.extra.get("dq_done") or [])
    return known, len(eff) + int(s.extra.get("dq_avail") or 0)


def missing_for_options(s: SessionState, pack) -> int:
    if pack is None:
        return 0
    skip = set(s.extra.get("skip_slots") or [])
    if not skip:
        return max(0, pack.gate_min_slots - pack.known_required(s.home))
    eff = [r for r in pack.required_slots if r not in skip]
    known = sum(1 for r in eff if r in s.home.slots and s.home.slots[r]) + len(s.extra.get("dq_done") or [])
    return max(0, min(pack.gate_min_slots, len(eff) + int(s.extra.get("dq_avail") or 0)) - known)


def price_gate_open(s: SessionState, pack) -> bool:
    if has_override(s, "price"):
        return True
    return s.demo_done and options_gate_open(s, pack)


def add_override(s: SessionState, gate: str, reason: str, at: datetime) -> None:
    s.overrides.append({"gate": gate, "reason": reason, "at": at.isoformat()})
    if gate == "price" and "price_first_by" not in s.extra and not s.demo_done:
        s.extra["price_first_by"] = "customer" if reason == "asked" else "associate"
        s.extra["price_first_reason"] = reason


# ---------------------------------------------------------------- the options' roles (labels stay A, B, C in order)
def option_role(s: SessionState, label: str) -> str | None:
    """value | best | step for an option label (B is the best fit unless there is no cheaper option)."""
    roles = (s.extra or {}).get("option_roles") or {}
    return roles.get(label) or {"A": "value", "B": "best", "C": "step"}.get(label)


def best_label(s: SessionState) -> str:
    roles = (s.extra or {}).get("option_roles") or {}
    return next((lab for lab, r in roles.items() if r == "best"), "B")


def best_sku(s: SessionState) -> str | None:
    """The best-fit option's SKU (never assume it is labelled B)."""
    return s.option_labels.get(best_label(s))

