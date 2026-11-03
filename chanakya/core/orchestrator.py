"""handle_inbound(): dedupe → ack → route → skill → validate → render → send → persist (spec 7.1)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from chanakya.app import diag
from chanakya.core import clock, fallback, locks, repo, router
from chanakya.core.contracts import Event, InboundMessage, OutMessage, Reply, Step
from chanakya.log import get_log
from chanakya.skills.base import SkillContext
from chanakya.truth.firewall import NumberRegistry

log = get_log("orchestrator")
AFTER_SALE_AWAIT = ("lost_reason", "voice_for_parivaar")  # answers still expected after the session closed


VOICE_KINDS = ("text", "buttons", "list", "image")


def voice_footer(ctx: SkillContext, m) -> None:
    """Voice first, at every step: every message to the associate (and the manager) says it can simply be
    answered by voice, in Hindi, English or Marathi, unless it already carries its own footer (such as what a
    voice note was understood as). Not on the customer's, expert's or visitor's phone."""
    if m.footer or m.kind not in VOICE_KINDS or m.to != ctx.actor_wa_id or ctx.role not in ("associate", "manager"):
        return
    if ctx.staff is None or not ctx.staff.lang_set:
        return  # the language picker comes first
    m.footer = ctx.t("footer_voice_mgr" if ctx.role == "manager" else "footer_voice_q")


