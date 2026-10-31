"""Button id encode/decode (spec 7.3): <scope>:<a>:<b>[:<c>…], ASCII, ≤ 256 chars."""

from __future__ import annotations

from dataclasses import dataclass

SCOPES = {"s", "g", "m", "x", "u", "v"}


@dataclass(frozen=True)
class ButtonId:
    scope: str
    parts: tuple[str, ...]

    def get(self, i: int, default: str | None = None) -> str | None:
        return self.parts[i] if len(self.parts) > i else default


def encode(scope: str, *parts: object) -> str:
    if scope not in SCOPES:
        raise ValueError(f"bad scope {scope}")
    s = ":".join([scope, *[str(p) for p in parts]])
    if not s.isascii() or len(s) > 256:
        raise ValueError(f"bad button id {s!r}")
    return s


def decode(button_id: str | None) -> ButtonId | None:
    if not button_id or ":" not in button_id:
        return None
    scope, *parts = button_id.split(":")
    if scope not in SCOPES:
        return None
    return ButtonId(scope, tuple(parts))
