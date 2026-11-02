"""SessionRepo: get/create/switch sessions, codes C1..Cn per associate per day."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select

from chanakya.core.contracts import HomeCard, SessionState, Step
from chanakya.truth import db
from chanakya.truth.models import SessionRow, Staff


def _row_to_state(r: SessionRow) -> SessionState:
    return SessionState.model_validate(r.state)


class SessionRepo:
    async def get(self, session_id: str | None) -> SessionState | None:
        if not session_id:
            return None
        async with db.session() as s:
            r = await s.get(SessionRow, session_id)
            return _row_to_state(r) if r else None

    async def status(self, session_id: str) -> str | None:
        async with db.session() as s:
            r = await s.get(SessionRow, session_id)
            return r.status if r else None

    async def create(
        self, associate_wa_id: str, now: datetime, customer_lang: str = "hi", category: str | None = None
    ) -> SessionState:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with db.session() as s:
            n = (
                await s.execute(
                    select(func.count())
                    .select_from(SessionRow)
                    .where(
                        SessionRow.associate_wa_id == associate_wa_id,
                        SessionRow.created_at >= start,
                        SessionRow.created_at < start + timedelta(days=1),
                    )
                )
            ).scalar_one()
            st = SessionState(
                id=str(uuid.uuid4()),
                associate_wa_id=associate_wa_id,
                code=f"C{n + 1}",
                home=HomeCard(category=category, customer_lang=customer_lang),  # type: ignore[arg-type]
                created_at=now,
                updated_at=now,
            )
            st.extra["started_at"] = now.isoformat()
            s.add(
                SessionRow(
                    id=st.id,
                    associate_wa_id=associate_wa_id,
                    code=st.code,
                    state=st.model_dump(mode="json"),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            staff = (await s.execute(select(Staff).where(Staff.wa_id == associate_wa_id))).scalar_one_or_none()
            if staff:
                staff.current_session_id = st.id
            await s.commit()
        return st

    async def save(self, st: SessionState, now: datetime | None = None) -> None:
        st.updated_at = now or st.updated_at
        async with db.session() as s:
            r = await s.get(SessionRow, st.id)
            if r is None:
                return
            r.state = st.model_dump(mode="json")
            r.updated_at = st.updated_at
            r.customer_wa_id = st.customer_wa_id
            r.optin_token = st.optin_token
            if st.step == Step.CLOSED:
                r.status = "closed"
                r.outcome = st.extra.get("outcome")
            await s.commit()

    async def by_code(self, associate_wa_id: str, code: str) -> SessionState | None:
        async with db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(SessionRow)
                        .where(SessionRow.associate_wa_id == associate_wa_id, SessionRow.code == code)
                        .order_by(SessionRow.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                if r.status == "active":
                    return _row_to_state(r)
            return _row_to_state(rows[0]) if rows else None

    async def active(self, associate_wa_id: str) -> list[SessionState]:
        async with db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(SessionRow)
                        .where(SessionRow.associate_wa_id == associate_wa_id, SessionRow.status == "active")
                        .order_by(SessionRow.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_row_to_state(r) for r in rows]

    async def all_active(self) -> list[SessionState]:
        async with db.session() as s:
            rows = (await s.execute(select(SessionRow).where(SessionRow.status == "active"))).scalars().all()
            return [_row_to_state(r) for r in rows]

    async def by_token(self, token: str) -> SessionState | None:
        async with db.session() as s:
            rows = (await s.execute(select(SessionRow).where(SessionRow.optin_token.is_not(None)))).scalars().all()
            for r in rows:
                if r.optin_token and r.optin_token.lower() == token.lower():
                    return _row_to_state(r)
        return None

    async def by_customer(self, customer_wa_id: str) -> SessionState | None:
        async with db.session() as s:
            r = (
                await s.execute(
                    select(SessionRow)
                    .where(SessionRow.customer_wa_id == customer_wa_id)
                    .order_by(SessionRow.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_state(r) if r else None

    async def by_prefix(self, prefix: str) -> SessionState | None:
        async with db.session() as s:
            r = (
                await s.execute(select(SessionRow).where(SessionRow.id.like(f"{prefix}%")).limit(1))
            ).scalar_one_or_none()
            return _row_to_state(r) if r else None

    async def since(self, start: datetime) -> list[tuple[SessionRow, SessionState]]:
        async with db.session() as s:
            rows = (await s.execute(select(SessionRow).where(SessionRow.created_at >= start))).scalars().all()
            return [(r, _row_to_state(r)) for r in rows]

    async def close(self, st: SessionState, outcome: str | None, now: datetime) -> None:
        st.step = Step.CLOSED
        st.extra["outcome"] = outcome
        await self.save(st, now)
        async with db.session() as s:
            staff = (await s.execute(select(Staff).where(Staff.wa_id == st.associate_wa_id))).scalar_one_or_none()
            from chanakya.core.orchestrator import AFTER_SALE_AWAIT

            if staff and staff.current_session_id == st.id and st.awaiting not in AFTER_SALE_AWAIT:
                staff.current_session_id = None
            await s.commit()
