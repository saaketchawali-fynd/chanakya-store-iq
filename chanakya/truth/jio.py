"""Jio Commerce storefront API (Fynd platform): the only source of product, price and offer data in live mode.

Only the two methods documented in FDK_Method_Reference_v1.9.5_UAT.docx are used:
- getProducts         GET /service/application/catalog/v1.0/products
- getPromotionOffers  GET /service/application/cart/v1.0/available-promotions
Auth: Authorization: Bearer base64(application_id:application_token) (checked against UAT on 2 Nov 2026)."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import AsyncIterator

import httpx

PRODUCTS = "/service/application/catalog/v1.0/products"
PROMOTIONS = "/service/application/cart/v1.0/available-promotions"


class JioError(Exception):
    def __init__(self, status: int | None, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class JioClient:
    transport: httpx.AsyncBaseTransport | None = None  # tests: recorded backend responses

    def __init__(self, domain: str, app_id: str, token: str, timeout: float = 20):
        auth = base64.b64encode(f"{app_id}:{token}".encode()).decode()
        self.http = httpx.AsyncClient(
            base_url=domain.rstrip("/"), timeout=httpx.Timeout(timeout, connect=8),
            headers={"Authorization": f"Bearer {auth}"}, transport=JioClient.transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(3):
            try:
                r = await self.http.get(path, params=params)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise JioError(None, f"{type(e).__name__}: {e}") from e
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            if r.status_code >= 400:
                raise JioError(r.status_code, r.text[:200])
            return r.json()
        raise JioError(None, "retries exhausted")

    async def products_page(self, page_no: int, page_size: int = 50) -> dict:
        """getProducts, numbered pages."""
        return await self._get(PRODUCTS, page_type="number", page_no=page_no, page_size=page_size, filters="false")

    async def iter_products(self, page_size: int = 50) -> AsyncIterator[tuple[int, list[dict], int | None]]:
        """(page number, items, total items) for every page of the catalog."""
        page_no = 1
        while True:
            d = await self.products_page(page_no, page_size)
            page = d.get("page") or {}
            yield page_no, d.get("items") or [], page.get("item_total")
            if not page.get("has_next") or not d.get("items"):
                return
            page_no += 1

    async def promotions(self, slug: str, store_id: int | None = None) -> list[dict]:
        """getPromotionOffers for one product: the offers as returned (empty when none)."""
        d = await self._get(PROMOTIONS, slug=slug, store_id=store_id, page_size=50)
        return d.get("available_promotions") or []


# ---------------------------------------------------------------- which of Chanakya's categories a product is in
ACCESSORY = re.compile(r"bag|sleeve|cover|stand|mount|accessor|stabili[sz]er|cable|remote|skin|keyboard|mouse|"
                       r"adapter|charger|cooling pad|battery|warranty|care plan|filter|trolley|bracket", re.I)
RULES = (("tv", re.compile(r"televisions?\b|\btvs?\b", re.I)), ("ac", re.compile(r"air conditioners?", re.I)),
         ("wm", re.compile(r"washing machines?|washer", re.I)), ("fridge", re.compile(r"refrigerators?|fridges?", re.I)),
         ("laptop", re.compile(r"laptops?|notebooks?", re.I)))


def _category_names(p: dict) -> list[str]:
    return [c.get("name") or "" for c in p.get("categories") or []]


# Attributes only a real product of the category carries (an accessory or service plan does not)
DEFINING = {"tv": ("tv_operating_system", "screen_size_diagonal", "display_type", "hdmi_arc", "screen_resolution"),
            "ac": ("approx-room-size", "compressor-type"),
            "wm": ("washing-machine-type", "loading-type"),
            "fridge": ("refrigerator-type", "refrigerator-style", "total-capacity", "freezer-style"),
            "laptop": ("ram", "processor", "laptop_type")}


def category_of(p: dict) -> str | None:
    """Chanakya category from the backend's own category names; when those don't say (e.g. a fridge filed under
    "Frost Free"), from the product's own name, but only if it also carries that category's defining attributes.
    Accessories and service plans are excluded."""
    names = _category_names(p)
    if any(ACCESSORY.search(n) for n in names):
        return None
    for cat, rx in RULES:
        if any(rx.search(n) for n in names):
            return cat
    attrs, name = p.get("attributes") or {}, p.get("name") or ""
    for cat, rx in RULES:
        if rx.search(name) and any(attrs.get(k) not in (None, "", "None") for k in DEFINING[cat]):
            return cat
    return None


def is_protection(p: dict) -> bool:
    """A resQ / extended-warranty plan sold as a catalog item."""
    names = " ".join(_category_names(p)).lower()
    return "extended warranty" in names or ((p.get("brand") or {}).get("name") or "").lower() == "resq"
