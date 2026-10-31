"""Latency report: time to first reply through the full pipeline (route → skill → validator → send).

With no speech model wired, this measures the pipeline on the understanding fixtures and typed Hinglish /
Marathi notes (rules mode). Add WhatsApp media download (~0.6 s) and model time when a model is plugged in.
Usage: python scripts/latency_report.py [--runs 20]   → prints p50/p95, writes latency.csv"""

import argparse
import asyncio
import csv
import statistics
import tempfile
import time
from pathlib import Path

from chanakya.log import setup_logging
from chanakya.sim.harness import Harness

NOTES = [
    ("fixture", "assess_1"), ("fixture", "noisy_partial"), ("fixture", "price_ask"), ("fixture", "assess_2_claim"),
    ("rules", "Shah ji ka living room, sofa se das feet, dhoop aati hai, papa cricket dekhte hain"),
    ("rules", "खोलीत ऊन येतं, टीव्ही भिंतीवर लावणार, सोफ्यापासून 10 फूट"),
    ("rules", "customer pooch rahe hain ye TV kitne ka hai"),
    ("rules", "ghar mein dadi bhi dekhti hain, dialogue saaf chahiye"),
]


async def main(runs: int) -> None:
    setup_logging("ERROR")
    rows = []
    with tempfile.TemporaryDirectory() as d:
        for mode in ("fixture", "rules"):
            h = await Harness.create(f"sqlite+aiosqlite:///{Path(d) / f'{mode}.db'}", ai_mode=mode)
            try:
                for i in range(runs):
                    for kind, note in [n for n in NOTES if n[0] == mode]:
                        await h.say("reset")
                        await h.tap("g:new")
                        await h.tap("s:C1:lang:mr" if i % 2 else "s:C1:lang:hi")
                        t0 = time.perf_counter()
                        out = await (h.voice(note) if kind == "fixture" else h.say(note))
                        ms = (time.perf_counter() - t0) * 1000
                        rows.append({"mode": mode, "note": note[:40], "ms": round(ms, 1), "messages": len(out)})
            finally:
                await h.close()
    ms = sorted(r["ms"] for r in rows)
    p50 = statistics.median(ms)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    with open("latency.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mode", "note", "ms", "messages"])
        w.writeheader()
        w.writerows(rows)
    print(f"n={len(ms)}  p50={p50:.0f} ms  p95={p95:.0f} ms  (pipeline only; target p95 < 4000 ms incl. media+model)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    asyncio.run(main(ap.parse_args().runs))
