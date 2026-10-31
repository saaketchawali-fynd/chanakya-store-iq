"""Interactive simulator: python -m chanakya.sim [--rules] [--render] [--script FILE]

Runs the real orchestrator with FakeChannel. Prints every outbound message (body, buttons with ids) and saves
rendered images to ./sim_out/NNN_<template>.jpg when --render is on.

Commands:
  as <associate|customer|manager|expert|visitor>   switch the sending phone
  say <text>                                        text message
  voice <fixture_key>                               'FIX:<key>' stand-in for a voice note (fixture mode)
  photo <path.jpg>                                  image (bare names resolve to data/fixtures/images/)
  tap <button_id> / pick <row_id>                   button / list reply ({token} and {s8} are expanded)
  clock <+1d|install|+7d|reset>
  dump session|events|score
  quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from chanakya.config import ROOT
from chanakya.core import repo
from chanakya.log import setup_logging
from chanakya.sim.harness import PHONES, Harness

OUT = Path("sim_out")


def render_msgs(msgs, ch, counter: list[int]) -> None:
    rev = {v: k for k, v in PHONES.items()}
    for m in msgs:
        who = rev.get(m.to, m.to)
        tag = f" [{m.card.template} card]" if m.card else (" [image]" if m.image_bytes else "")
        print(f"  → {who} · {m.kind}{tag}")
        for line in (m.body or m.caption or "").split("\n"):
            if line:
                print(f"      {line}")
        for b in m.buttons:
            print(f"      [{b.title}]  ({b.id})")
        for s in m.sections:
            for r in s.rows:
                print(f"      • {r.title}  ({r.id})")
        img = None
        if m.media_id and m.media_id in ch.media:
            img = ch.media[m.media_id][0]
        img = img or m.image_bytes
        if img:
            OUT.mkdir(exist_ok=True)
            counter[0] += 1
            name = OUT / f"{counter[0]:03d}_{m.card.template if m.card else 'image'}.jpg"
            name.write_bytes(img)
            print(f"      (saved {name})")


async def run_line(h: Harness, line: str, counter: list[int]) -> bool:
    line = line.strip()
    if not line or line.startswith("#"):
        return True
    cmd, _, rest = line.partition(" ")
    s = None
    if "{token}" in rest or "{s8}" in rest:
        rows = await h.rt.svc.sessions.active(PHONES["associate"])
        s = rows[0] if rows else None
        if s is None:
            from sqlalchemy import select

            from chanakya.truth import db
            from chanakya.truth.models import SessionRow

            async with db.session() as ss:
                r = (
                    await ss.execute(select(SessionRow).order_by(SessionRow.updated_at.desc()).limit(1))
                ).scalar_one_or_none()
                s = await h.rt.svc.sessions.get(r.id) if r else None
        if s:
            rest = rest.replace("{token}", s.optin_token or "").replace("{s8}", s.id[:8])
    print(f"\n» {h.who}: {line}")
    if cmd == "quit":
        return False
    if cmd == "as":
        h.as_(rest.strip())
        return True
    if cmd == "dump":
        st_rows = await h.rt.svc.sessions.active(PHONES["associate"])
        if rest == "session" and st_rows:
            print(json.dumps(st_rows[0].model_dump(mode="json"), ensure_ascii=False, indent=1)[:4000])
        elif rest == "events":
            for e in await repo.events_for():
                print(f"  {e.type} {e.payload}")
        elif rest == "score":
            for sc in await repo.scores_for(limit=3):
                print(f"  {sc.total}/14 {sc.steps}")
        return True
    fn = {"say": h.say, "voice": h.voice, "photo": h.photo, "tap": h.tap, "pick": h.pick, "clock": h.clock}.get(cmd)
    if fn is None:
        print("  ? unknown command")
        return True
    msgs = await fn(rest.strip())
    render_msgs(msgs, h.ch, counter)
    return True


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", action="store_true", help="offline rules understanding instead of fixtures")
    ap.add_argument("--render", action="store_true", help="render cards with Playwright and save to ./sim_out")
    ap.add_argument("--script", help="run a script of simulator commands")
    ap.add_argument("--db", default=f"sqlite+aiosqlite:///{ROOT / 'sim.db'}")
    a = ap.parse_args()
    setup_logging("WARNING")
    h = await Harness.create(a.db, ai_mode="rules" if a.rules else "fixture", render=a.render, lang_set=False)
    counter = [0]
    try:
        if a.script:
            for line in Path(a.script).read_text(encoding="utf-8").splitlines():
                if not await run_line(h, line, counter):
                    break
            return
        print(__doc__)
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, lambda: input(f"{h.who}> "))
            if not await run_line(h, line, counter):
                break
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await h.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
