"""The associate's own name: asked once, right after the language, and used everywhere (the pre-read, the Family
Card, "Talk to …"). No dummy name. 'naam <name>' changes it later."""

from __future__ import annotations

import re

from chanakya.core.coach import info_reply
from chanakya.core.contracts import Reply
from chanakya.skills.base import SkillContext

LEADS = re.compile(r"^(?:mera naam|mera nam|my name is|i am|i'm|main|naam|name|मेरा नाम|माझं नाव|माझे नाव|मी|नाव)\s*[:\-]?\s*",
                   re.I)
TAILS = re.compile(r"\s*(?:hai|hoon|hu|है|हूँ|हूं|आहे)\.?$", re.I)


def clean_name(text: str) -> str | None:
    """'mera naam Amit hai' → 'Amit'. A real name: letters only (any script), 1–3 words, at most 30 characters."""
    t = " ".join((text or "").strip().split())
    t = LEADS.sub("", t)
    t = TAILS.sub("", t).strip(" .,!-")
    if not t or len(t) > 30 or len(t.split()) > 3 or re.search(r"[\d@#/_:*]", t):
        return None
    if not all(ch.isalpha() or ch in " .'-" or 0x0900 <= ord(ch) <= 0x097F for ch in t):
        return None
    return " ".join(w[:1].upper() + w[1:] if w.isascii() else w for w in t.split())


def ask(ctx: SkillContext) -> Reply:
    return ctx.reply(info_reply(ctx, ctx.t("ask_staff_name")))


async def capture(ctx: SkillContext) -> Reply:
    """Waiting for the name: a typed (or spoken) name is saved; anything else asks again, kindly."""
    from chanakya.ai.gemini import AIUnavailable

    msg = ctx.inbound
    text = msg.text if msg.type == "text" else None
    if msg.type == "audio" and msg.media_id:
        try:
            audio, mime = await ctx.svc.channel.download_media(msg.media_id)
            text = await ctx.ai.transcribe(audio, mime or msg.mime_type)
        except AIUnavailable:
            return ctx.reply(info_reply(ctx, ctx.t("ask_staff_name_type")))
    name = clean_name(text or "")
    if not name:
        return ctx.reply(info_reply(ctx, ctx.t("ask_staff_name_again")))
    return await _save(ctx, name, first=True)


async def change(ctx: SkillContext, arg: str) -> Reply:
    name = clean_name(arg)
    if not name:
        return ctx.reply(info_reply(ctx, ctx.t("ask_staff_name_again")))
    return await _save(ctx, name, first=not ctx.staff.name_set)  # the first answer may come as 'mera naam …'


async def _save(ctx: SkillContext, name: str, first: bool) -> Reply:
    from chanakya.skills import brief

    st = ctx.staff
    st.name, st.name_set = name, True
    ctx.ev("staff_name_set", first=first)
    if first:  # then the shift start, greeting them by their own name
        return await brief.brief(ctx)
    return ctx.reply(info_reply(ctx, ctx.t("staff_name_saved", name=ctx.reg.raw(name, "staff:name"))))
