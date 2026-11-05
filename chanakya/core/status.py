"""Status line: C1 Shah · TV · Ghar 4/7 · Demo baaki · Daam band (plain words, no symbols)."""

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
    if not s.home.category:  # no product picked yet: 'C1 · New customer'
        return ctx.t("st_new", L, code=ctx.reg.raw(s.code, "session:code"))
    cat = ctx.pick(pack.label) if pack else "–"
    known, total = state.required_progress(s, pack) if pack else (0, 0)
    code = ctx.reg.raw(s.code, "session:code")
    k = ctx.reg.num(known, "card:known")
    tt = ctx.reg.num(total, "pack:required")
    demo = ctx.t("st_demo_done" if s.demo_done else "st_demo_pending", L)
    price = ctx.t("st_price_open" if state.price_gate_open(s, pack) else "st_price_locked", L)
    name = short_name(s.home.customer_name)
    who = f"{code} {name}" if name and name != "–" else code
    line = f"{who} · {cat} · {ctx.t('status_home', L)} {k}/{tt} · {demo} · {price}"
    return line
