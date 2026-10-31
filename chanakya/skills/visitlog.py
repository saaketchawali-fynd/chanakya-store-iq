"""Nirikshan — store visit log by voice/text: extract → draft → ≤ 3 missing fields → save → CSV export.
Names are never stored (post-filter replaces capitalised person names with 'associate')."""

from __future__ import annotations

import csv
import io
import re

from chanakya.ai.client import AIUnavailable
from chanakya.ai.schemas import VisitEntry
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, OutMessage, Reply
from chanakya.skills.base import SkillContext

STEPS = ["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
KEY_FIELDS = ["price_first_by", "demo_shown", "would_come_back"]
SAFE_WORDS = {
    "Reliance",
    "Digital",
    "Croma",
    "Vijay",
    "Sales",
    "Juhu",
    "Andheri",
    "Belapur",
    "Ulwe",
    "Citi",
    "Mall",
    "TV",
    "AC",
    "HDFC",
    "SBI",
    "ICICI",
    "EMI",
    "QLED",
    "OLED",
    "Meru",
    "Arka",
    "Novex",
    "The",
    "He",
    "She",
    "Mumbai",
    "Navi",
    "West",
    "CBD",
}


def scrub_names(text: str | None) -> str | None:
    if not text:
        return text

    def rep(m):
        w = m.group(0)
        return w if w in SAFE_WORDS or w.isupper() else "associate"

    return re.sub(r"\b[A-Z][a-z]{2,}\b", rep, text)


def _merge(old: dict, new: VisitEntry) -> dict:
    out = dict(old)
    for k, v in new.model_dump().items():
        if k == "steps":
            cur = {s["step"]: s for s in out.get("steps", [])}
            for s in v:
                s["evidence"] = scrub_names(s.get("evidence"))
                cur[s["step"]] = s
            out["steps"] = list(cur.values())
        elif v not in (None, [], ""):
            out[k] = scrub_names(v) if isinstance(v, str) and k in ("best_sentence", "worst_sentence", "notes") else v
    return out


async def start(ctx: SkillContext) -> Reply:
    await repo.visit_save(None, ctx.actor_wa_id, {}, "draft")
    return ctx.reply(info_reply(ctx, ctx.t("visit_start")))


async def on_note(ctx: SkillContext) -> Reply:
    draft = await repo.visit_draft(ctx.actor_wa_id)
    if draft is None:
        draft = await repo.visit_save(None, ctx.actor_wa_id, {}, "draft")
    audio = None
    if ctx.inbound.type == "audio" and ctx.inbound.media_id:
        audio, _ = await ctx.svc.channel.download_media(ctx.inbound.media_id)
    try:
        entry = await ctx.ai.extract_visit(ctx.inbound.text, audio)
    except AIUnavailable:
        return ctx.reply(info_reply(ctx, ctx.t("voice_unavailable")))
    merged = _merge(draft.entry or {}, entry)
    draft = await repo.visit_save(draft.id, ctx.actor_wa_id, merged, "draft")
    return ctx.reply(_next_missing(ctx, draft.id, merged))


def _next_missing(ctx: SkillContext, log_id: int, entry: dict) -> OutMessage:
    for f in KEY_FIELDS:
        if entry.get(f) is None:
            if f == "price_first_by":
                opts = [("associate", "b_v_associate"), ("customer", "b_v_customer"), ("none", "b_v_none")]
            else:
                opts = [("yes", "b_yes"), ("no", "b_no")]
            btns = [Button(id=encode("v", log_id, f, val), title=ctx.t(key)) for val, key in opts]
            return info_reply(ctx, ctx.t(f"visit_ask_{f}"), btns)
    n = ctx.reg.num(len(entry.get("steps", [])), "visit:steps")
    return info_reply(
        ctx,
        ctx.t("visit_ready", n=n),
        [
            Button(id=encode("v", log_id, "save", "1"), title=ctx.t("b_save")),
            Button(id=encode("v", log_id, "more", "1"), title=ctx.t("b_add_more")),
        ],
    )


async def button(ctx: SkillContext, log_id: str, field: str, val: str) -> Reply:
    draft = await repo.visit_draft(ctx.actor_wa_id)
    if draft is None or str(draft.id) != log_id:
        return ctx.reply(info_reply(ctx, ctx.t("visit_none")))
    entry = dict(draft.entry or {})
    if field == "save":
        await repo.visit_save(draft.id, ctx.actor_wa_id, entry, "final")
        btns = [
            Button(id=encode("v", 0, "export", "1"), title=ctx.t("b_export")),
            Button(id=encode("v", 0, "new", "1"), title=ctx.t("b_new_visit")),
        ]
        return ctx.reply(info_reply(ctx, ctx.t("visit_saved", id=ctx.reg.raw(str(draft.id), "visit:id")), btns))
    if field == "more":
        return ctx.reply(info_reply(ctx, ctx.t("visit_start")))
    if field == "price_first_by":
        entry[field] = val
    elif field in ("demo_shown", "would_come_back"):
        entry[field] = val == "yes"
    await repo.visit_save(draft.id, ctx.actor_wa_id, entry, "draft")
    return ctx.reply(_next_missing(ctx, draft.id, entry))


def to_csv(rows: list) -> bytes:
    cols = (
        [
            "id",
            "store",
            "date",
            "scenario",
            "questions_before_first_product",
            "price_first_by",
            "minute_first_price",
            "demo_shown",
            "full_cost_given",
            "would_come_back",
            "best_sentence",
            "worst_sentence",
            "notes",
        ]
        + STEPS
        + ["total"]
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        e = r.entry or {}
        steps = {s["step"]: s.get("score") for s in e.get("steps", [])}
        total = sum(v for v in steps.values() if isinstance(v, int))
        w.writerow([r.id] + [e.get(c) for c in cols[1:13]] + [steps.get(s) for s in STEPS] + [total])
    return buf.getvalue().encode("utf-8")


async def export(ctx: SkillContext) -> Reply:
    rows = await repo.visits_all(final_only=True)
    data = to_csv(rows)
    mid = await ctx.svc.channel.upload_media(data, "text/csv", "visit_log.csv")
    n = ctx.reg.num(len(rows), "visit:count")
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="document",
            media_id=mid,
            filename="visit_log.csv",
            caption=ctx.t("visit_export", n=n),
            allowed_numbers=ctx.reg.allowed(),
        )
    )
