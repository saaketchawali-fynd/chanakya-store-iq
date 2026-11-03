"""Instance memory, read from Linux (cgroup + /proc); None elsewhere.

Shown on /health (what the instance uses against its limit, and which process holds it), and used to start
Chromium fresh before the instance nears its limit: Render restarts the whole service when it goes over."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

ROOT = Path("/")
DEFAULT_LIMIT_MB = 512  # Render Starter, when the container does not expose its limit


def _int(rel: str) -> int | None:
    try:
        v = (ROOT / rel).read_text().strip()
        return None if v == "max" else int(v)
    except (OSError, ValueError):
        return None


def cgroup() -> tuple[int | None, int | None, int | None]:
    """(used, limit, peak) in bytes, cgroup v2 then v1."""
    used = _int("sys/fs/cgroup/memory.current")
    if used is not None:
        return used, _int("sys/fs/cgroup/memory.max"), _int("sys/fs/cgroup/memory.peak")
    used = _int("sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _int("sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit is not None and limit > 1 << 50:  # v1 'no limit'
        limit = None
    return used, limit, _int("sys/fs/cgroup/memory/memory.max_usage_in_bytes")


def file_cache() -> int:
    """Reclaimable page cache (inactive file pages) inside the cgroup: fonts, the Chromium binary, images read.
    Linux frees it before ever killing the service, so it is not memory pressure."""
    for rel, key in (("sys/fs/cgroup/memory.stat", "inactive_file"), ("sys/fs/cgroup/memory/memory.stat", "total_inactive_file")):
        try:
            for line in (ROOT / rel).read_text().splitlines():
                k, _, v = line.partition(" ")
                if k == key:
                    return int(v)
        except (OSError, ValueError):
            continue
    return 0


def working_set() -> int | None:
    """What the service really holds: usage minus reclaimable cache (as docker stats and Kubernetes measure)."""
    used = cgroup()[0]
    return None if used is None else max(0, used - file_cache())


def limit_bytes() -> int:
    limit = cgroup()[1]
    return limit or int(os.environ.get("MEMORY_LIMIT_MB") or DEFAULT_LIMIT_MB) * 1024 * 1024


def used_fraction() -> float | None:
    """Memory pressure for decisions (start Chromium fresh): the working set, not the cache-inflated total."""
    used = working_set()
    return used / limit_bytes() if used is not None else None


def _group(name: str) -> str:
    n = name.lower()
    if "python" in n or "uvicorn" in n:
        return "python"
    if "chrom" in n or "headless" in n:
        return "chromium"
    return n or "other"  # e.g. node: the Playwright driver


def _kb(text: str, key: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(key):
            return int(line.split()[1])
    return None


def by_process() -> dict[str, int]:
    """Memory (MB) per kind of process: python, chromium, node (the Playwright driver), others by name.
    Proportional (Pss) where Linux gives it: Chromium's processes share memory, which plain RSS counts once per
    process, so the parts would add up to more than the instance uses."""
    out: dict[str, int] = {}
    proc = ROOT / "proc"
    if not proc.is_dir():
        return out
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            status = (d / "status").read_text()
            name = next((ln.split(None, 1)[1].strip() for ln in status.splitlines()
                         if ln.startswith("Name:") and len(ln.split()) > 1), "")
            try:
                kb = _kb((d / "smaps_rollup").read_text(), "Pss:")
            except OSError:
                kb = None
            kb = kb if kb is not None else _kb(status, "VmRSS:")
            if kb:
                g = _group(name)
                out[g] = out.get(g, 0) + kb * 1024
        except (OSError, ValueError, IndexError):
            continue
    small = sum(v for v in out.values() if v < 5e6)
    out = {k: v for k, v in out.items() if v >= 5e6} | ({"other": small} if small else {})
    return {k: round(v / 1e6) for k, v in sorted(out.items())}


def _mb(b: int | None) -> int | None:
    return round(b / 1e6) if b is not None else None


def used_mb() -> int | None:
    return _mb(working_set())


def snapshot() -> dict:
    used, limit, peak = cgroup()
    return {"used_mb": _mb(working_set()), "with_cache_mb": _mb(used), "cache_mb": _mb(file_cache()) if used else None,
            "limit_mb": _mb(limit or limit_bytes()), "peak_mb": _mb(peak), "by_process": by_process()}


def trim() -> None:
    """Hand freed memory back to the OS (glibc keeps it otherwise, so a photo job would raise memory for good)."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
