"""Customer role: QR opt-in, Parivaar Card buttons, relay to the associate, installation confirmation.
Chanakya never auto-answers the customer with facts; the associate replies (prefix "r ")."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.core.buttons import ButtonId
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
    from chanakya.skills import follow

    s = await ctx.svc.sessions.by_token(token.strip())
    if s is None:
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_unknown_token", "hi")))
    ctx.session = s
    return await follow.record_consent(ctx, ctx.actor_wa_id, "qr")


async def button(ctx: SkillContext, bid: ButtonId) -> Reply:
    s = ctx.session
    action = bid.get(1)
    lang = CUSTOMER_PHONE_LANG
    alang = await _assoc_lang(ctx)
    a = s.associate_wa_id
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
    if action == "change":
        t = await repo.task_create(
            a,
            "callback",
            {lg: ctx.t("task_change_day", lg) for lg in ("hi", "mr", "en")},
            ctx.now,
            s.id,
            {"job_id": job.id},
        )
        ctx.ev("task_created", task_id=t.id, kind="callback", owner=a, due_at=ctx.now.isoformat())
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_change_ack", lang)),
            OutMessage(to=a, kind="text", body=ctx.t("cust_change_assoc", alang, name=_name(ctx))),
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


async def relay(ctx: SkillContext) -> Reply:
    """Customer free text / voice / photo → the associate."""
    s = ctx.session
    alang = await _assoc_lang(ctx)
    a = s.associate_wa_id
    inb = ctx.inbound
    if (inb.text or "").strip().upper() in ("STOP", "BAND", "बंद"):
        s.extra["opted_out"] = True
        s.consent = None
        s.customer_wa_id = None
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
    return ctx.reply(
        OutMessage(
            to=a,
            kind="text",
            body=ctx.t("cust_msg_assoc", alang, name=_name(ctx), text=text[:300]),
            allowed_numbers=ctx.reg.allowed(),
        )
    )


async def associate_reply(ctx: SkillContext, text: str) -> Reply:
    """Associate typed 'r <message>' → relayed to the linked customer (their words, their responsibility)."""
    s = ctx.session
    if not s or not s.customer_wa_id:
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("no_customer_linked")))
    ctx.reg.raw(text, "associate:relay")
    return ctx.reply(
        OutMessage(to=s.customer_wa_id, kind="text", body=text[:500], allowed_numbers=ctx.reg.allowed()),
        OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("relay_sent")),
    )
