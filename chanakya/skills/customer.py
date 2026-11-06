"""Customer role: QR opt-in, Parivaar Card buttons, relay to the associate, installation confirmation.
Chanakya never auto-answers the customer with facts; the associate replies (prefix "r ")."""

from __future__ import annotations

import re

from chanakya.core import repo
from chanakya.core.buttons import ButtonId, encode
from chanakya.core.contracts import CUSTOMER_PHONE_LANG, Button, OutMessage, Reply
from chanakya.core.status import short_name
from chanakya.skills.base import SkillContext


def _name(ctx: SkillContext) -> str:
    n = ctx.session.home.customer_name if ctx.session else None
    return ctx.reg.raw(short_name(n) if n else ctx.t("customer_word"), "card:name")


async def _assoc_lang(ctx: SkillContext) -> str:
    st = await repo.staff_by_wa(ctx.session.associate_wa_id)
    return st.lang if st else "hi"


async def optin(ctx: SkillContext, token: str) -> Reply:
    """The customer sent the QR's code. One code, one customer (6 Nov 2026): the first number to send it is this
    session's customer; another number is refused (and the associate told); a code nobody used in 48 hours has
    expired; the same number sending it again while connected changes nothing (no second welcome)."""
    from chanakya.skills import follow

    s = await ctx.svc.sessions.by_token(token.strip())
    me = ctx.actor_wa_id
    if s is None:
        return ctx.reply(OutMessage(to=me, kind="text", body=ctx.t("cust_unknown_token", CUSTOMER_PHONE_LANG)))
    bound = s.extra.get("optin_wa_id") or s.customer_wa_id
    if bound and bound != me:
        ctx.ev("optin_refused", session_code=s.code, reason="taken")
        alang = (st.lang if (st := await repo.staff_by_wa(s.associate_wa_id)) else None) or "hi"
        return ctx.reply(
            OutMessage(to=me, kind="text", body=ctx.t("cust_optin_taken", CUSTOMER_PHONE_LANG)),
            OutMessage(to=s.associate_wa_id, kind="text",
                       body=ctx.t("optin_taken_assoc", alang, code=ctx.reg.raw(s.code, "session:code"))),
        )
    if bound is None and follow.token_expired(s, ctx.now):
        ctx.ev("optin_refused", session_code=s.code, reason="expired")
        return ctx.reply(OutMessage(to=me, kind="text", body=ctx.t("cust_optin_expired", CUSTOMER_PHONE_LANG)))
    ctx.session = s
    if s.customer_wa_id == me and s.consent:  # already connected: a second send is not a second welcome
        ctx.ev("optin_repeat")
        return ctx.reply()
    return await follow.record_consent(ctx, me, "qr")


async def button(ctx: SkillContext, bid: ButtonId) -> Reply:
    s = ctx.session
    action = bid.get(1)
    lang = CUSTOMER_PHONE_LANG
    alang = await _assoc_lang(ctx)
    a = s.associate_wa_id
    if action in ("talk", "visit", "ask"):
        ctx.ev("family_replied", associate=a, how=action)  # the family answered the Family Card (associate's wins)
    if action == "talk":
        return ctx.reply(
            OutMessage(
                to=a,
                kind="buttons",
                body=ctx.t("cust_wants_talk", alang, name=_name(ctx)),
                buttons=[Button(id=ctx.sid("called"), title=ctx.t("b_called", alang))],
            ),
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_talk_ack", lang)),
        )
    if action == "visit":
        due = ctx.now.replace(hour=11, minute=0, second=0, microsecond=0)
        from datetime import timedelta

        due = due + timedelta(days=1)
        name = s.home.customer_name or s.code
        t = await repo.task_create(
            a,
            "callback",
            {lg: ctx.t("task_visit_title", lg, name=name) for lg in ("hi", "mr", "en")},
            due,
            s.id,
            {"code": s.code},
        )
        ctx.ev("task_created", task_id=t.id, kind="callback", owner=a, due_at=due.isoformat())
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_visit_ack", lang)),
            OutMessage(to=a, kind="text", body=ctx.t("cust_visit_assoc", alang, name=_name(ctx))),
        )
    if action == "ask":
        s.extra["customer_asking"] = True
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_ask_prompt", lang)))
    if action in ("ready", "change", "working", "problem"):
        return await install_reply(ctx, action)
    return ctx.reply()


