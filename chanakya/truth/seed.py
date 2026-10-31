"""Load data/seed/*.yaml (idempotent) and upsert staff phones from env."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import func, select

from chanakya.config import ROOT, Settings
from chanakya.truth import db
from chanakya.truth.models import (
    Charge,
    Content,
    ExchangeValue,
    FinancePlan,
    Guide,
    Offer,
    PriceStock,
    Product,
    ProtectionPlan,
    Staff,
    Store,
)

SEED_DIR = ROOT / "data" / "seed"


def _load(name: str, seed_dir: Path = SEED_DIR):
    return yaml.safe_load((seed_dir / name).read_text(encoding="utf-8")) or []


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _clean(row: dict, model) -> dict:
    cols = {c.key for c in model.__table__.columns}
    return {k: v for k, v in row.items() if k in cols}


async def load(force: bool = False, seed_dir: Path = SEED_DIR) -> bool:
    """Load seed rows if the products table is empty (or force). Returns True if loaded."""
    async with db.session() as s:
        n = (await s.execute(select(func.count()).select_from(Product))).scalar_one()
        if n and not force:
            return False
        if force:
            for m in (
                PriceStock,
                Product,
                Store,
                Offer,
                FinancePlan,
                Charge,
                ProtectionPlan,
                ExchangeValue,
                Guide,
                Content,
            ):
                await s.execute(m.__table__.delete())
        for r in _load("stores.yaml", seed_dir):
            s.add(Store(**_clean(r, Store)))
        for r in _load("products.yaml", seed_dir):
            assert r.get("test_data") is True, f"seed row without test_data: {r.get('sku')}"
            s.add(Product(**_clean(r, Product)))
        for r in _load("prices_stock.yaml", seed_dir):
            s.add(PriceStock(**_clean(r, PriceStock)))
        for r in _load("offers.yaml", seed_dir):
            r = dict(r, valid_from=_d(r["valid_from"]), valid_to=_d(r["valid_to"]))
            s.add(Offer(**_clean(r, Offer)))
        for r in _load("finance.yaml", seed_dir):
            s.add(FinancePlan(**_clean(r, FinancePlan)))
        for r in _load("charges.yaml", seed_dir):
            s.add(Charge(**_clean(r, Charge)))
        for r in _load("protection.yaml", seed_dir):
            s.add(ProtectionPlan(**_clean(r, ProtectionPlan)))
        for r in _load("exchange.yaml", seed_dir):
            s.add(ExchangeValue(**_clean(r, ExchangeValue)))
        guides = yaml.safe_load((seed_dir / "guides.yaml").read_text(encoding="utf-8"))
        for kind, rows in guides.items():
            s.add(Guide(id=kind, kind=kind, rows=rows))
        for r in _load("content.yaml", seed_dir):
            s.add(Content(**_clean(r, Content)))
        await s.commit()
    return True


async def upsert_staff(settings: Settings) -> None:
    people = [
        (settings.associate_phone, settings.associate_name, "associate", "tv"),
        (settings.manager_phone, settings.manager_name, "manager", "all"),
        (settings.expert_phone, settings.expert_name, "expert", "tv"),
        (settings.visitor_phone, "Visitor", "visitor", "all"),
    ]
    async with db.session() as s:
        for phone, name, role, dept in people:
            if not phone:
                continue
            row = (await s.execute(select(Staff).where(Staff.wa_id == phone))).scalar_one_or_none()
            if row is None:
                s.add(
                    Staff(
                        wa_id=phone,
                        name=name,
                        role=role,
                        base_role=role,
                        store_id=settings.store_id,
                        dept=dept,
                        lang="hi",
                        streak=0,
                    )
                )
            else:
                row.name = name
                row.store_id = settings.store_id
        await s.commit()
