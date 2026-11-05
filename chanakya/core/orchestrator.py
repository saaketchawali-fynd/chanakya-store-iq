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


async def voice_footer(ctx: SkillContext, m) -> None:
    """Voice first, at every step: every message to an associate or the manager says, in its grey footer, that it
    can simply be answered by voice, and for the associate in front of a customer where that customer stands
    ('Bolkar bhi · C1 Shah · TV · Ghar 4/4'), unless it already carries its own footer (such as what a voice note
    was understood as). A message to another staff phone (the manager's tip, an approval) gets it in that
    person's language. Not on the customer's, expert's or visitor's phone."""
    if m.footer or m.kind not in VOICE_KINDS:
        return
    if m.to != ctx.actor_wa_id:
        other = await repo.staff_by_wa(m.to)
        if other is not None and other.role in ("associate", "manager") and other.lang_set:
            m.footer = ctx.t("footer_voice_mgr" if other.role == "manager" else "footer_voice_q", other.lang or "en")
        return
    if ctx.role not in ("associate", "manager") or ctx.staff is None or not ctx.staff.lang_set:
        return  # the language picker comes first
    if ctx.role == "associate" and ctx.session is not None:
        from chanakya.core.coach import status_footer

        m.footer = status_footer(ctx)
        m.allowed_numbers = sorted(set(m.allowed_numbers or []) | set(ctx.reg.allowed()))
    m.footer = m.footer or ctx.t("footer_voice_mgr" if ctx.role == "manager" else "footer_voice_q")


class Orchestrator:
    def __init__(self, svc, scheduler=None):
        self.svc = svc
        self.scheduler = scheduler
        self._tasks: set[asyncio.Task] = set()
        svc.orchestrator = self
        self._first: dict[str, dict] = {}  # inbound message id → {ctx, done, hold_ms}: the first-reply watchdog

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
        holder: dict = {"ctx": None, "done": asyncio.Event(), "hold_ms": None}
        self._first[msg.wa_message_id] = holder
        self.spawn(self._first_reply_watchdog(msg, holder, t0))
        try:
            await self._handle(msg, holder, t0)
        finally:
            holder["done"].set()
            self._first.pop(msg.wa_message_id, None)

    async def _first_reply_watchdog(self, msg: InboundMessage, holder: dict, t0: datetime | None) -> None:
        """The contest's floor rule: the first reply in under 5 seconds. When nothing has gone back by
        `first_reply_hold_s` (3.5 s: a voice note being heard, a label being read, a card being drawn), one short line
        goes first ('Sun liya — ek second…'); the full reply follows. Staff phones only."""
        wait = float(getattr(self.svc.settings, "first_reply_hold_s", 3.5))
        try:
            await asyncio.wait_for(holder["done"].wait(), timeout=wait)
            return
        except TimeoutError:
            pass
        ctx = holder["ctx"]
        if ctx is None or ctx.role not in ("associate", "manager", "expert", "visitor") or ctx.staff is None \
                or not ctx.staff.lang_set or holder["done"].is_set():
            return
        key = {"audio": "hold_voice", "image": "hold_photo"}.get(msg.type, "hold_wait")
        try:
            await self.svc.deliver(OutMessage(to=msg.from_wa_id, kind="text", body=ctx.t(key)))
        except Exception:
            log.warning("hold_failed")
            return
        ms = int((datetime.now(UTC) - t0.astimezone(UTC)).total_seconds() * 1000) if t0 else None
        holder["hold_ms"] = ms
        diag.mark(f"reply_ms_{msg.type}", {"ms": ms, "hold": True})
        ctx.events.append(Event(type="latency", session_id=ctx.session.id if ctx.session else None,
                                actor_wa_id=ctx.actor_wa_id, payload={"phase": "first_reply", "ms": ms, "hold": True},
                                latency_ms=ms))

    async def _handle(self, msg: InboundMessage, holder: dict, t0: datetime | None) -> None:
        async with locks.for_(msg.from_wa_id):
            ctx = None
            try:
                ctx = await router.build_context(msg, self.svc)
                holder["ctx"] = ctx
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
        holder = self._first.get(ctx.inbound.wa_message_id) if ctx.inbound else None
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
                await voice_footer(ctx, m)
                out = self.svc.validate(m, ctx.reg, home, lang, ctx.asking, ctx.session)
                if holder and first and m.to == ctx.actor_wa_id and out.card is None:
                    holder["done"].set()  # a plain message is on its way now: no holding line
                await self.svc.deliver(out)
                if holder and m.to == ctx.actor_wa_id:
                    holder["done"].set()
                if first and t0 is not None and m.to == ctx.actor_wa_id:
                    ms = int((datetime.now(UTC) - t0.astimezone(UTC)).total_seconds() * 1000)
                    kind = ctx.inbound.type if ctx.inbound else "?"
                    held = holder["hold_ms"] if holder else None
                    diag.mark(f"full_reply_ms_{kind}" if held else f"reply_ms_{kind}",
                              {"ms": ms, "step": ctx.session.step.value if ctx.session else None})
                    ctx.events.append(
                        Event(
                            type="first_reply_hold" if held else "latency",
                            session_id=ctx.session.id if ctx.session else None,
                            actor_wa_id=ctx.actor_wa_id,
                            payload={"phase": "full_reply" if held else "first_reply", "ms": ms},
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
