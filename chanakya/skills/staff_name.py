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


# never a name: commands, greetings and filler (found on 3 Nov 2026: 'restart' was saved as a name)
NOT_NAMES = {
    "restart", "start", "start over", "shuru", "shuru se", "phir se", "reset", "new", "naya", "naya customer",
    "new customer", "help", "madad", "menu", "status", "options", "offers", "khatam", "done", "end", "exit", "band",
    "cancel", "stop", "language", "lang", "bhasha", "catalog", "score", "floor", "digest", "visit", "export", "sold",
    "switch", "brief", "hi", "hii", "hello", "hey", "helo", "namaste", "namaskar", "namaskaar", "ok", "okay", "k",
    "haan", "han", "ha", "haa", "ji", "yes", "no", "nahi", "na", "thanks", "thank you", "shukriya", "dhanyavad",
    "test", "testing", "chanakya", "kya", "what", "why", "who", "hmm", "accha", "acha", "theek", "theek hai",
    "नमस्ते", "नमस्कार", "हाँ", "हां", "नहीं", "ठीक", "ठीक है", "हो", "नाही", "मदद", "मदत", "मेनू", "भाषा",
}


def clean_name(text: str) -> str | None:
    """'mera naam Amit hai' → 'Amit'. A real name: letters only (any script), 1–3 words, at most 30 characters,
    and never a command, greeting or filler word."""
    t = " ".join((text or "").strip().split())
    t = LEADS.sub("", t)
    t = TAILS.sub("", t).strip(" .,!-")
    if not t or len(t) > 30 or len(t.split()) > 3 or re.search(r"[\d@#/_:*]", t):
        return None
    if t.lower() in NOT_NAMES:
        return None
    from chanakya.core.router import command_of

    if command_of(t)[0] is not None:
        return None
    if not all(ch.isalpha() or ch in " .'-" or 0x0900 <= ord(ch) <= 0x097F for ch in t):
        return None
    return " ".join(w[:1].upper() + w[1:] if w.isascii() else w for w in t.split())


def ask(ctx: SkillContext) -> Reply:
    ctx.staff.name_asked = True
    return ctx.reply(info_reply(ctx, ctx.t("ask_staff_name")))


def needs_name(st) -> bool:
    """No name yet, or a name that was really a command or greeting (saved by the 3 Nov bug): ask again."""
    if not st.name_set:
        return True
    if not st.name or clean_name(st.name) is None:
        st.name_set, st.name_asked = False, False
        return True
    return False


async def capture(ctx: SkillContext) -> Reply:
    """The name question comes first; only an answer to it is saved. A command, greeting or anything that is not
    a name asks again, kindly (a command typed now is not lost: the name is asked once, then everything works)."""
    from chanakya.ai.gemini import AIUnavailable

    if not ctx.staff.name_asked:  # never take a message as the name before the question was asked
        return ask(ctx)
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
    st.name, st.name_set, st.name_asked = name, True, True
    ctx.ev("staff_name_set", first=first)
    if first:  # then the shift start, greeting them by their own name
        return await brief.brief(ctx)
    return ctx.reply(info_reply(ctx, ctx.t("staff_name_saved", name=ctx.reg.raw(name, "staff:name"))))
