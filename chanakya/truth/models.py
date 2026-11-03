"""ORM tables (spec 9.1). Portable: JSONB on Postgres, JSON on SQLite (local dev/tests).
Array columns are stored as JSON lists for portability."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

J = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


class _Ts:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Store(_Ts, Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    city: Mapped[str] = mapped_column(String)


class Staff(_Ts, Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)  # associate | manager | expert | visitor
    base_role: Mapped[str | None] = mapped_column(String, nullable=True)  # role before a /role demo switch
    store_id: Mapped[str] = mapped_column(String)
    dept: Mapped[str] = mapped_column(String, default="tv")
    lang: Mapped[str] = mapped_column(String, default="en")
    lang_set: Mapped[bool] = mapped_column(Boolean, default=False)  # associate picked a language at first contact
    name_set: Mapped[bool] = mapped_column(Boolean, default=False)  # the associate told us their own name
    current_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_brief_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Product(_Ts, Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, index=True)
    brand: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    title: Mapped[dict] = mapped_column(J)
    specs: Mapped[dict] = mapped_column(J, default=dict)
    dims: Mapped[dict] = mapped_column(J, default=dict)
    energy: Mapped[dict] = mapped_column(J, default=dict)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    info: Mapped[dict] = mapped_column(J, default=dict)  # series, year, colours, warranty, in_box, features, ...
    test_data: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceStock(_Ts, Base):
    __tablename__ = "prices_stock"
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    store_id: Mapped[str] = mapped_column(String, primary_key=True)
    price: Mapped[int] = mapped_column(Integer)
    mrp: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)
    qty_yesterday: Mapped[int] = mapped_column(Integer)
    nearby_store_id: Mapped[str | None] = mapped_column(String, nullable=True)
    nearby_qty: Mapped[int] = mapped_column(Integer, default=0)
    delivery_days: Mapped[int] = mapped_column(Integer, default=1)


class Offer(_Ts, Base):
    __tablename__ = "offers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[dict] = mapped_column(J)
    bank: Mapped[str | None] = mapped_column(String, nullable=True)
    pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    flat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_discount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_amount: Mapped[int] = mapped_column(Integer, default=0)
    categories: Mapped[list] = mapped_column(J, default=list)
    filter: Mapped[dict] = mapped_column(J, default=dict)
    requires: Mapped[dict] = mapped_column(J, default=dict)
    stack_group: Mapped[str] = mapped_column(String)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    with_emi: Mapped[bool] = mapped_column(Boolean, default=True)


class FinancePlan(_Ts, Base):
    __tablename__ = "finance_plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[dict] = mapped_column(J)
    months: Mapped[int] = mapped_column(Integer)
    apr_pct: Mapped[float] = mapped_column(Float, default=0)
    fee_flat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_amount: Mapped[int] = mapped_column(Integer, default=0)
    banks: Mapped[list] = mapped_column(J, default=list)
    categories: Mapped[list] = mapped_column(J, default=list)


class Charge(_Ts, Base):
    __tablename__ = "charges"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String)
    title: Mapped[dict] = mapped_column(J)
    kind: Mapped[str] = mapped_column(String)  # mandatory | conditional | suggest | optional
    unit: Mapped[str] = mapped_column(String, default="flat")  # flat | per_ft
    amount: Mapped[int] = mapped_column(Integer)
    included_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    condition: Mapped[dict] = mapped_column(J, default=dict)
    reason: Mapped[dict] = mapped_column(J, default=dict)
    filter: Mapped[dict] = mapped_column(J, default=dict)


class ProtectionPlan(_Ts, Base):
    __tablename__ = "protection_plans"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String)
    title: Mapped[dict] = mapped_column(J)
    years: Mapped[int] = mapped_column(Integer)
    cover: Mapped[dict] = mapped_column(J, default=dict)
    price: Mapped[int] = mapped_column(Integer)
    filter: Mapped[dict] = mapped_column(J, default=dict)
    suggest_if: Mapped[dict] = mapped_column(J, default=dict)
    reason: Mapped[dict] = mapped_column(J, default=dict)


class ExchangeValue(_Ts, Base):
    __tablename__ = "exchange_values"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(String)
    size_band: Mapped[dict] = mapped_column(J, default=dict)
    min_value: Mapped[int] = mapped_column(Integer)
    max_value: Mapped[int] = mapped_column(Integer)


class Guide(_Ts, Base):
    __tablename__ = "guides"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    rows: Mapped[dict | list] = mapped_column(J)


class Content(_Ts, Base):
    __tablename__ = "content"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    step: Mapped[str] = mapped_column(String)
    lang: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(J, default=dict)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    associate_wa_id: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)
    state: Mapped[dict] = mapped_column(J)
    status: Mapped[str] = mapped_column(String, default="active")
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_wa_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    optin_token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventRow(_Ts, Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_wa_id: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, index=True)
    step: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(J, default=dict)
    sources: Mapped[list] = mapped_column(J, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # demo-clock time


class MessageRow(_Ts, Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    wa_message_id: Mapped[str] = mapped_column(String, unique=True)
    direction: Mapped[str] = mapped_column(String)
    wa_id: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(J, default=dict)


class Task(_Ts, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_wa_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # followup | stock_check | approval | callback
    title: Mapped[dict] = mapped_column(J)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, default="open")
    payload: Mapped[dict] = mapped_column(J, default=dict)


class InstallJob(_Ts, Base):
    __tablename__ = "install_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String)
    sku: Mapped[str] = mapped_column(String)
    slot_label: Mapped[str] = mapped_column(String)
    slot_date: Mapped[date] = mapped_column(Date)
    site_needs: Mapped[dict] = mapped_column(J, default=dict)
    tryon_media: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String, default="created")
    timeline: Mapped[dict] = mapped_column(J, default=dict)


class ExpertQuestion(_Ts, Base):
    __tablename__ = "expert_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    associate_wa_id: Mapped[str] = mapped_column(String)
    expert_wa_id: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    answer: Mapped[dict | None] = mapped_column(J, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)


class LibraryEntry(_Ts, Base):
    __tablename__ = "library"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict] = mapped_column(J)
    expert_name: Mapped[str] = mapped_column(String)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)


class Score(_Ts, Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String)
    associate_wa_id: Mapped[str] = mapped_column(String)
    steps: Mapped[dict] = mapped_column(J)
    total: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VisitLog(_Ts, Base):
    __tablename__ = "visit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_wa_id: Mapped[str] = mapped_column(String)
    entry: Mapped[dict] = mapped_column(J)
    status: Mapped[str] = mapped_column(String, default="draft")


class Media(_Ts, Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)  # room_photo | tryon | label | voice
    data: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(J)
