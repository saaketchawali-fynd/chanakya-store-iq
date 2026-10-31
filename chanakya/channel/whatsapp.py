"""WhatsApp Cloud API channel (spec 6.2–6.6)."""

from __future__ import annotations

import asyncio
import hashlib
import time

import httpx

from chanakya.app import diag
from chanakya.channel.base import SendError
from chanakya.config import Settings
from chanakya.core.contracts import OutMessage
from chanakya.log import get_log

log = get_log("whatsapp")


class WhatsAppChannel:
    renders_cards = True

    def __init__(self, s: Settings, client: httpx.AsyncClient | None = None):
        self.s = s
        self.base = f"https://graph.facebook.com/{s.wa_api_version}"
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5))
        self._media_cache: dict[str, tuple[str, float]] = {}

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.wa_token}"}

    async def close(self) -> None:
        await self.client.aclose()

    async def _post(self, path: str, **kw) -> dict:
        delays = [0.5, 1.5]
        for attempt in range(3):
            r = await self.client.post(f"{self.base}/{path}", headers=self._headers, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(delays[attempt])
                    continue
            if r.status_code >= 400:
                err = {}
                try:
                    err = r.json().get("error", {})
                except Exception:
                    pass
                log.error(
                    "graph_error",
                    code=err.get("code"),
                    subcode=err.get("error_subcode"),
                    message=err.get("message"),
                    status=r.status_code,
                )
                diag.bump("send_errors")
                diag.mark(
                    "last_send_error",
                    {
                        "code": err.get("code"),
                        "subcode": err.get("error_subcode"),
                        "message": (err.get("message") or "")[:160],
                        "http": r.status_code,
                        "path": path.split("/")[-1],
                    },
                )
                raise SendError(err.get("code"), err.get("message", r.text[:200]))
            return r.json()
        raise SendError(None, "retries exhausted")

    # ---------- payloads ----------
    def payload(self, m: OutMessage) -> dict:
        base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": m.to}
        if m.kind == "text":
            return base | {"type": "text", "text": {"preview_url": False, "body": m.body or ""}}
        if m.kind == "buttons":
            inter: dict = {
                "type": "button",
                "body": {"text": m.body or " "},
                "action": {
                    "buttons": [{"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in m.buttons[:3]]
                },
            }
            if m.media_id:
                inter["header"] = {"type": "image", "image": {"id": m.media_id}}
            if m.footer:
                inter["footer"] = {"text": m.footer}
            return base | {"type": "interactive", "interactive": inter}
        if m.kind == "list":
            return base | {
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": m.body or " "},
                    "action": {
                        "button": m.list_button or "Chuniye",
                        "sections": [
                            {
                                **({"title": s.title} if s.title else {}),
                                "rows": [{k: v for k, v in r.model_dump().items() if v is not None} for r in s.rows],
                            }
                            for s in m.sections
                        ],
                    },
                },
            }
        if m.kind == "image":
            img = {"id": m.media_id}
            if m.caption or m.body:
                img["caption"] = m.caption or m.body
            return base | {"type": "image", "image": img}
        if m.kind == "audio":
            return base | {"type": "audio", "audio": {"id": m.media_id, **({"voice": True} if m.voice else {})}}
        if m.kind == "video":
            return base | {
                "type": "video",
                "video": {"id": m.media_id, **({"caption": m.caption} if m.caption else {})},
            }
        if m.kind == "document":
            return base | {
                "type": "document",
                "document": {
                    "id": m.media_id,
                    "filename": m.filename or "file",
                    **({"caption": m.caption} if m.caption else {}),
                },
            }
        if m.kind == "reaction":
            return base | {"type": "reaction", "reaction": {"message_id": m.reaction_to, "emoji": m.emoji or "👂"}}
        if m.kind == "cta_url":
            return base | {
                "type": "interactive",
                "interactive": {
                    "type": "cta_url",
                    "body": {"text": m.body or " "},
                    "action": {"name": "cta_url", "parameters": {"display_text": m.url_text or "Open", "url": m.url}},
                },
            }
        if m.kind == "template":
            return base | {
                "type": "template",
                "template": {
                    "name": m.template_name,
                    "language": {"code": m.template_lang or "en"},
                    "components": m.template_components,
                },
            }
        raise ValueError(f"unknown kind {m.kind}")

    async def send(self, msg: OutMessage) -> str:
        data = await self._post(f"{self.s.wa_phone_number_id}/messages", json=self.payload(msg))
        diag.bump("sent_ok")
        diag.mark("last_send_ok_at")
        return (data.get("messages") or [{}])[0].get("id", "")

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        key = hashlib.sha256(data).hexdigest()
        hit = self._media_cache.get(key)
        if hit and time.time() - hit[1] < 24 * 3600:
            return hit[0]
        res = await self._post(
            f"{self.s.wa_phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
        )
        self._media_cache[key] = (res["id"], time.time())
        return res["id"]

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        r = await self.client.get(f"{self.base}/{media_id}", headers=self._headers)
        r.raise_for_status()
        meta = r.json()
        r2 = await self.client.get(meta["url"], headers=self._headers)
        r2.raise_for_status()
        return r2.content, meta.get("mime_type", "application/octet-stream")

    async def mark_read_typing(self, wa_message_id: str) -> None:
        try:
            await self._post(
                f"{self.s.wa_phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": wa_message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
        except Exception as e:  # feedback only; never block the reply
            log.warning("typing_failed", err=str(e))
