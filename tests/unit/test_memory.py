"""Memory on the 512 MB instance (Render restarted the service when it went over):
- Chromium: no element handle outlives a card, the page process is replaced every few cards and when the
  instance nears its limit, so the cards it has drawn cannot pile up;
- try-on: one heavy drawing at a time, and the associate's copy only redraws the labels;
- the memory readings on /health (cgroup + /proc)."""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from PIL import Image

from chanakya.app import memory
from chanakya.core.contracts import Card
from chanakya.render import renderer as R
from chanakya.sim.harness import FIX_IMAGES
from chanakya.tryon import pipeline


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_cgroup_readings_v2_and_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "ROOT", tmp_path)
    assert memory.cgroup() == (None, None, None) and memory.used_fraction() is None  # not Linux: nothing claimed
    _write(tmp_path, "sys/fs/cgroup/memory.current", "268435456\n")
    _write(tmp_path, "sys/fs/cgroup/memory.max", "536870912\n")
    _write(tmp_path, "sys/fs/cgroup/memory.peak", "402653184\n")
    assert memory.cgroup() == (268435456, 536870912, 402653184)
    assert memory.used_fraction() == 0.5
    snap = memory.snapshot()
    assert (snap["used_mb"], snap["limit_mb"], snap["peak_mb"]) == (268, 537, 403)
    # 3 Nov 2026 on Render: 497 MB 'used' at idle was mostly file cache (fonts, the Chromium binary). Decisions use
    # the working set (usage minus inactive file cache), as docker stats and Kubernetes do.
    _write(tmp_path, "sys/fs/cgroup/memory.stat", "anon 100000000\nfile 150000000\ninactive_file 134217728\n")
    assert memory.working_set() == 268435456 - 134217728 and memory.used_fraction() == 0.25
    snap = memory.snapshot()
    assert snap["used_mb"] == 134 and snap["with_cache_mb"] == 268 and snap["cache_mb"] == 134
    _write(tmp_path, "sys/fs/cgroup/memory.max", "max\n")  # no limit set: fall back to the plan's 512 MB
    assert memory.limit_bytes() == 512 * 1024 * 1024
    v1 = tmp_path / "v1"
    monkeypatch.setattr(memory, "ROOT", v1)
    _write(v1, "sys/fs/cgroup/memory/memory.usage_in_bytes", "100")
    _write(v1, "sys/fs/cgroup/memory/memory.limit_in_bytes", str(1 << 62))
    assert memory.cgroup() == (100, None, None)


def test_memory_by_process_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "ROOT", tmp_path)
    for pid, name, kb in ((7, "python3.12", 200_000), (40, "chrome-headless", 90_000), (41, "chrome-headless", 60_000),
                          (30, "node", 50_000), (1, "sh", 1_000)):
        _write(tmp_path, f"proc/{pid}/status", f"Name:\t{name}\nState:\tS\nVmRSS:\t{kb} kB\n")
    _write(tmp_path, "proc/99/status", "Name:\tkthreadd\n")  # no VmRSS: skipped
    _write(tmp_path, "proc/41/smaps_rollup", "Rss:\t60000 kB\nPss:\t30000 kB\n")  # shared pages counted once
    assert memory.by_process() == {"chromium": 123, "node": 51, "other": 1, "python": 205}


@pytest.fixture
async def fresh_renderer(monkeypatch):
    monkeypatch.setattr(R, "RECYCLE_AFTER", 3)
    r = R.Renderer()
    await r.start(warm_up=False)
    if not r.ready:
        pytest.skip("Chromium not available")
    yield r
    await r.stop()


def _card(tpl: str) -> Card:
    return Card.model_construct(template=tpl, lang="en", data=R.sample_data(tpl, "en"))


async def _dom_nodes(page) -> int:
    s = await page.context.new_cdp_session(page)
    await s.send("Performance.enable")
    m = {x["name"]: x["value"] for x in (await s.send("Performance.getMetrics"))["metrics"]}
    await s.detach()
    return int(m["Nodes"])


async def test_chromium_keeps_no_card_and_starts_fresh(fresh_renderer):
    r = fresh_renderer
    first_page = r.page
    for i in range(7):
        assert (await r.render(_card(("options", "kharcha", "offer")[i % 3])))[:3] == b"\xff\xd8\xff"
    assert r.fresh_starts == 2 and r.renders == 1 and r.page is not first_page and first_page.is_closed()
    handles = [o for o in r.page._impl_obj._connection._objects.values() if type(o).__name__ == "ElementHandle"]
    assert not handles  # a handle keeps its whole card (pictures included) alive in Chromium
    await r.render(_card("options"))
    assert await _dom_nodes(r.page) < 1500  # one card's worth, not every card drawn so far


async def test_chromium_starts_fresh_when_memory_is_high(fresh_renderer, monkeypatch):
    r = fresh_renderer
    monkeypatch.setattr(R, "RECYCLE_AFTER", 100)  # only the memory rule here, not the routine recycle
    await r.render(_card("offer"))
    monkeypatch.setattr(memory, "used_fraction", lambda: 0.9)
    for _ in range(3):
        await r.render(_card("offer"))
    assert r.fresh_starts == 0  # a few cards first: never a fresh start on every card (the 6 Nov start-up cascade)
    await r.render(_card("offer"))
    assert r.fresh_starts == 1  # then a clean page process while memory stays high
    monkeypatch.setattr(memory, "used_fraction", lambda: 0.5)
    await r.render(_card("offer"))
    assert r.fresh_starts == 1


async def test_chromium_comes_back_after_a_failed_card(fresh_renderer):
    r = fresh_renderer
    await r.page.close()  # e.g. the page process was killed
    assert (await r.render(_card("offer")))[:3] == b"\xff\xd8\xff"
    assert r.renders == 1 and not r.page.is_closed()


def _req() -> pipeline.TryonRequest:
    im = Image.open(FIX_IMAGES / "room_living.jpg").convert("RGB").resize((1600, 1200))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return pipeline.TryonRequest(
        photo=b.getvalue(), read=None, wall_width_ft=10, footer_text="Approximate preview",
        products=[pipeline.TryonProduct(sku="A", category="tv", width_mm=1230, height_mm=710, label='55" Arka', solid=True)],
    )


async def test_tryon_draws_one_at_a_time(monkeypatch):
    live = most = 0
    real = pipeline._prepare

    def counted(*a):
        nonlocal live, most
        live += 1
        most = max(most, live)
        try:
            time.sleep(0.05)
            return real(*a)
        finally:
            live -= 1

    monkeypatch.setattr(pipeline, "_prepare", counted)
    req = _req()
    results = await asyncio.gather(*(pipeline.run(req) for _ in range(3)))
    assert all(r.image for r in results) and most == 1  # ~120 MB each at their peak: never two at once


def test_debug_lists_are_bounded(rt):
    assert rt.svc.violations.maxlen == 200
