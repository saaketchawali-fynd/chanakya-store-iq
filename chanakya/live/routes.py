"""/live (the page) and /live/ws (one socket per customer conversation). Off unless LIVE_ENABLED=true.

Socket protocol
  page → server  JSON {"type": "start", "lang", "clang", "provider"} | {"type": "text", "text", "who"} |
                 {"type": "done"} | {"type": "options"} | {"type": "lang", "lang", "clang"} | {"type": "reset"}
                 binary: PCM16 mono frames at the rate the server announced
  server → page  {"type": "provider", "name", "rate"} | the Floor view {"type": "view", ...} | {"type": "error"}"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from chanakya.live import providers as PR
from chanakya.live.engine import LANGS, Floor
from chanakya.log import get_log

log = get_log("live")
router = APIRouter()
PAGE = Path(__file__).parent / "static" / "live.html"
_active = 0


def _allowed(s, key: str | None) -> bool:
    return bool(s.live_enabled) and (not s.live_key or key == s.live_key)


@router.get("/live", response_class=HTMLResponse)
async def page(request: Request, k: str | None = None):
    if not _allowed(request.app.state.rt.settings, k):
        raise HTTPException(404)
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.websocket("/live/ws")
async def socket(ws: WebSocket, k: str | None = None):
    global _active
    rt = ws.app.state.rt
    s = rt.settings
    if not _allowed(s, k) or _active >= s.live_max_sessions:
        await ws.close(code=4403)
        return
    await ws.accept()
    _active += 1
    floor = Floor(rt)
    prov: PR.Provider | None = None
    lock = asyncio.Lock()

    async def push() -> None:
        await ws.send_text(json.dumps(floor.view(), ensure_ascii=False, default=str))

    async def on_text(text: str) -> None:
        async with lock:
            floor.absorb(text)
            await push()

    async def on_heard(args: dict) -> dict:
        async with lock:
            res = floor.heard_tool(args)
            await push()
            return res

    async def use(name: str) -> None:
        nonlocal prov
        if prov:
            await prov.close()
            prov = None
        if name == "gemini":
            prov = PR.GeminiLive(s, rt.packs, floor.home.category, floor.lang, on_text, on_heard)
        elif name == "openai":
            prov = PR.OpenAIRealtime(s, floor.clang, on_text)
        if prov:
            try:
                await prov.start()
            except Exception as e:
                log.warning("live_provider_failed", provider=name, error=str(e)[:200])
                await prov.close()
                prov, name = None, "browser"
        await ws.send_text(json.dumps({"type": "provider", "name": name, "rate": PR.rate_for(name)}))

    async def failover() -> None:
        log.warning("live_failover", provider=prov.name if prov else None)
        nxt = "openai" if prov and prov.name == "gemini" and s.openai_api_key else "browser"
        await use(nxt)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if prov is None:
                    continue
                if getattr(prov, "failed", None):
                    await failover()
                    continue
                try:
                    await prov.audio(msg["bytes"])
                except Exception:
                    await failover()
                continue
            try:
                m = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            t = m.get("type")
            if t in ("start", "lang", "reset"):
                if t == "reset":
                    floor = Floor(rt, lang=floor.lang)
                lang = m.get("lang") if m.get("lang") in LANGS else floor.lang
                floor.lang = lang
                if m.get("clang") in LANGS:
                    floor.home.customer_lang = m["clang"]
                elif t == "start" and not floor.home.slots:
                    floor.home.customer_lang = lang
                if t == "start":
                    await use(PR.pick_provider(s, m.get("provider")))
                floor._next_cue(None)
            elif t == "text":
                floor.absorb(str(m.get("text") or "")[:500], who=m.get("who") or "customer")
            elif t == "done":
                floor.done_cue()
            elif t == "options":
                floor.force_options = True
                floor._next_cue(None)
            elif t == "stop":
                if prov:
                    await prov.close()
                    prov = None
                continue
            await push()
    except WebSocketDisconnect:
        pass
    finally:
        _active -= 1
        if prov:
            await prov.close()
