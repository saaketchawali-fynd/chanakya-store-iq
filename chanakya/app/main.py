"""FastAPI app; lifespan: DB init + seed, packs, renderer pool, scheduler."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from chanakya.app import admin, legal, webhook
from chanakya.app.bootstrap import build, shutdown, start_data_sync, start_renderer
from chanakya.channel.whatsapp import WhatsAppChannel
from chanakya.config import get_settings
from chanakya.log import get_log, setup_logging

log = get_log("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    rt = await build(s, WhatsAppChannel(s))
    await start_renderer(rt)
    start_data_sync(rt)
    if s.scheduler_enabled:
        rt.scheduler.start()
    app.state.rt = rt
    log.info("started", ai_mode=rt.ai.mode, packs=len(rt.packs.packs), store=s.store_id, data=s.data_source)
    yield
    await shutdown(rt)


app = FastAPI(title="Chanakya — Store IQ (TEST)", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(legal.router)
