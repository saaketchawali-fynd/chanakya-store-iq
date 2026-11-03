"""GET /health; /admin/sessions, /admin/events, /admin/reset (ADMIN_TOKEN)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from chanakya.config import get_settings
from chanakya.core import repo

router = APIRouter()


def _auth(token: str | None) -> None:
    if not token or token != get_settings().admin_token:
        raise HTTPException(401)


def _fonts() -> dict:
    try:
        from chanakya.tryon.fonts import describe

        return describe()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


async def _data(s, rt) -> dict:
    """Where product data comes from; for Jio: token set, last sync, counts, last error (never the token)."""
    out = {"source": s.data_source, "products_loaded": len(rt.facts.snap.products) if rt else 0}
    if s.data_source == "jio":
        from chanakya.truth import jio_sync

        st = dict(jio_sync.STATUS, configured=jio_sync.configured(s))
        try:
            meta = await jio_sync.last_meta()
        except Exception:  # noqa: BLE001
            meta = None
        out.update(domain=s.jio_domain, token_set=st["configured"], syncing=st["running"],
                   last_sync=(meta or {}).get("at"), counts=(meta or {}).get("counts") or st["counts"],
                   last_error=st["last_error"], catalog=(meta or {}).get("catalog"))
    return out


@router.get("/health")
async def health(request: Request):
    rt = getattr(request.app.state, "rt", None)
    db_ok = True
    try:
        await repo.setting_get("health")
    except Exception:
        db_ok = False
    from chanakya.app import diag, memory

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
        "gemini_key_set": bool(s.gemini_api_key),
        "ai_mode_effective": rt.ai.mode if rt else None,
        "verify_token_set": bool(s.wa_verify_token),
        "ai_mode": s.ai_mode,
        "phones": {
            k: ("placeholder" if v == "15551234567" else "ok" if len(v) >= 10 else "missing" if not v else "too_short")
            for k, v in phones.items()
        },
    }
    return {
        "ok": bool(rt) and db_ok,
        "version": (__import__("os").environ.get("RENDER_GIT_COMMIT") or "local")[:7],
        "db": db_ok,
        "renderer": bool(rt and rt.renderer and getattr(rt.renderer, "ready", False)),
        "packs": len(rt.packs.packs) if rt else 0,
        "tryon_fonts": _fonts(),
        "data": await _data(s, rt),
        "memory": memory.snapshot(),
        "chromium": rt.renderer.stats() if rt and rt.renderer and hasattr(rt.renderer, "stats") else None,
        "config": config,
        "delivery": diag.snapshot(),
    }


_VOICE_CHECK: dict = {}
VOICE_SAMPLE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "audio" / "voice_check_hi.ogg"


@router.get("/health/voice")
async def voice_check(request: Request):
    """The voice path, measured on this server with its own key: a short Hindi sample note is transcribed (across the
    models, as for a real note), then understood. Cached 5 minutes, so it cannot run up costs."""
    import time

    from chanakya.ai import prompts
    from chanakya.ai.gemini import AIUnavailable
    from chanakya.app import diag

    now = time.time()
    if _VOICE_CHECK and now - _VOICE_CHECK.get("_at", 0) < 300:
        return {k: v for k, v in _VOICE_CHECK.items() if not k.startswith("_")} | {"cached": True}
    rt = request.app.state.rt
    ai = rt.ai
    if not ai.live or not VOICE_SAMPLE.is_file():
        return {"ok": False, "error": "voice model not configured" if not ai.live else "sample missing"}
    from google.genai import types

    audio = VOICE_SAMPLE.read_bytes()
    out: dict = {"ok": False}
    t0 = time.perf_counter()
    try:
        part = types.Part.from_bytes(data=audio, mime_type="audio/ogg")
        text = await ai.gemini.transcribe([part], prompts.TRANSCRIBE_SYSTEM, timeout=24.0)
        t1 = time.perf_counter()
        last = diag.snapshot().get("ai_last_transcribe") or {}
        out.update(transcript=text[:200], transcribe_ms=int((t1 - t0) * 1000), transcribe_model=last.get("model"),
                   models_tried=last.get("tried"))
        pack = rt.packs.get("tv")  # the same context a real note gets: the TV questions and their answer ids
        ctx = {"category": "tv", "known_slots": {}, "slots": [
            {"id": sd.id, "hint": sd.ask.get("en", ""), "number_name": sd.number,
             "values": [{"id": v, "means": sd.value_title(v, "en")} for v in sd.values]} for sd in pack.slots]}
        u = await ai._understand_text(text, ctx)
        t2 = time.perf_counter()
        lu = diag.snapshot().get("ai_last_understand_text") or {}
        out.update(ok=True, understand_ms=int((t2 - t1) * 1000), understand_model=lu.get("model") if lu.get("ok") else "rules",
                   slots=[f"{s.slot}={s.value}" for s in u.slots], numbers=[f"{n.name}={n.value}" for n in u.numbers],
                   total_ms=int((t2 - t0) * 1000))
    except AIUnavailable as e:
        out.update(error=str(e)[:300], total_ms=int((time.perf_counter() - t0) * 1000))
    _VOICE_CHECK.clear()
    _VOICE_CHECK.update(out, _at=now)
    return out


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
