"""Build the runtime (DB, seed, packs, facts, AI, services, orchestrator, scheduler) for the app, simulator and tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from chanakya.ai.client import AIClient
from chanakya.brain.packs import PackRegistry, load_packs
from chanakya.config import Settings
from chanakya.core import clock, repo
from chanakya.core.orchestrator import Orchestrator
from chanakya.core.services import Services, set_services
from chanakya.i18n.lang import reload_strings, strings
from chanakya.jobs.scheduler import Scheduler
from chanakya.truth import db, seed
from chanakya.truth.facts import FactsService


@dataclass
class Runtime:
    settings: Settings
    packs: PackRegistry
    facts: FactsService
    ai: AIClient
    svc: Services
    orch: Orchestrator
    scheduler: Scheduler
    renderer: object | None = None
    data_task: object | None = None  # live mode: the Jio catalogue sync loop


async def build(settings: Settings, channel, drop: bool = False, render: bool | None = None) -> Runtime:
    db.init_engine(settings.database_url)
    await db.create_all(drop=drop)
    if settings.data_source == "seed":  # automated tests and the offline simulator only
        await seed.load()
    else:  # live: product, price and offer data come only from Jio Commerce (jio_sync)
        from chanakya.truth import jio_sync

        await jio_sync.purge_seed()
        await seed.load_logic()
    await seed.upsert_staff(settings)
    reload_strings()
    assert strings(), "i18n strings missing"
    packs = load_packs()
    facts = FactsService(settings.store_id)
    await facts.refresh()
    ai = AIClient(settings, packs)
    renderer = None
    render_fn = None
    use_render = settings.render_cards if render is None else render
    if use_render:
        from chanakya.render.renderer import get_renderer

        renderer = get_renderer()
        render_fn = renderer.render
    svc = Services(settings, channel, facts, packs, ai, render=render_fn)
    set_services(svc)
    scheduler = Scheduler(svc)
    orch = Orchestrator(svc, scheduler)
    off = await repo.setting_get("demo_offset_s", 0)
    if off and settings.demo_mode:
        clock.set_offset(timedelta(seconds=float(off)))
    return Runtime(settings, packs, facts, ai, svc, orch, scheduler, renderer)


async def start_renderer(rt: Runtime) -> None:
    if rt.renderer is not None:
        await rt.renderer.start()


def start_data_sync(rt: Runtime) -> None:
    """Live mode: sync the catalogue from Jio Commerce now and every JIO_SYNC_MINUTES, in the background (the app
    serves from the last synced data meanwhile)."""
    if rt.settings.data_source == "jio":
        import asyncio

        from chanakya.truth import jio_sync

        rt.data_task = asyncio.create_task(jio_sync.loop(rt.settings, rt.facts))


async def shutdown(rt: Runtime) -> None:
    if rt.data_task is not None:
        rt.data_task.cancel()
    await rt.scheduler.stop()
    if rt.renderer is not None:
        try:
            await rt.renderer.stop()
        except Exception:
            pass
    await db.dispose()
