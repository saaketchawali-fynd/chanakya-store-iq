"""Webhook JSON → list[InboundMessage] (+ status updates) (spec 6.1)."""

from __future__ import annotations

from datetime import UTC, datetime

from chanakya.core.contracts import InboundMessage
from chanakya.log import get_log

log = get_log("parse")


def _ts(v) -> datetime:
    try:
        return datetime.fromtimestamp(int(v), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def parse(payload: dict, received_at: datetime | None = None) -> tuple[list[InboundMessage], list[dict]]:
    received_at = received_at or datetime.now(UTC)
    msgs: list[InboundMessage] = []
    statuses: list[dict] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            contacts = value.get("contacts") or []
            profile = (contacts[0].get("profile") or {}).get("name") if contacts else None
            for m in value.get("messages", []) or []:
                try:
                    msgs.append(_one(m, profile, received_at))
                except Exception:  # never crash the webhook on a weird payload
                    log.exception("parse_failed")
            for s in value.get("statuses", []) or []:
                st = {
                    "id": s.get("id"),
                    "status": s.get("status"),
                    "recipient_id": s.get("recipient_id"),
                    "errors": s.get("errors") or [],
                }
                statuses.append(st)
                codes = [e.get("code") for e in st["errors"]]
                if st["status"] == "failed":
                    log.warning(
                        "window_closed" if 131047 in codes else "send_failed", to=st["recipient_id"], codes=codes
                    )
    return msgs, statuses


def _one(m: dict, profile: str | None, received_at: datetime) -> InboundMessage:
    t = m.get("type")
    base = dict(
        wa_message_id=m["id"],
        from_wa_id=m["from"],
        profile_name=profile,
        timestamp=_ts(m.get("timestamp")),
        context_message_id=(m.get("context") or {}).get("id"),
        received_at=received_at,
    )
    if t == "text":
        return InboundMessage(type="text", text=(m.get("text") or {}).get("body", ""), **base)
    if t == "audio":
        a = m.get("audio") or {}
        return InboundMessage(
            type="audio", media_id=a.get("id"), mime_type=a.get("mime_type"), is_voice=bool(a.get("voice")), **base
        )
    if t == "image":
        i = m.get("image") or {}
        return InboundMessage(
            type="image", media_id=i.get("id"), mime_type=i.get("mime_type"), text=i.get("caption"), **base
        )
    if t == "interactive":
        it = m.get("interactive") or {}
        if it.get("type") == "button_reply":
            br = it.get("button_reply") or {}
            return InboundMessage(type="button_reply", reply_id=br.get("id"), text=br.get("title"), **base)
        if it.get("type") == "list_reply":
            lr = it.get("list_reply") or {}
            return InboundMessage(type="list_reply", reply_id=lr.get("id"), text=lr.get("title"), **base)
    if t == "button":
        b = m.get("button") or {}
        return InboundMessage(type="template_button", reply_id=b.get("payload"), text=b.get("text"), **base)
    if t == "document":
        d = m.get("document") or {}
        return InboundMessage(type="document", media_id=d.get("id"), mime_type=d.get("mime_type"), **base)
    if t == "video":
        v = m.get("video") or {}
        return InboundMessage(type="video", media_id=v.get("id"), mime_type=v.get("mime_type"), **base)
    if t == "location":
        return InboundMessage(type="location", **base)
    return InboundMessage(type="unsupported", **base)
