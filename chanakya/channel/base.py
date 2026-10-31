"""Channel protocol (spec 5)."""

from __future__ import annotations

from typing import Protocol

from chanakya.core.contracts import OutMessage


class SendError(Exception):
    def __init__(self, code: int | None, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code


class Channel(Protocol):
    renders_cards: bool

    async def send(self, msg: OutMessage) -> str: ...  # returns wamid

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str: ...

    async def download_media(self, media_id: str) -> tuple[bytes, str]: ...  # (bytes, mime)

    async def mark_read_typing(self, wa_message_id: str) -> None: ...