class Orchestrator:
    def __init__(self, svc, scheduler=None):
        self.svc = svc
        self.scheduler = scheduler
        self._tasks: set[asyncio.Task] = set()
        svc.orchestrator = self

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def drain(self, timeout: float = 30) -> None:
        """Wait for background work (tests/simulator)."""
        end = time.monotonic() + timeout
        while self._tasks and time.monotonic() < end:
            await asyncio.wait(list(self._tasks), timeout=max(0.01, end - time.monotonic()))

    async def handle_inbound(self, msg: InboundMessage) -> None:
        t0 = msg.received_at
        if not await repo.message_insert(
            msg.wa_message_id, "in", msg.from_wa_id, msg.type, msg.model_dump(mode="json", exclude={"received_at"})
        ):
            log.info("duplicate", wamid=msg.wa_message_id)
            return
        self.spawn(self.svc.channel.mark_read_typing(msg.wa_message_id))
        async with locks.for_(msg.from_wa_id):
            ctx = None
            try:
                ctx = await router.build_context(msg, self.svc)
                w = msg.from_wa_id
                diag.mark(
                    "last_inbound",
                    {"role": ctx.role, "type": msg.type, "from": f"{w[:2]}{'*' * max(0, len(w) - 4)}{w[-2:]}"},
                )
                if msg.type == "audio" and ctx.role != "customer" and not msg.wa_message_id.endswith(":retry"):
                    try:
                        await self.svc.channel.send(OutMessage(to=msg.from_wa_id, kind="reaction",
                                                               reaction_to=msg.wa_message_id, emoji="👂"))
                    except Exception:
                        log.warning("reaction_failed")
                reply = await router.dispatch(ctx)
            except Exception:
                log.exception("skill_failed", wa_id=msg.from_wa_id, type=msg.type)
                lang = ctx.lang if ctx else "hi"
                reply = Reply(messages=[fallback.safe_reply(msg.from_wa_id, lang, ctx.session if ctx else None)])
                if ctx is None:
                    for m in reply.messages:
                        await self.svc.deliver(m)
                    return
            await self.finish(ctx, reply, t0)

    async def finish(self, ctx: SkillContext, reply: Reply, t0: datetime | None) -> None:
        first = True
        home = ctx.session.home if ctx.session else None
        for m in reply.messages:
            if m is None:
                continue
            try:
                lang = ctx.lang
                if ctx.session and m.to == ctx.actor_wa_id and m.session_id is None:
                    m.session_id = ctx.session.id
                if ctx.session and ctx.role == "associate":
                    ctx.session.extra["last_touch"] = ctx.now.isoformat()
                voice_footer(ctx, m)
                out = self.svc.validate(m, ctx.reg, home, lang, ctx.asking, ctx.session)
                await self.svc.deliver(out)
                if first and t0 is not None and m.to == ctx.actor_wa_id:
                    ms = int((datetime.now(UTC) - t0.astimezone(UTC)).total_seconds() * 1000)
                    ctx.events.append(
                        Event(
                            type="latency",
                            session_id=ctx.session.id if ctx.session else None,
                            actor_wa_id=ctx.actor_wa_id,
                            payload={"phase": "first_reply", "ms": ms},
                            latency_ms=ms,
                        )
                    )
                    first = False
            except Exception:
                log.exception("deliver_loop_failed")
        seen = {id(e) for e in ctx.events}
        events = list(ctx.events) + [e for e in reply.events if id(e) not in seen]
        for v in self.svc.violations:
            events.append(
                Event(
                    type="validator_violation",
                    session_id=ctx.session.id if ctx.session else None,
                    payload={"kind": v.kind, "detail": v.detail[:200]},
                )
            )
        self.svc.violations.clear()
        try:
            await repo.events_insert(events, ctx.now)
            if ctx.session:
                await self.svc.sessions.save(ctx.session, ctx.now)
                if ctx.session.step == Step.CLOSED and ctx.staff and ctx.session.awaiting not in AFTER_SALE_AWAIT:
                    ctx.staff.current_session_id = None  # (kept while an after-sale answer is awaited)
            if ctx.staff:
                await repo.staff_update(
                    ctx.staff.wa_id,
                    lang=ctx.staff.lang,
                    role=ctx.staff.role,
                    base_role=ctx.staff.base_role,
                    current_session_id=ctx.staff.current_session_id,
                    streak=ctx.staff.streak,
                    lang_set=ctx.staff.lang_set,
                    name=ctx.staff.name,
                    name_set=ctx.staff.name_set,
                    name_asked=ctx.staff.name_asked,
                    last_brief_date=ctx.staff.last_brief_date,
                )
        except Exception:
            log.exception("persist_failed")
        from chanakya.app import memory

        wid = ctx.inbound.wa_message_id if ctx.inbound else ""
        step = ctx.session.step if ctx.session else None
        log.info("handled", what=wid.split(":")[1] if wid.startswith("job:") else (ctx.inbound.type if ctx.inbound else None),
                 step=getattr(step, "value", step), out=len(reply.messages),
                 mem_mb=memory.used_mb())  # one line per handled message: what ran, and memory after it
        for job in reply.background:
            if ctx.session:
                self.spawn(self.run_job(job, ctx.session.id, ctx.session.associate_wa_id))

    async def run_job(self, job: str, session_id: str, associate_wa_id: str) -> None:
        from chanakya.skills import tryon_flow

        async with locks.for_(associate_wa_id):
            staff = None
            try:
                staff = await repo.staff_by_wa(associate_wa_id)
                session = await self.svc.sessions.get(session_id)
                if session is None:
                    return
                now = clock.now()
                msg = InboundMessage(
                    wa_message_id=f"job:{job}:{time.time()}",
                    from_wa_id=associate_wa_id,
                    timestamp=now,
                    type="unsupported",
                    received_at=now,
                )
                ctx = SkillContext(
                    actor_wa_id=associate_wa_id,
                    role="associate",
                    staff=staff,
                    session=session,
                    inbound=msg,
                    facts=self.svc.facts,
                    packs=self.svc.packs,
                    ai=self.svc.ai,
                    reg=NumberRegistry(),
                    now=now,
                    svc=self.svc,
                )
                if job == "tryon":
                    reply = await asyncio.wait_for(tryon_flow.job(ctx), timeout=30)
                elif job == "room_read":
                    reply = await asyncio.wait_for(tryon_flow.prefetch(ctx), timeout=25)
                else:
                    return
            except Exception:
                log.exception("job_failed", job=job)
                try:
                    from chanakya.i18n.lang import t

                    await self.svc.deliver(OutMessage(to=associate_wa_id, kind="text", body=t("tryon_failed", (staff.lang if staff and staff.lang else "en"))))
                except Exception:
                    pass
                return
            await self.finish(ctx, reply, None)
