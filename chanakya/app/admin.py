"""GET /health; /admin/sessions, /admin/events, /admin/reset (ADMIN_TOKEN)."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from chanakya.config import get_settings
from chanakya.core import repo

router = APIRouter()


def _auth(token: str | None) -> None:
    if not token or token != get_settings().admin_token:
        raise HTTPException(401)


@router.get("/health")
async def health(request: Request):
    rt = getattr(request.app.state, "rt", None)
    db_ok = True
    try:
        await repo.setting_get("health")
    except Exception:
        db_ok = False
    from chanakya.app import diag

    s = get_settings()
    phones = {
        "associate": s.associate_phone,
        "customer": s.customer_phone,
        "manager": s.manager_phone,
        "expert": s.expert_phone,
        "test_number": s.wa_test_number,
    }
    config = {
        "wa_token_set": bool(s.wa_token),
        "phone_number_id_set": bool(s.wa_phone_number_id),
        "app_secret_set": bool(s.wa_app_secret),
        "verify_token_set": bool(s.wa_verify_token),
        "ai_mode": s.ai_mode,
        "phones": {
            k: ("placeholder" if v == "15551234567" else "ok" if len(v) >= 10 else "missing" if not v else "too_short")
            for k, v in phones.items()
        },
    }
    return {
        "ok": bool(rt) and db_ok,
        "db": db_ok,
        "renderer": bool(rt and rt.renderer and getattr(rt.renderer, "ready", False)),
        "packs": len(rt.packs.packs) if rt else 0,
        "config": config,
        "delivery": diag.snapshot(),
    }


@router.get("/admin/sessions")
async def sessions(x_admin_token: str | None = Header(default=None)):
    _auth(x_admin_token)
    from chanakya.core.session import SessionRepo

    out = await SessionRepo().all_active()
    return [s.model_dump(mode="json") for s in out]


@router.get("/admin/events")
async def events(session: str | None = None, x_admin_token: str | None = Header(default=None)):
    _auth(x_admin_token)
    rows = await repo.events_for(session)
    return [
        {
            "id": r.id,
            "type": r.type,
            "session_id": r.session_id,
            "payload": r.payload,
            "sources": r.sources,
            "latency_ms": r.latency_ms,
        }
        for r in rows[-500:]
    ]


@router.post("/admin/reset")
async def reset(x_admin_token: str | None = Header(default=None)):
    _auth(x_admin_token)
    from datetime import timedelta

    from chanakya.core.clock import set_offset

    set_offset(timedelta(0))
    await repo.setting_set("demo_offset_s", 0)
    from chanakya.core.clock import now
    from chanakya.core.session import SessionRepo

    sr = SessionRepo()
    for s in await sr.all_active():
        await sr.close(s, "reset", now())
    return {"ok": True}
