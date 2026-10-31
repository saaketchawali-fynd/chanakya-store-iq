"""Number firewall (spec 7.7): every number a user sees must come from data, a registered
calculation, the Home Card, an expert answer, or approved static text."""

from __future__ import annotations

import re

from chanakya.truth import fmt

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalise(n: str) -> str:
    n = n.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n


class NumberRegistry:
    def __init__(self) -> None:
        self._allowed: set[str] = set()
        self.sources: list[str] = []

    def inr(self, value: float, source: str) -> str:
        s = fmt.inr(value)
        self._allow(s)
        self.sources.append(source)
        return s

    def num(self, value: float | int, source: str, unit: str = "") -> str:
        s = fmt.num(value) + (f" {unit}" if unit else "")
        self._allow(s)
        self.sources.append(source)
        return s

    def static(self, text: str) -> str:
        """Approved template text (packs/strings): its literal numerals are allowed."""
        self._allow(text)
        return text

    def raw(self, text: str, source: str) -> str:
        """Pre-formatted text from data (e.g. a date string, an expert's words)."""
        self._allow(text)
        self.sources.append(source)
        return text

    def _allow(self, s: str) -> None:
        for n in NUM_RE.findall(s or ""):
            self._allowed.add(normalise(n))

    def check(self, text: str) -> list[str]:
        return [n for n in NUM_RE.findall(text or "") if normalise(n) not in self._allowed]

    def allowed(self) -> list[str]:
        return sorted(self._allowed)

    def merge(self, other: NumberRegistry) -> None:
        self._allowed |= other._allowed
        self.sources.extend(other.sources)
