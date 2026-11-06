"""FactsService — read-only queries over the truth tables (spec 9.3).

The data set is small and read-only, so it is loaded once into memory (refresh() reloads). Every object
carries its row id so callers can cite sources (price:<sku>@<store>, charge:<code>, offer:<id> …)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select

from chanakya.ai.schemas import ImageRead
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
    Store,
)


@dataclass
class ProductWithStock:
    sku: str
    category: str
    brand: str
    model: str
    title: dict
    specs: dict
    dims: dict
    energy: dict
    image: str | None
    test_data: bool
    store_id: str
    price: int
    mrp: int
    qty: int
    qty_yesterday: int
    nearby_store_id: str | None
    nearby_qty: int
    delivery_days: int
    info: dict = field(default_factory=dict)  # series, year, colours, warranty, in_box, features, model_number

    @property
    def price_source(self) -> str:
        return f"price:{self.sku}@{self.store_id}"

    @property
    def stock_known(self) -> bool:
        """False for Jio products: the documented API says only whether a product is sellable, not how many
        or where (no counts, nearby stores or delivery days are shown for them)."""
        return (self.info or {}).get("source") != "jio"

    @property
    def in_stock(self) -> bool:
        return self.qty > 0

    @property
    def available(self) -> bool:
        return self.qty > 0 or self.nearby_qty > 0

    def spec(self, key: str, default: Any = None) -> Any:
        for d in (self.specs, self.dims, self.energy):
            if d and key in d:
                return d[key]
        return default

    def short_name(self) -> str:
        """'Meru 55 QLED Pro' → 'Meru 55 Pro'-style short label for buttons (≤ 14 chars where possible)."""
        m = self.model
        return m if len(m) <= 14 else m.replace("QLED ", "").replace(" 4K", "")[:16]


@dataclass
class Snapshot:
    stores: dict[str, Store] = field(default_factory=dict)
    products: dict[str, Product] = field(default_factory=dict)
    stock: dict[tuple[str, str], PriceStock] = field(default_factory=dict)
    offers: list[Offer] = field(default_factory=list)
    finance: list[FinancePlan] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    protection: list[ProtectionPlan] = field(default_factory=list)
    exchange: list[ExchangeValue] = field(default_factory=list)
    guides: dict[str, Any] = field(default_factory=dict)
    content: list[Content] = field(default_factory=list)


def _code(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _codes(text: str) -> set[str]:
    """Model-number-like tokens printed on a label: letters and digits together, 5+ characters ('KD-43X7002E')."""
    out = set()
    for tok in re.split(r"[\s,;:()]+", text or ""):
        c = _code(tok)
        if len(c) >= 5 and re.search(r"\d", c) and re.search(r"[a-z]", c):
            out.add(c)
    return out


_SPEC_UNITS = (("capacity_kg", r"(\d+(?:\.\d+)?)\s*(?:kg|kilo)"), ("capacity_l", r"(\d{2,4})\s*(?:l|ltr|litres?|liters?)\b"),
               ("ton", r"(\d(?:\.\d+)?)\s*tons?\b"), ("size_in", r"(\d{2,3})\s*(?:\"|inch|in\b)"))


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", (s or "").lower()))


def _cover(label: str, name: str) -> float:
    """How much of what the label says is in this product's full name (0–100): a label that says 'Pro' is not the
    model without 'Pro', and a label showing part of a long name still matches it."""
    said = _words(label)
    return 100.0 * len(said & _words(name)) / len(said) if said else 0.0


SPEC_TOLERANCE = {"size_in": 0.6, "capacity_l": 3.0}  # inches (54.6 vs 55) and litres (rounding); kg and ton exact


def _label_specs(read: ImageRead, text: str) -> dict[str, float]:
    """The sizes a label states (kg, litres, ton, inch), to check against the data."""
    out: dict[str, float] = {}
    if read.size_in:
        out["size_in"] = float(read.size_in)
    low = " ".join([text.lower()] + [f"{sp.value} {sp.unit or ''} {sp.name}".lower() for sp in read.specs or []])
    for key, rx in _SPEC_UNITS:
        m = re.search(rx, low)
        if m and key not in out:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


class FactsService:
    def __init__(self, store_id: str):
        self.store_id = store_id
        self.snap = Snapshot()

    async def refresh(self) -> None:
        snap = Snapshot()
        async with db.session() as s:
            snap.stores = {r.id: r for r in (await s.execute(select(Store))).scalars()}
            snap.products = {r.sku: r for r in (await s.execute(select(Product))).scalars()}
            snap.stock = {(r.sku, r.store_id): r for r in (await s.execute(select(PriceStock))).scalars()}
            snap.offers = list((await s.execute(select(Offer))).scalars())
            snap.finance = list((await s.execute(select(FinancePlan))).scalars())
            snap.charges = list((await s.execute(select(Charge))).scalars())
            snap.protection = list((await s.execute(select(ProtectionPlan))).scalars())
            snap.exchange = list((await s.execute(select(ExchangeValue))).scalars())
            snap.guides = {r.kind: r.rows for r in (await s.execute(select(Guide))).scalars()}
            snap.content = list((await s.execute(select(Content))).scalars())
        self.snap = snap

    # ---------- products ----------
    def _with_stock(self, p: Product, store_id: str) -> ProductWithStock | None:
        st = self.snap.stock.get((p.sku, store_id))
        if st is None:
            return None
        return ProductWithStock(
            sku=p.sku,
            category=p.category,
            brand=p.brand,
            model=p.model,
            title=p.title or {},
            specs=p.specs or {},
            dims=p.dims or {},
            energy=p.energy or {},
            image=p.image,
            test_data=p.test_data,
            store_id=store_id,
            price=st.price,
            mrp=st.mrp,
            qty=st.qty,
            qty_yesterday=st.qty_yesterday,
            nearby_store_id=st.nearby_store_id,
            nearby_qty=st.nearby_qty or 0,
            delivery_days=st.delivery_days or 1,
            info=p.info or {},
        )

    def product_sync(self, sku: str, store_id: str | None = None) -> ProductWithStock | None:
        p = self.snap.products.get(sku)
        return self._with_stock(p, store_id or self.store_id) if p else None

    async def product(self, sku: str, store_id: str | None = None) -> ProductWithStock | None:
        return self.product_sync(sku, store_id)

    def products_sync(self, category: str, store_id: str | None = None) -> list[ProductWithStock]:
        sid = store_id or self.store_id
        out = [self._with_stock(p, sid) for p in self.snap.products.values() if p.category == category]
        return sorted([p for p in out if p], key=lambda p: (p.price, p.sku))

    async def products(self, category: str, store_id: str | None = None) -> list[ProductWithStock]:
        return self.products_sync(category, store_id)

    def store_name(self, store_id: str | None = None) -> str:
        s = self.snap.stores.get(store_id or self.store_id)
        return s.name if s else (store_id or self.store_id)

    async def match_label(self, read: ImageRead, category_hint: str | None = None) -> tuple[str | None, float]:
        cands = self.rank_label(read, category_hint)
        return (cands[0][0], cands[0][1]) if cands else (None, 0.0)

    def rank_label(self, read: ImageRead, category_hint: str | None = None) -> list[tuple[str, float]]:
        """The product on a shelf label, from the backend's own catalogue (user, 6 Nov 2026: real labels, real data):
        an exact product from a Chanakya QR; else the model number printed anywhere on the label (exact, or with a
        suffix such as '/TL'); else the printed name matched to the product's full name. The label's own specs (kg,
        litres, ton, inch) must agree with the data; the brand must too."""
        if read.sku and read.sku in self.snap.products:
            return [(read.sku, 100.0)]
        text = " ".join(x for x in [read.brand or "", read.model_text or ""] if x).strip()
        printed = " ".join(x for x in [read.model_text or "", read.raw_text or ""] if x)
        if not text and read.size_in is None and not printed:
            return []
        cat = read.category or category_hint
        codes = _codes(printed)
        said = _label_specs(read, " ".join([text, read.raw_text or ""]))
        scored: list[tuple[str, float]] = []
        exact: set[str] = set()
        read_model = _code(read.model_text or "")
        for p in self.snap.products.values():
            if cat and p.category != cat:
                continue
            mn = _code((p.info or {}).get("model_number") or "")
            name = (p.info or {}).get("name") or (p.title or {}).get("en") or p.model
            if read_model and read_model in (_code(p.model), mn):
                exact.add(p.sku)  # the label's model text is this product's model, letter for letter
            if mn and len(mn) >= 5 and any(c == mn or (len(c) >= 6 and (mn.startswith(c) or c.startswith(mn))) for c in codes):
                s = 96.0  # its model number is printed on the label
                exact.add(p.sku)
            elif read.model_text:
                s = max(float(fuzz.ratio(read.model_text.lower(), p.model.lower())), _cover(text, name))
            elif text:
                s = float(fuzz.token_sort_ratio(text.lower(), p.model.lower()))
            else:
                s = 40.0
            if read.brand and read.brand.lower() == p.brand.lower():
                s += 5
            elif read.brand:
                s -= 25
            for key, v in said.items():  # what the label says must be what the data says
                have = (p.specs or {}).get(key)
                if have is not None:
                    try:
                        s += 5 if abs(float(have) - v) <= SPEC_TOLERANCE.get(key, 0.01) else -60  # contradicts: out
                    except (TypeError, ValueError):
                        pass
            if read.confidence is not None and read.confidence < 0.5:
                s *= read.confidence / 0.5
            scored.append((p.sku, s))
        if exact:  # an exact model on the label decides: near namesakes ('… QLED' for '… QLED Pro') fall back
            scored = [(sku, sc if sku in exact else sc - 15) for sku, sc in scored]
        scored = [(sku, max(0.0, min(100.0, sc))) for sku, sc in scored]
        return sorted(scored, key=lambda x: -x[1])[:3]

    def find_in_text(self, text: str, category: str | None = None) -> str | None:
        """Find a product model mentioned in free text (e.g. an image caption)."""
        if not text:
            return None
        low = text.lower()
        best, best_key = None, (0.0, 0)
        for p in self.snap.products.values():
            if category and p.category != category:
                continue
            s = fuzz.partial_ratio(p.model.lower(), low)
            if p.brand.lower() not in low:
                continue
            size = (p.specs or {}).get("size_in")
            if size is not None and not re.search(rf"\b{int(size)}\b", low):
                s -= 20
            # ties ("Meru 55 QLED" vs "Meru 55 QLED Pro" both inside the text): the longer, more specific name wins
            key = (s, len(p.model) if p.model.lower() in low else 0)
            if key > best_key:
                best, best_key = p.sku, key
        return best if best_key[0] >= 85 else None

    # ---------- money and rules ----------
    async def charges(self, category: str) -> list[Charge]:
        return [c for c in self.snap.charges if c.category == category]

    async def protection(self, category: str) -> list[ProtectionPlan]:
        return [c for c in self.snap.protection if c.category == category]

    async def exchange(self, category: str, condition: str, size_in: float | None) -> tuple[int, int, str] | None:
        for e in self.snap.exchange:
            if e.category != category or e.condition != condition:
                continue
            band = e.size_band or {}
            if size_in is not None:
                if "max_in" in band and size_in > band["max_in"]:
                    continue
                if "min_in" in band and size_in < band["min_in"]:
                    continue
            elif band:
                continue
            return e.min_value, e.max_value, e.id
        return None

    async def offers(self, category: str, on_date: date) -> list[Offer]:
        return [
            o for o in self.snap.offers if category in (o.categories or []) and o.valid_from <= on_date <= o.valid_to
        ]

    async def all_offers(self, on_date: date) -> list[Offer]:
        return [o for o in self.snap.offers if o.valid_from <= on_date <= o.valid_to]

    async def finance(self, category: str) -> list[FinancePlan]:
        return [f for f in self.snap.finance if category in (f.categories or [])]

    def guide_sync(self, kind: str) -> Any:
        return self.snap.guides.get(kind)

    async def guide(self, kind: str) -> Any:
        return self.guide_sync(kind)

    def content(self, kind: str, step: str | None = None) -> list[Content]:
        return [c for c in self.snap.content if c.kind == kind and (step is None or c.step == step)]

    async def stock_changes(self, store_id: str, dept: str) -> list[dict]:
        out = []
        for (sku, sid), st in self.snap.stock.items():
            if sid != store_id:
                continue
            p = self.snap.products.get(sku)
            if not p or (dept not in ("all", None) and p.category != dept):
                continue
            kind = None
            if st.qty > 0 and st.qty_yesterday == 0:
                kind = "back_in_stock"
            elif st.qty == 0 and st.qty_yesterday > 0:
                kind = "out_of_stock"
            elif 0 < st.qty <= 2 and (p.info or {}).get("source") != "jio":  # Jio gives availability, not counts
                kind = "low"
            if kind:
                out.append(
                    {
                        "sku": sku,
                        "model": p.model,
                        "kind": kind,
                        "qty": st.qty,
                        "qty_yesterday": st.qty_yesterday,
                        "source": f"stock:{sku}@{sid}",
                    }
                )
        order = {"back_in_stock": 0, "out_of_stock": 1, "low": 2}
        return sorted(out, key=lambda r: (order[r["kind"]], r["sku"]))
