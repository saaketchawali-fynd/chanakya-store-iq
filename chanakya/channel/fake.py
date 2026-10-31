"""FakeChannel (simulator/tests): records OutMessages, stores media in memory."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from chanakya.channel.base import SendError
from chanakya.core.contracts import OutMessage


@dataclass
class Sent:
    msg: OutMessage
    wamid: str
    image: bytes | None = None  # rendered card / image bytes


@dataclass
class FakeChannel:
    renders_cards: bool = False
    sent: list[Sent] = field(default_factory=list)
    media: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)  # id → (bytes, mime, filename)
    read_marks: list[str] = field(default_factory=list)
    fail_codes: dict[str, int] = field(default_factory=dict)  # to → error code (e.g. 131047)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    async def send(self, msg: OutMessage) -> str:
        code = self.fail_codes.get(msg.to)
        if code and msg.kind != "template":
            raise SendError(code, "fake failure")
        wamid = f"wamid.OUT{next(self._ids)}"
        img = self.media.get(msg.media_id or "", (None,))[0] if msg.media_id else None
        self.sent.append(Sent(msg=msg, wamid=wamid, image=img))
        return wamid

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        mid = f"MEDIA{next(self._ids)}"
        self.media[mid] = (data, mime, filename)
        return mid

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        data, mime, _ = self.media[media_id]
        return data, mime

    def media_filename(self, media_id: str) -> str | None:
        hit = self.media.get(media_id)
        return hit[2] if hit else None

    def put_media(self, data: bytes, mime: str, filename: str) -> str:
        mid = f"IN{next(self._ids)}"
        self.media[mid] = (data, mime, filename)
        return mid

    async def mark_read_typing(self, wa_message_id: str) -> None:
        self.read_marks.append(wa_message_id)

    def to(self, wa_id: str) -> list[OutMessage]:
        return [s.msg for s in self.sent if s.msg.to == wa_id]

    def clear(self) -> None:
        self.sent.clear()
