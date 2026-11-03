"""Simulator / test harness: the real orchestrator with FakeChannel (fixture AI by default)."""

from __future__ import annotations

import itertools
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from chanakya.app.bootstrap import Runtime, build, shutdown, start_renderer
from chanakya.channel.fake import FakeChannel
from chanakya.config import ROOT, Settings
from chanakya.core import clock, locks
from chanakya.core.contracts import InboundMessage, OutMessage

PHONES = {
    "associate": "919000000001",
    "customer": "919000000002",
    "manager": "919000000003",
    "expert": "919000000004",
    "visitor": "919000000005",
}
FIX_IMAGES = ROOT / "data" / "fixtures" / "images"


def harness_settings(db_url: str, ai_mode: str = "fixture", **kw) -> Settings:
    return Settings(
        _env_file=None,
        database_url=db_url,
        ai_mode=ai_mode,
        data_source=kw.pop("data_source", "seed"),  # sample data: tests and the offline simulator
        demo_mode=True,
        render_cards=False,
        scheduler_enabled=False,
        associate_phone=PHONES["associate"],
        customer_phone=PHONES["customer"],
        manager_phone=PHONES["manager"],
        expert_phone=PHONES["expert"],
        visitor_phone=PHONES["visitor"],
        wa_app_secret="",
        store_id="RD-JUHU",
        associate_name=kw.pop("associate_name", "Ravi"),  # the simulated associate already told us their name
        **kw,
    )


class Harness:
    def __init__(self, rt: Runtime, channel: FakeChannel):
        self.rt = rt
        self.ch = channel
        self.who = "associate"
        self._ids = itertools.count(1)

    @classmethod
    async def create(
        cls,
        db_url: str,
        ai_mode: str = "fixture",
        render: bool = False,
        drop: bool = True,
        lang_set: bool = True,
        **kw,
    ) -> Harness:
        locks.reset()
        ch = FakeChannel(renders_cards=render)
        rt = await build(harness_settings(db_url, ai_mode, **kw), ch, drop=drop, render=render)
        if lang_set:  # skip first contact: language and name (tests that check it pass lang_set=False)
            from chanakya.core import repo

            for st in await repo.staff_all():
                await repo.staff_update(st.wa_id, lang_set=True, name_set=True, name_asked=True)
        if render:
            await start_renderer(rt)
        return cls(rt, ch)

    async def close(self) -> None:
        await shutdown(self.rt)

    @property
    def phone(self) -> str:
        return PHONES[self.who]

    def as_(self, who: str) -> Harness:
        self.who = who
        return self

    async def _send(self, **kw) -> list[OutMessage]:
        n0 = len(self.ch.sent)
        now = clock.now()
        msg = InboundMessage(
            wa_message_id=f"wamid.SIM{next(self._ids)}.{now.timestamp()}",
            from_wa_id=self.phone,
            timestamp=now,
            received_at=datetime.now(UTC),
            **kw,
        )
        await self.rt.orch.handle_inbound(msg)
        await self.rt.orch.drain()
        return [s.msg for s in self.ch.sent[n0:] if s.msg.kind != "reaction"]  # the 👂 receipt is checked apart

    async def say(self, text: str) -> list[OutMessage]:
        return await self._send(type="text", text=text)

    async def voice(self, key: str) -> list[OutMessage]:
        """Fixture stand-in for a voice note: 'FIX:<key>' as text (spec 18.1)."""
        return await self._send(type="text", text=f"FIX:{key}")

    async def voice_bytes(self, data: bytes) -> list[OutMessage]:
        mid = self.ch.put_media(data, "audio/ogg", "note.ogg")
        return await self._send(type="audio", media_id=mid, mime_type="audio/ogg", is_voice=True)

    async def photo(self, path: str | Path, caption: str | None = None) -> list[OutMessage]:
        p = Path(path)
        if not p.exists():
            p = FIX_IMAGES / str(path)
        data = p.read_bytes()
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        mid = self.ch.put_media(data, mime, p.name)
        return await self._send(type="image", media_id=mid, mime_type=mime, text=caption)

    def _context(self, reply_id: str) -> str | None:
        """Like WhatsApp: a tap carries the id of the message the button (or list row) was on."""
        for sent in reversed(self.ch.sent):
            m = sent.msg
            if m.to != self.phone:
                continue
            if any(b.id == reply_id for b in m.buttons) or any(
                r.id == reply_id for sec in (m.sections or []) for r in sec.rows
            ):
                return sent.wamid
        return None

    async def tap(self, button_id: str, title: str = "") -> list[OutMessage]:
        return await self._send(type="button_reply", reply_id=button_id, text=title,
                                context_message_id=self._context(button_id))

    async def pick(self, row_id: str, title: str = "") -> list[OutMessage]:
        return await self._send(type="list_reply", reply_id=row_id, text=title,
                                context_message_id=self._context(row_id))

    async def clock(self, arg: str) -> list[OutMessage]:
        prev = self.who
        self.who = "associate"
        try:
            return await self.say(f"/clock {arg}")
        finally:
            self.who = prev

    def to(self, who: str) -> list[OutMessage]:
        return self.ch.to(PHONES[who])