async def install_reply(ctx: SkillContext, action: str) -> Reply:
    s = ctx.session
    job_id = s.extra.get("install_job_id")
    job = await repo.install_get(job_id) if job_id else None
    lang = CUSTOMER_PHONE_LANG
    alang = await _assoc_lang(ctx)
    a = s.associate_wa_id
    if job is None:
        return ctx.reply()
    p = ctx.facts.product_sync(job.sku)
    product = ctx.reg.raw(p.model if p else job.sku, f"product:{job.sku}")
    tl = dict(job.timeline or {})
    if action == "ready":
        tl["ready_at"] = ctx.now.isoformat()
        await repo.install_update(job.id, timeline=tl)
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_ready_ack", lang)))
    if action == "change":  # pause the job (no 'is it done?' for the old slot) and let the associate re-book
        await repo.install_update(job.id, status="paused", timeline=tl)
        s.extra["install_paused"] = True
        await ctx.svc.sessions.save(s, ctx.now)
        ctx.ev("install_change_requested", job_id=job.id)
        from chanakya.skills import follow

        with ctx.as_lang(alang):
            prev, ctx.session = ctx.session, s
            slots = follow.slot_choices(ctx)
            btns = [Button(id=encode("s", s.code, "islot", sid), title=label[:20]) for sid, (label, *_r) in slots.items()]
            body = ctx.t("cust_change_rebook", alang, name=_name(ctx))
            ctx.session = prev
        for b in btns:
            ctx.reg.static(b.title)
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_change_ack", lang)),
            OutMessage(to=a, kind="buttons", body=body, buttons=btns, session_id=s.id),
        )
    if action == "working":
        tl["done_at"] = ctx.now.isoformat()
        await repo.install_update(job.id, status="done", timeline=tl)
        ctx.ev("install_confirmed", job_id=job.id)
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_working_ack", lang)),
            OutMessage(to=a, kind="text", body=ctx.t("installed_ok", alang, name=_name(ctx), product=product)),
        )
    tl["problem_at"] = ctx.now.isoformat()
    await repo.install_update(job.id, status="problem", timeline=tl)
    ctx.ev("install_problem", job_id=job.id)
    staff = await repo.staff_by_wa(a)
    msgs = [
        OutMessage(
            to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_problem_ack", lang, associate=staff.name if staff else "")
        ),
        OutMessage(to=a, kind="text", body=ctx.t("install_problem_assoc", alang, name=_name(ctx), product=product)),
    ]
    site = ", ".join(f"{k}: {v}" for k, v in (job.site_needs or {}).items() if not k.startswith("_"))
    for m in await repo.staff_by_role("manager"):
        msgs.append(
            OutMessage(
                to=m.wa_id,
                kind="text",
                body=ctx.t(
                    "install_problem_manager",
                    m.lang or "en",
                    id=ctx.reg.raw(str(job.id), "job:id"),
                    name=_name(ctx),
                    site=ctx.reg.static(site),
                ),
            )
        )
    return ctx.reply(*msgs)


# The whole message is one of these (after case and punctuation are dropped): a request to stop on its own.
STOP_WORDS = {
    "stop", "stop it", "stop now", "stop please", "please stop", "pls stop", "stop pls", "unsubscribe", "optout",
    "opt out", "band", "band karo", "band kijiye", "band kariye", "band kar do", "band karein", "ab band karo",
    "mat bhejo", "mat bhejna", "ab mat bhejo", "stop karo", "बंद", "बंद करो", "बंद कीजिए", "बंद कर दो", "मत भेजो",
    "मत भेजिए", "अब मत भेजो", "थांबा", "बंद करा", "नको पाठवू", "पाठवू नका", "नका पाठवू", "मेसेज नको",
}
# Anywhere in a short message: phrases that can only be about the messages themselves.
STOP_PHRASES = re.compile(
    r"\bunsubscribe\b|\bopt[\s-]?out\b"
    r"|\b(?:don'?t|do not|dont|never)\s+(?:message|msg|text|contact|whatsapp)\b"
    r"|\b(?:don'?t|do not|dont)\s+send\s+(?:me\s+)?(?:any\s+)?(?:more\s+)?(?:messages|msgs|texts)\b"
    r"|\bstop\s+(?:messaging|msging|sending|texting|whatsapping|contacting)\b|\bno more (?:messages|msgs|texts)\b"
    r"|\b(?:message|messages|msg|msgs)\s+(?:band|mat)\b"
    r"|(?:मैसेज|मेसेज|संदेश)\s*(?:मत|बंद|नको|पाठवू\s*नका)",
    re.IGNORECASE,
)


def wants_stop(text: str | None) -> bool:
    """The customer asks not to be messaged (6 Nov 2026: 'Stop.', 'STOP please', 'unsubscribe', 'don't message me',
    'band karo', 'मत भेजो', 'नको पाठवू'), in a short message of its own. Ordinary chat that only mentions the word
    ('bus stop ke paas', 'TV band ho gaya', 'delivery kal mat bhejo') is relayed as usual."""
    t = " ".join(re.sub(r"[^\w\s'ऀ-ॿ-]+", " ", (text or "").lower().replace("’", "'")).split())
    if not t or len(t.split()) > 8:
        return False
    if t in STOP_WORDS:
        return True
    words = t.split()
    if words[0] == "stop" and len(words) <= 3:  # 'stop', 'stop it please'
        return True
    return bool(STOP_PHRASES.search(t))


