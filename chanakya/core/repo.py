"""Small DB helpers for staff, messages, events, tasks, install jobs, expert questions, library,
scores, visit logs, media and app settings."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from chanakya.core.clock import IST
from chanakya.core.contracts import Event
from chanakya.truth import db
from chanakya.truth.models import (
    AppSetting,
    EventRow,
    ExpertQuestion,
    InstallJob,
    LibraryEntry,
    Media,
    MessageRow,
    Score,
    Staff,
    Task,
    VisitLog,
)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


# ---------- staff ----------
async def staff_by_wa(wa_id: str) -> Staff | None:
    async with db.session() as s:
        return (await s.execute(select(Staff).where(Staff.wa_id == wa_id))).scalar_one_or_none()


async def staff_all() -> list[Staff]:
    async with db.session() as s:
        return list((await s.execute(select(Staff))).scalars())


async def staff_update(wa_id: str, **fields: Any) -> None:
    async with db.session() as s:
        row = (await s.execute(select(Staff).where(Staff.wa_id == wa_id))).scalar_one_or_none()
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()


async def staff_by_role(role: str) -> list[Staff]:
    async with db.session() as s:
        return list((await s.execute(select(Staff).where(Staff.role == role))).scalars())


# ---------- messages (dedupe) ----------
async def message_insert(wa_message_id: str, direction: str, wa_id: str, type_: str, payload: dict) -> bool:
    """Insert; returns False if the wamid already exists (dedupe via unique index)."""
    async with db.session() as s:
        s.add(MessageRow(wa_message_id=wa_message_id, direction=direction, wa_id=wa_id, type=type_, payload=payload))
        try:
            await s.commit()
            return True
        except IntegrityError:
            await s.rollback()
            return False


async def message_session(wa_message_id: str | None) -> str | None:
    """The customer session an outgoing message (with buttons) belonged to."""
    if not wa_message_id:
        return None
    async with db.session() as s:
        r = (await s.execute(select(MessageRow).where(MessageRow.wa_message_id == wa_message_id))).scalar_one_or_none()
        return (r.payload or {}).get("session_id") if r and r.direction == "out" else None


async def last_inbound_at(wa_id: str) -> datetime | None:
    async with db.session() as s:
        r = (
            await s.execute(
                select(MessageRow)
                .where(MessageRow.wa_id == wa_id, MessageRow.direction == "in")
                .order_by(MessageRow.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return aware(r.created_at) if r else None


# ---------- events ----------
async def events_insert(events: list[Event], at: datetime) -> None:
    if not events:
        return
    async with db.session() as s:
        for e in events:
            s.add(
                EventRow(
                    session_id=e.session_id,
                    actor_wa_id=e.actor_wa_id,
                    type=e.type,
                    step=e.step,
                    payload=e.payload,
                    sources=e.sources,
                    latency_ms=e.latency_ms,
                    at=at,
                )
            )
        await s.commit()


async def events_for(
    session_id: str | None = None, types: list[str] | None = None, since: datetime | None = None,
    until: datetime | None = None,
) -> list[EventRow]:
    async with db.session() as s:
        q = select(EventRow)
        if session_id:
            q = q.where(EventRow.session_id == session_id)
        if types:
            q = q.where(EventRow.type.in_(types))
        q = q.order_by(EventRow.id)
        rows = list((await s.execute(q)).scalars())
    if since:
        rows = [r for r in rows if (aware(r.at) or since) >= since]
    if until:  # one day's events: [since, until)
        rows = [r for r in rows if (aware(r.at) or since or until) < until]
    return rows


async def event_get(event_id: int) -> EventRow | None:
    async with db.session() as s:
        return await s.get(EventRow, event_id)


# ---------- tasks ----------
async def task_create(
    owner: str, kind: str, title: dict, due_at: datetime, session_id: str | None = None, payload: dict | None = None
) -> Task:
    async with db.session() as s:
        t = Task(
            owner_wa_id=owner,
            kind=kind,
            title=title,
            due_at=due_at,
            session_id=session_id,
            payload=payload or {},
            status="open",
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return t


async def tasks_open(owner: str | None = None, kind: str | None = None) -> list[Task]:
    async with db.session() as s:
        q = select(Task).where(Task.status == "open")
        if owner:
            q = q.where(Task.owner_wa_id == owner)
        if kind:
            q = q.where(Task.kind == kind)
        return list((await s.execute(q.order_by(Task.due_at))).scalars())


async def task_get(task_id: int) -> Task | None:
    async with db.session() as s:
        return await s.get(Task, task_id)


async def task_update(task_id: int, **fields: Any) -> None:
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t:
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()


# ---------- install jobs ----------
async def install_create(**fields: Any) -> InstallJob:
    async with db.session() as s:
        j = InstallJob(**fields)
        s.add(j)
        await s.commit()
        await s.refresh(j)
        return j


async def install_get(job_id: int) -> InstallJob | None:
    async with db.session() as s:
        return await s.get(InstallJob, job_id)


async def install_all(status_in: list[str] | None = None) -> list[InstallJob]:
    async with db.session() as s:
        q = select(InstallJob)
        if status_in:
            q = q.where(InstallJob.status.in_(status_in))
        return list((await s.execute(q.order_by(InstallJob.id))).scalars())


async def install_update(job_id: int, **fields: Any) -> None:
    async with db.session() as s:
        j = await s.get(InstallJob, job_id)
        if j:
            for k, v in fields.items():
                setattr(j, k, v)
            await s.commit()


# ---------- expert ----------
async def expert_q_create(**fields: Any) -> ExpertQuestion:
    async with db.session() as s:
        q = ExpertQuestion(**fields)
        s.add(q)
        await s.commit()
        await s.refresh(q)
        return q


async def expert_q_get(qid: int) -> ExpertQuestion | None:
    async with db.session() as s:
        return await s.get(ExpertQuestion, qid)


async def expert_q_open(expert_wa_id: str | None = None) -> list[ExpertQuestion]:
    async with db.session() as s:
        q = select(ExpertQuestion).where(ExpertQuestion.status == "open")
        if expert_wa_id:
            q = q.where(ExpertQuestion.expert_wa_id == expert_wa_id)
        return list((await s.execute(q.order_by(ExpertQuestion.id))).scalars())


async def expert_q_update(qid: int, **fields: Any) -> None:
    async with db.session() as s:
        q = await s.get(ExpertQuestion, qid)
        if q:
            for k, v in fields.items():
                setattr(q, k, v)
            await s.commit()


async def library_add(**fields: Any) -> LibraryEntry:
    async with db.session() as s:
        e = LibraryEntry(**fields)
        s.add(e)
        await s.commit()
        await s.refresh(e)
        return e


async def library_all(category: str | None = None) -> list[LibraryEntry]:
    async with db.session() as s:
        q = select(LibraryEntry).where(LibraryEntry.approved.is_(True))
        if category:
            q = q.where((LibraryEntry.category == category) | (LibraryEntry.category.is_(None)))
        return list((await s.execute(q.order_by(LibraryEntry.id.desc()).limit(30))).scalars())


async def library_get(lid: int) -> LibraryEntry | None:
    async with db.session() as s:
        return await s.get(LibraryEntry, lid)


# ---------- scores ----------
async def score_add(session_id: str, associate: str, steps: dict, total: int, at: datetime) -> None:
    async with db.session() as s:
        s.add(Score(session_id=session_id, associate_wa_id=associate, steps=steps, total=total, at=at))
        await s.commit()


async def scores_for(associate: str | None = None, limit: int = 50) -> list[Score]:
    async with db.session() as s:
        q = select(Score)
        if associate:
            q = q.where(Score.associate_wa_id == associate)
        return list((await s.execute(q.order_by(Score.id.desc()).limit(limit))).scalars())


# ---------- visit logs ----------
async def visit_draft(visitor: str) -> VisitLog | None:
    async with db.session() as s:
        return (
            await s.execute(
                select(VisitLog)
                .where(VisitLog.visitor_wa_id == visitor, VisitLog.status == "draft")
                .order_by(VisitLog.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def visit_save(log_id: int | None, visitor: str, entry: dict, status: str = "draft") -> VisitLog:
    async with db.session() as s:
        v = await s.get(VisitLog, log_id) if log_id else None
        if v is None:
            v = VisitLog(visitor_wa_id=visitor, entry=entry, status=status)
            s.add(v)
        else:
            v.entry = entry
            v.status = status
        await s.commit()
        await s.refresh(v)
        return v


async def visits_all(final_only: bool = True) -> list[VisitLog]:
    async with db.session() as s:
        q = select(VisitLog)
        if final_only:
            q = q.where(VisitLog.status == "final")
        return list((await s.execute(q.order_by(VisitLog.id))).scalars())


# ---------- media ----------
async def media_put(session_id: str | None, kind: str, data: bytes, mime: str, now: datetime, days: int = 7) -> int:
    async with db.session() as s:
        m = Media(session_id=session_id, kind=kind, data=data, mime=mime, expires_at=now + timedelta(days=days))
        s.add(m)
        await s.commit()
        await s.refresh(m)
        return m.id


async def media_latest(session_id: str, kind: str) -> Media | None:
    async with db.session() as s:
        return (
            await s.execute(
                select(Media)
                .where(Media.session_id == session_id, Media.kind == kind)
                .order_by(Media.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def media_cleanup(now: datetime) -> int:
    async with db.session() as s:
        rows = list((await s.execute(select(Media))).scalars())
        n = 0
        for m in rows:
            if (aware(m.expires_at) or now) < now:
                await s.delete(m)
                n += 1
        await s.commit()
        return n


# ---------- settings ----------
async def setting_get(key: str, default: Any = None) -> Any:
    async with db.session() as s:
        r = await s.get(AppSetting, key)
        return r.value.get("v", default) if r else default


async def setting_set(key: str, value: Any) -> None:
    async with db.session() as s:
        r = await s.get(AppSetting, key)
        if r is None:
            s.add(AppSetting(key=key, value={"v": value}))
        else:
            r.value = {"v": value}
        await s.commit()
