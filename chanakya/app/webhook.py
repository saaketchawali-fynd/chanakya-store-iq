"""GET /webhook (verify), POST /webhook (receive → enqueue; 200 immediately)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from chanakya.app import diag
from chanakya.channel.parse import parse
from chanakya.config import get_settings
from chanakya.log import get_log

router = APIRouter()
log = get_log("webhook")


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


@router.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == get_settings().wa_verify_token:
        diag.bump("verify_ok")
        return PlainTextResponse(p.get("hub.challenge", ""))
    diag.bump("verify_failed")
    raise HTTPException(403)


@router.post("/webhook")
async def receive(request: Request):
    s = get_settings()
    received = datetime.now(UTC)
    body = await request.body()
    diag.bump("webhook_posts")
    diag.mark("last_webhook_at")
    if s.wa_app_secret or not s.demo_mode:
        if not verify_signature(s.wa_app_secret, body, request.headers.get("X-Hub-Signature-256")):
            diag.bump("bad_signature")
            log.warning("webhook_bad_signature")
            raise HTTPException(401)
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400) from None
    msgs, statuses = parse(payload, received)
    try:  # which account/number the webhook came from (ids only)
        e0 = (payload.get("entry") or [{}])[0]
        v0 = (e0.get("changes") or [{}])[0].get("value") or {}
        diag.mark(
            "last_webhook_source",
            {
                "waba_id": e0.get("id"),
                "field": (e0.get("changes") or [{}])[0].get("field"),
                "phone_number_id": (v0.get("metadata") or {}).get("phone_number_id"),
            },
        )
    except Exception:
        pass
    diag.bump("messages_in", len(msgs))
    diag.bump("statuses_in", len(statuses))
    if msgs:
        diag.mark("last_message_at")
    for st in statuses:
        if st.get("status") == "failed":
            diag.mark(
                "last_status_error",
                {
                    "codes": [e.get("code") for e in st.get("errors", [])],
                    "titles": [e.get("title") for e in st.get("errors", [])],
                },
            )
    rt = request.app.state.rt
    for m in msgs:
        rt.orch.spawn(rt.orch.handle_inbound(m))
    return {"ok": True}