async def relay(ctx: SkillContext) -> Reply:
    """Customer free text / voice / photo → the associate."""
    s = ctx.session
    alang = await _assoc_lang(ctx)
    a = s.associate_wa_id
    inb = ctx.inbound
    await repo.setting_set(f"last_customer_session:{a}", s.id)  # the associate's 'r …' goes back to this customer
    if inb.type == "text" and wants_stop(inb.text):
        s.extra["opted_out"] = True
        s.extra.setdefault("optin_wa_id", ctx.actor_wa_id)  # their code stays theirs: no one else can take it
        s.consent = None
        s.customer_wa_id = None
        await repo.setting_set(f"opted_out:{ctx.actor_wa_id}", ctx.now.isoformat())  # on every session, until they opt in
        ctx.ev("customer_opted_out")
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_stopped", CUSTOMER_PHONE_LANG)),
            OutMessage(to=a, kind="text", body=ctx.t("cust_stopped_assoc", alang, name=_name(ctx))),
        )
    if inb.type == "image":
        from chanakya.skills import tryon_flow

        return await tryon_flow.customer_photo(ctx)
    if inb.type == "audio" and inb.media_id:
        data, mime = await ctx.svc.channel.download_media(inb.media_id)
        mid = await ctx.svc.channel.upload_media(data, mime.split(";")[0] or "audio/ogg", "customer.ogg")
        return ctx.reply(
            OutMessage(to=a, kind="text", body=ctx.t("cust_voice_assoc", alang, name=_name(ctx))),
            OutMessage(to=a, kind="audio", media_id=mid, voice=True),
        )
    text = (inb.text or "").strip()
    ctx.reg.raw(text, "customer:text")  # the customer's own words are relayed as-is
    s.extra.pop("customer_asking", None)
    if s.extra.get("parivaar_sent"):
        ctx.ev("family_replied", associate=a, how="text")
    body = ctx.reg.raw(f"[{s.code}]", "session:code") + " " + ctx.t("cust_msg_assoc", alang, name=_name(ctx), text=text[:300])
    last = s.extra.get("last_touch")
    if last:
        from datetime import datetime

        from chanakya.skills import returning

        seen = datetime.fromisoformat(last)
        if (ctx.now - seen).total_seconds() >= 6 * 3600:  # coming back after a while: what happened last time
            with ctx.as_lang(alang):
                line, _ = returning.summary(ctx, seen, s)
            head, _, rest = body.partition("\n")  # 3 lines: who wrote, with what happened last time; the message; the reply hint
            body = head + " — " + ctx.t("ret_msg_prefix", alang, line=line) + ("\n" + rest if rest else "")
    return ctx.reply(OutMessage(to=a, kind="text", body=body, allowed_numbers=ctx.reg.allowed()))


async def associate_reply(ctx: SkillContext, text: str) -> Reply:
    """Associate typed 'r <message>' (or 'r C2 <message>') → relayed to that customer: by default the one who
    wrote last, even after the sale; never to whoever happens to be the current customer."""
    import re as _re

    s = None
    m = _re.match(r"^(C\d{1,3})\s+(.+)$", text.strip(), _re.S)
    if m:
        s = await ctx.svc.sessions.by_code(ctx.actor_wa_id, m.group(1))
        text = m.group(2) if s else text
    if s is None:
        sid = await repo.setting_get(f"last_customer_session:{ctx.actor_wa_id}")
        s = await ctx.svc.sessions.get(sid) if sid else None
    s = s or ctx.session
    if not s or not s.customer_wa_id:
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("no_customer_linked")))
    ctx.reg.raw(text, "associate:relay")
    out = OutMessage(to=s.customer_wa_id, kind="text", body=text[:500], allowed_numbers=ctx.reg.allowed())
    from chanakya.core import validator

    # checked before 'sent' is said (6 Nov 2026: a blocked relay sent the staff fallback to the customer, and the
    # associate was told it went): blocked → nothing to the customer, the associate is told why, in their language
    chk = validator.check(out, ctx.reg, None, CUSTOMER_PHONE_LANG, ctx.packs.banned)
    if chk.fatal:
        kinds = {v.kind for v in chk.violations}
        ctx.ev("relay_blocked", why=sorted(kinds)[:4])
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text",
                                    body=ctx.t("relay_blocked" if "banned_phrase" in kinds else "relay_blocked_other")))
    return ctx.reply(out, OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("relay_sent")))
