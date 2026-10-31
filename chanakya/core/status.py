"""Status line: 📍 C1 · Shah · TV · Ghar 4/7 · Demo – · Daam 🔒 (≤ 60 chars)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chanakya.core import state

if TYPE_CHECKING:
    from chanakya.skills.base import SkillContext


def short_name(name: str | None) -> str:
    if not name:
        return "–"
    n = name.strip()
    for suf in (" ji", " Ji", " जी"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    for pre in ("Mrs ", "Mr ", "Mrs. ", "Mr. ", "Ms "):
        if n.startswith(pre):
            n = n[len(pre) :]
    return n[:12]


def status_line(ctx: SkillContext) -> str:
    s = ctx.session
    if s is None:
        return ""
    pack = ctx.pack
    L = ctx.lang
    cat = ctx.pick(pack.label) if pack else "–"
    known = pack.known_required(s.home) if pack else 0
    total = len(pack.required_slots) if pack else 0
    code = ctx.reg.raw(s.code, "session:code")
    k = ctx.reg.num(known, "card:known")
    tt = ctx.reg.num(total, "pack:required")
    demo = "✓" if s.demo_done else "–"
    lock = "🔓" if state.price_gate_open(s, pack) else "🔒"
    line = (
        f"📍 {code} · {short_name(s.home.customer_name)} · {cat} · {ctx.t('status_home', L)} {k}/{tt} · "
        f"{ctx.t('status_demo', L)} {demo} · {ctx.t('status_price', L)} {lock}"
    )
    if len(line) > 60:
        line = line.replace(f" · {short_name(s.home.customer_name)}", "", 1)
    return line
