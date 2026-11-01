"""SkillContext (spec 5) + Skill protocol + small helpers shared by every skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from chanakya.core.contracts import Button, Event, InboundMessage, Reply, Role, SessionState
from chanakya.core.events import ev
from chanakya.i18n import lang as i18n
from chanakya.truth.firewall import NumberRegistry

if TYPE_CHECKING:
    from chanakya.ai.client import AIClient
    from chanakya.brain.packs import Pack, PackRegistry
    from chanakya.core.services import Services
    from chanakya.truth.facts import FactsService
    from chanakya.truth.models import Staff


@dataclass
class SkillContext:
    actor_wa_id: str
    role: Role
    staff: Staff | None
    session: SessionState | None
    inbound: InboundMessage
    facts: FactsService
    packs: PackRegistry
    ai: AIClient
    reg: NumberRegistry  # fresh per reply
    now: datetime  # demo-clock aware
    # --- additive helpers (not in the original contract) ---
    svc: Services | None = None
    asking: set[str] = field(default_factory=set)  # slots being asked about (allowed Kyun cites)
    events: list[Event] = field(default_factory=list)
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def lang(self) -> str:
        return self.staff.lang if self.staff and self.staff.lang else "en"

    @property
    def clang(self) -> str:
        return self.session.home.customer_lang if self.session else self.lang

    @property
    def pack(self) -> Pack | None:
        if not self.session:
            return None
        for cat in (self.session.home.category, self.staff.dept if self.staff else None, "tv"):
            if cat and self.packs.get(cat):
                return self.packs.get(cat)
        return None

    @property
    def x(self) -> dict:
        """Session working state (SessionState.extra)."""
        assert self.session is not None
        return self.session.extra

    def t(self, key: str, lang: str | None = None, **vars) -> str:
        return i18n.t(key, lang or self.lang, self.reg, **vars)

    def tc(self, key: str, **vars) -> str:
        """Customer-language string."""
        return i18n.t(key, self.clang, self.reg, **vars)

    def pick(self, d: dict | None, lang: str | None = None) -> str:
        return i18n.pick(d, lang or self.lang, self.reg)

    def ev(self, type: str, sources: list[str] | None = None, **payload) -> Event:
        e = ev(
            type,
            session_id=self.session.id if self.session else None,
            actor=self.actor_wa_id,
            step=self.session.step.value if self.session else None,
            sources=sources,
            **payload,
        )
        self.events.append(e)
        return e

    def b(self, key: str, button_id: str, lang: str | None = None, **vars) -> Button:
        from chanakya.channel.limits import truncate_title

        return Button(id=button_id, title=truncate_title(self.t(key, lang, **vars)))

    def sid(self, *parts: object) -> str:
        """Session-scoped button id s:<code>:<action>[:<args>]."""
        from chanakya.core.buttons import encode

        assert self.session is not None
        return encode("s", self.session.code, *parts)

    def reply(self, *messages, background: list[str] | None = None) -> Reply:
        return Reply(
            messages=[m for m in messages if m is not None], events=list(self.events), background=background or []
        )


class Skill(Protocol):
    async def handle(self, ctx: SkillContext, action: str, arg: str | None) -> Reply: ...
