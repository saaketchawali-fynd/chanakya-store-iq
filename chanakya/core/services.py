"""Services: shared handles (settings, channel, facts, packs, ai, sessions) + delivery
(validate → render card → upload → send; window-closed handling)."""

from __future__ import annotations

from collections import deque

from chanakya.channel.base import Channel, SendError
from chanakya.config import Settings
from chanakya.core import fallback, validator
from chanakya.core.contracts import HomeCard, OutMessage
from chanakya.core.session import SessionRepo
from chanakya.i18n.lang import t
from chanakya.log import get_log
from chanakya.truth.firewall import NumberRegistry

log = get_log("services")


class Services:
    def __init__(self, settings: Settings, channel: Channel, facts, packs, ai, render=None):
        self.settings = settings
        self.channel = channel
        self.facts = facts
        self.packs = packs
        self.ai = ai
        self.sessions = SessionRepo()
        self._render = render  # async (Card) -> bytes
        self.violations: deque[validator.Violation] = deque(maxlen=200)  # recent ones only, for debugging
        self.orchestrator = None  # set by Orchestrator

    def media_name(self, media_id: str | None) -> str | None:
        fn = getattr(self.channel, "media_filename", None)
        return fn(media_id) if (fn and media_id) else None

    async def render(self, card) -> bytes | None:
        if self._render is None:
            return None
        try:
            return await self._render(card)
        except Exception:
            log.exception("render_failed", template=card.template)
            return None

    def validate(
        self,
        m: OutMessage,
        reg: NumberRegistry,
        home: HomeCard | None,
        lang: str,
        asking: set[str] | None,
        session=None,
    ) -> OutMessage:
        out, vio = validator.enforce(
            m, reg, home, lang, self.packs.banned, asking, lambda: fallback.safe_reply(m.to, lang, session)
        )
        self.violations.extend(vio)
        return out

    async def _send_copy(self, m: OutMessage) -> None:
        """The associate's-language copy of a customer-facing visual, just before the English one."""
        try:
            img = None
            if m.card_copy is not None and self.channel.renders_cards:
                img = await self.render(m.card_copy)
            elif m.image_copy is not None:
                img = m.image_copy
            if not img:
                return
            mid = await self.channel.upload_media(img, "image/jpeg", "copy.jpg")
            await self.channel.send(OutMessage(to=m.to, kind="image", media_id=mid, caption=m.copy_caption))
        except Exception:
            log.exception("copy_failed", to=m.to)

    async def deliver(self, m: OutMessage) -> str | None:
        if m.kind == "image" and not m.caption and m.body:
            m.caption, m.body = m.body, None
        if m.card_copy is not None or m.image_copy is not None:
            await self._send_copy(m)
        try:
            if m.card is not None and self.channel.renders_cards:
                img = await self.render(m.card)
                if img:
                    m.media_id = await self.channel.upload_media(img, "image/jpeg", f"{m.card.template}.jpg")
            elif m.image_bytes is not None and m.media_id is None:
                m.media_id = await self.channel.upload_media(m.image_bytes, "image/jpeg", "image.jpg")
            if m.kind == "image" and not m.media_id and self.channel.renders_cards:
                m.kind, m.body, m.caption = "text", m.caption or m.body or " ", None  # card failed → text only
            wamid = await self.channel.send(m)
            if wamid and m.session_id and (m.buttons or m.sections):
                from chanakya.core import repo

                # a tap on this message's buttons comes back with its id: the exact customer, even days later
                await repo.message_insert(wamid, "out", m.to, m.kind, {"session_id": m.session_id})
            return wamid
        except SendError as e:
            if e.code == 131047:
                log.warning("window_closed", to=m.to)
                s = await self.sessions.by_customer(m.to)
                if s:
                    from chanakya.core import repo

                    st = await repo.staff_by_wa(s.associate_wa_id)
                    try:
                        await self.channel.send(
                            OutMessage(
                                to=s.associate_wa_id, kind="text", body=t("window_closed", st.lang if st else "hi")
                            )
                        )
                    except Exception:
                        log.exception("notify_window_closed_failed")
                return None
            log.error("send_failed", code=e.code, to=m.to)
            return None
        except Exception:
            log.exception("deliver_failed", to=m.to, kind=m.kind)
            return None

    async def send_checked(self, m: OutMessage, reg: NumberRegistry | None = None, lang: str = "hi") -> str | None:
        """For scheduler/background messages built from strings + data (text registered by the caller)."""
        if reg is None:  # caller built the text from strings + data rows: trusted static text
            reg = NumberRegistry()
            for s in (m.body, m.caption):
                if s:
                    reg.static(s)
        return await self.deliver(self.validate(m, reg, None, lang, None))


_svc: Services | None = None


def set_services(s: Services | None) -> None:
    global _svc
    _svc = s


def get_services() -> Services:
    assert _svc is not None, "services not initialised"
    return _svc
