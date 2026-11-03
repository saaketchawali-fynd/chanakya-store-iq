"""Live data: pull the catalogue from Jio Commerce into the truth tables.

- getProducts (every page) → products, selling price, MRP, availability (sellable), resQ plans
- getPromotionOffers (per product) → offers
The tables are replaced in one transaction, and only after a complete fetch; if a fetch fails the previous data
stay and the error is shown on /health. Charges, EMI plans and exchange values have no source in the documented
API, so those tables stay empty. Seed (sample) data is never loaded in this mode, and is removed if present."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from sqlalchemy import select

from chanakya.config import Settings
from chanakya.core import clock
from chanakya.log import get_log
from chanakya.truth import db
from chanakya.truth.jio import PLAN_LABELS, JioClient, JioError, category_of, is_protection
from chanakya.truth.jio_map import map_product, map_promotions, map_protection
from chanakya.truth.models import (
    AppSetting,
    Charge,
    ExchangeValue,
    FinancePlan,
    Offer,
    PriceStock,
    Product,
    ProtectionPlan,
    Store,
)

log = get_log("jio_sync")
DATA_TABLES = (PriceStock, Product, Offer, ProtectionPlan, Charge, FinancePlan, ExchangeValue, Store)
STATUS: dict = {"source": "jio", "configured": False, "running": False, "last_ok_at": None, "last_error": None,
                "last_ms": None, "counts": {}}


def configured(s: Settings) -> bool:
    return bool(s.jio_app_id and s.jio_app_token)


def _label_category(label: str) -> str | None:
    for cat, rx in PLAN_LABELS:
        if rx.search(label or ""):
            return cat
    return None


async def purge_seed() -> bool:
    """Remove sample (seed) data from the truth tables, e.g. left from an earlier deploy. True if removed."""
    async with db.session() as s:
        row = await s.get(AppSetting, "seed_hash")
        infos = (await s.execute(select(Product.info))).scalars().all()
        seeded = any((i or {}).get("source") != "jio" for i in infos)
        if row is None and not seeded:
            return False
        for m in DATA_TABLES:
            await s.execute(m.__table__.delete())
        if row is not None:
            await s.delete(row)
        await s.commit()
    log.warning("seed_data_removed")
    return True


async def fetch(s: Settings) -> tuple[list[dict], list[dict], list[dict], int]:
    """(products, offers, protection plans, promotion errors) from the backend."""
    client = JioClient(s.jio_domain, s.jio_app_id, s.jio_app_token)
    try:
        products, plans_raw, seen = [], [], set()
        async for _, items, _ in client.iter_products(page_size=50):
            for it in items:
                if is_protection(it):
                    plans_raw.append(it)
                    continue
                cat = category_of(it)
                row = map_product(it, cat) if cat else None
                if row and row["sku"] not in seen:
                    seen.add(row["sku"])
                    products.append(row)
        promos: dict[str, list[dict]] = {}
        errors = 0
        sem = asyncio.Semaphore(4)

        async def one(row: dict) -> None:
            nonlocal errors
            async with sem:
                try:
                    promos[row["sku"]] = await client.promotions(row["info"]["slug"], s.jio_store_id)
                except JioError:
                    errors += 1

        await asyncio.gather(*(one(r) for r in products if r["info"].get("slug")))
    finally:
        await client.close()
    today = clock.now().date()
    offers = map_promotions(promos, {r["sku"]: r["category"] for r in products}, today)
    labels = {}
    for it in plans_raw:
        lab = ((it.get("attributes") or {}).get("fg-product-category") or "").strip().lower()
        if lab and _label_category(lab):
            labels[lab] = _label_category(lab)
    plans = [x for x in (map_protection(it, labels) for it in plans_raw) if x]
    return products, offers, plans, errors


KEY_SPEC = {"tv": "size_in", "ac": "ton", "wm": "capacity_kg", "fridge": "capacity_l", "laptop": "ram_gb"}


def catalog_report(products: list[dict]) -> dict:
    """For checking the mapping on /health: the backend categories behind each Chanakya category, and how many
    products carry the spec the fit logic needs most (and from where)."""
    cats: dict[str, dict[str, int]] = {}
    cover: dict[str, dict[str, int]] = {}
    specs: dict[str, dict[str, int]] = {}
    for r in products:
        c = r["category"]
        for k, v in {**(r.get("specs") or {}), **(r.get("energy") or {})}.items():  # which questions the data supports
            if v not in (None, "", []):
                specs.setdefault(c, {})[k] = specs.setdefault(c, {}).get(k, 0) + 1
        for name in r["info"].get("categories") or ["?"]:
            cats.setdefault(c, {})[name] = cats.setdefault(c, {}).get(name, 0) + 1
        src = r["info"]["spec_source"].get(KEY_SPEC[c])
        k = "missing" if not src else ("name" if src == "name" else "attributes")
        cover.setdefault(c, {"attributes": 0, "name": 0, "missing": 0})[k] += 1
    return {"backend_categories": {c: dict(sorted(v.items(), key=lambda x: -x[1])[:15]) for c, v in cats.items()},
            "key_spec": {c: {"spec": KEY_SPEC[c], **v} for c, v in cover.items()},
            "spec_cover": {c: dict(sorted(v.items(), key=lambda x: -x[1])[:16]) for c, v in specs.items()}}


async def _yesterday(ses, store_id: str) -> dict[str, int]:
    """Availability at the end of yesterday (the last sync before today's first), so the brief can say what went
    out of stock or came back since yesterday. Kept in app_settings as {day, avail}; real data only."""
    today = clock.today().isoformat()
    row = await ses.get(AppSetting, "jio_availability")
    if row is not None and (row.value or {}).get("day") == today:
        return dict(row.value.get("avail") or {})
    prev = {r.sku: int(r.qty > 0) for r in (await ses.execute(select(PriceStock).where(PriceStock.store_id == store_id))).scalars()}
    val = {"day": today, "avail": prev}
    if row is None:
        ses.add(AppSetting(key="jio_availability", value=val))
    else:
        row.value = val
    return prev


async def write(s: Settings, products: list[dict], offers: list[dict], plans: list[dict], meta: dict) -> None:
    uat = "jiox5" in s.jio_domain
    async with db.session() as ses:
        before = await _yesterday(ses, s.store_id)
        for m in DATA_TABLES:
            await ses.execute(m.__table__.delete())
        ses.add(Store(id=s.store_id, name=s.store_name, city=""))
        for r in products:
            ses.add(Product(sku=r["sku"], category=r["category"], brand=r["brand"], model=r["model"], title=r["title"],
                            specs=r["specs"], dims=r["dims"], energy=r["energy"], image=r["image"], info=r["info"],
                            test_data=uat))
            avail = 1 if r["sellable"] else 0  # availability only: the documented API gives no per-store counts
            ses.add(PriceStock(sku=r["sku"], store_id=s.store_id, price=r["price"], mrp=r["mrp"], qty=avail,
                               qty_yesterday=before.get(r["sku"], avail), nearby_store_id=None, nearby_qty=0,
                               delivery_days=0))
        for o in offers:
            ses.add(Offer(**o))
        codes = set()
        for pl in plans:
            if pl["code"] not in codes:
                codes.add(pl["code"])
                ses.add(ProtectionPlan(**pl))
        row = await ses.get(AppSetting, "jio_sync")
        if row is None:
            ses.add(AppSetting(key="jio_sync", value=meta))
        else:
            row.value = meta
        await ses.commit()


async def sync(s: Settings, facts) -> dict:
    """One full refresh. Never raises; the outcome is in STATUS (shown on /health)."""
    STATUS["configured"] = configured(s)
    if not STATUS["configured"]:
        STATUS["last_error"] = "JIO_APP_ID / JIO_APP_TOKEN not set"
        return STATUS
    if STATUS["running"]:
        return STATUS
    STATUS["running"] = True
    t0 = time.perf_counter()
    try:
        products, offers, plans, errors = await fetch(s)
        if not products:
            raise JioError(None, "the catalogue returned no products in Chanakya's categories")
        counts = {"products": len(products), "offers": len(offers), "protection_plans": len(plans),
                  "promotion_errors": errors}
        for r in products:
            counts[r["category"]] = counts.get(r["category"], 0) + 1
        meta = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "counts": counts, "domain": s.jio_domain,
                "catalog": catalog_report(products)}
        await write(s, products, offers, plans, meta)
        await facts.refresh()
        STATUS.update(last_ok_at=meta["at"], last_error=None, counts=counts)
        log.info("jio_sync_ok", ms=round((time.perf_counter() - t0) * 1000), **counts)
    except Exception as e:  # keep the previous data
        STATUS["last_error"] = f"{type(e).__name__}: {e}"[:300]
        log.warning("jio_sync_failed", err=STATUS["last_error"])
    finally:
        STATUS["running"] = False
        STATUS["last_ms"] = round((time.perf_counter() - t0) * 1000)
    return STATUS


async def loop(s: Settings, facts, after=None) -> None:
    """Sync now, then every jio_sync_minutes (background task for the live app). `after()` runs after each good sync
    (the price watch)."""
    while True:
        await sync(s, facts)
        if after is not None and STATUS.get("last_error") is None:
            try:
                await after()
            except Exception:  # never stop the sync loop
                log.exception("after_sync_failed")
        await asyncio.sleep(max(5, s.jio_sync_minutes) * 60)


async def last_meta() -> dict | None:
    async with db.session() as ses:
        row = await ses.get(AppSetting, "jio_sync")
        return row.value if row else None
