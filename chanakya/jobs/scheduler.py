"""Scheduler (spec 14.5): one asyncio loop, tick every 20 s — due tasks, install lifecycle, expert SLA,
daily digest at 21:00, media cleanup. Every action is idempotent (markers in timeline/payload)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from chanakya.core import clock, repo
from chanakya.core.buttons import encode
from chanakya.core.contracts import Button, OutMessage
from chanakya.core.events import ev
from chanakya.i18n.lang import t
from chanakya.log import get_log
from chanakya.truth.firewall import NumberRegistry

log = get_log("scheduler")


class Scheduler:
    def __init__(self, svc, interval: float = 20):
        self.svc = svc
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("tick_failed")
            await asyncio.sleep(self.interval)

    async def send(self, m: OutMessage, trusted: list[str] | None = None) -> None:
        reg = NumberRegistry()
        for s in [m.body or "", m.caption or ""] + (trusted or []):
            reg.static(s)
        await self.svc.send_checked(m, reg)

    async def tick(self, now: datetime | None = None) -> None:
        async with self._lock:
            now = now or clock.now()
            await self.due_tasks(now)
            await self.install_lifecycle(now)
            from chanakya.skills.expert import sla_tick

            await sla_tick(now, self.send)
            await self.daily_digest(now)
            await repo.media_cleanup(now)

    # ---------- tasks ----------
    async def due_tasks(self, now: datetime) -> None:
        for task in await repo.tasks_open():
            if task.kind == "approval":
                continue
            due = repo.aware(task.due_at)
            p = dict(task.payload or {})
            if due > now or p.get("reminded"):
                continue
            st = await repo.staff_by_wa(task.owner_wa_id)
            lang = st.lang if st else "hi"
            title = (task.title or {}).get(lang) or (task.title or {}).get("hi", "")
            btns = [
                Button(id=encode("s", p.get("code", "C0"), "parivaar"), title=t("b_send_customer", lang)),
                Button(id=encode("s", p.get("code", "C0"), "khatam"), title=t("b_done_task", lang)),
            ]
            await self.send(
                OutMessage(
                    to=task.owner_wa_id, kind="buttons", body=t("task_reminder", lang, title=title), buttons=btns
                )
            )
            p["reminded"] = now.isoformat()
            await repo.task_update(task.id, payload=p)

    # ---------- installation ----------
    async def install_lifecycle(self, now: datetime) -> None:
        for job in await repo.install_all(["created", "reminded", "due", "done"]):
            s = await self.svc.sessions.get(job.session_id)
            if s is None:
                continue
            tl = dict(job.timeline or {})
            needs = job.site_needs or {}
            _start_h, end_h = needs.get("_start_h", 10), needs.get("_end_h", 13)
            day = datetime(job.slot_date.year, job.slot_date.month, job.slot_date.day, tzinfo=clock.IST)
            d_minus_1 = day - timedelta(days=1) + timedelta(hours=18)
            slot_end = day + timedelta(hours=end_h)
            cust = s.customer_wa_id
            lang = s.home.customer_lang
            p = self.svc.facts.product_sync(job.sku)
            product = p.model if p else job.sku
            changed = False
            if job.status in ("created",) and now >= d_minus_1 and now < slot_end and "d1_sent" not in tl:
                if cust:
                    await self.send(
                        OutMessage(
                            to=cust,
                            kind="buttons",
                            body=t("cust_install_d1", lang, slot=needs.get("_slot_time", "")),
                            buttons=[
                                Button(id=encode("u", s.id[:8], "ready"), title=t("cb_ready", lang)),
                                Button(id=encode("u", s.id[:8], "change"), title=t("cb_change_day", lang)),
                            ],
                        )
                    )
                tl["d1_sent"] = now.isoformat()
                job.status = "reminded"
                changed = True
            if job.status in ("created", "reminded") and now >= slot_end and "check_sent" not in tl:
                if cust:
                    await self.send(
                        OutMessage(
                            to=cust,
                            kind="buttons",
                            body=t("cust_install_check", lang, product=product),
                            buttons=[
                                Button(id=encode("u", s.id[:8], "working"), title=t("cb_working", lang)),
                                Button(id=encode("u", s.id[:8], "problem"), title=t("cb_problem", lang)),
                            ],
                        ),
                        trusted=[product],
                    )
                tl["check_sent"] = now.isoformat()
                job.status = "due"
                changed = True
            if job.status == "done" and now >= slot_end + timedelta(days=7) and "d7_sent" not in tl:
                if cust:
                    await self.send(OutMessage(to=cust, kind="text", body=t("cust_d7", lang)))
                tl["d7_sent"] = now.isoformat()
                changed = True
                await repo.events_insert([ev("checkin_sent", session_id=s.id, job_id=job.id)], now)
            if changed:
                await repo.install_update(job.id, timeline=tl, status=job.status)

    # ---------- digest ----------
    async def daily_digest(self, now: datetime) -> None:
        if now.hour < 21:
            return
        key = f"digest_sent:{now.date().isoformat()}"
        if await repo.setting_get(key):
            return
        await repo.setting_set(key, True)
        from chanakya.skills import manager
        from chanakya.skills.base import SkillContext

        for m in await repo.staff_by_role("manager"):
            last = await repo.last_inbound_at(m.wa_id)
            if not last or (now - last) > timedelta(hours=24):
                continue
            from chanakya.core.contracts import InboundMessage

            inb = InboundMessage(
                wa_message_id="digest", from_wa_id=m.wa_id, timestamp=now, type="unsupported", received_at=now
            )
            ctx = SkillContext(
                actor_wa_id=m.wa_id,
                role="manager",
                staff=m,
                session=None,
                inbound=inb,
                facts=self.svc.facts,
                packs=self.svc.packs,
                ai=self.svc.ai,
                reg=NumberRegistry(),
                now=now,
                svc=self.svc,
            )
            r = await manager.digest(ctx)
            for msg in r.messages:
                await self.svc.deliver(self.svc.validate(msg, ctx.reg, None, m.lang or "en", None))
