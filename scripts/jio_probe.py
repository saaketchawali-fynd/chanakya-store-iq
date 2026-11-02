"""Read-only check of the Jio Commerce storefront API (the planned data source for Chanakya).

Run it yourself. The application ID and token are typed at hidden prompts (or read from JIO_APP_ID / JIO_APP_TOKEN)
and are never printed or saved.

    python scripts/jio_probe.py                          # UAT: https://api.jiox5.de
    JIO_DOMAIN=https://api.example python scripts/jio_probe.py

Only GET calls: which sales channel the token belongs to, the catalog (listing + detail per category), sizes / price /
stock for a store, stores, and promotions. Nothing is written to Jio. The report (endpoints, status, product fields
and sample values; no credentials) goes to .jio_probe/report.json, full responses to .jio_probe/raw/, and a short
summary is printed."""

from __future__ import annotations

import base64
import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx

DOMAIN = os.environ.get("JIO_DOMAIN", "https://api.jiox5.de").rstrip("/")
OUT = Path(__file__).resolve().parents[1] / ".jio_probe"
CATEGORIES = {"tv": "television", "ac": "air conditioner", "wm": "washing machine", "fridge": "refrigerator",
              "laptop": "laptop"}
JUHU = {"latitude": 19.1075, "longitude": 72.8263, "range": 20000}  # Reliance Digital Juhu, Mumbai
APP = "/service/application"


def ask(env: str, prompt: str) -> str:
    v = os.environ.get(env, "").strip()
    while not v:
        v = getpass.getpass(prompt).strip()
    return v


def shape(x, depth: int = 0):
    """Field inventory: keys and types (first list element), 4 levels deep."""
    if depth > 4:
        return type(x).__name__
    if isinstance(x, dict):
        return {k: shape(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [shape(x[0], depth + 1)] if x else []
    return type(x).__name__


class Probe:
    def __init__(self, app_id: str, token: str):
        self.raw = base64.b64encode(f"{app_id}:{token}".encode()).decode()
        self.scheme = "Bearer"
        self.c = httpx.Client(base_url=DOMAIN, timeout=httpx.Timeout(25, connect=8))
        self.report: dict = {"domain": DOMAIN, "calls": []}
        (OUT / "raw").mkdir(parents=True, exist_ok=True)

    def get(self, name: str, path: str, **params) -> dict | list | None:
        params = {k: v for k, v in params.items() if v is not None}
        t0 = time.perf_counter()
        try:
            r = self.c.get(APP + path, params=params, headers={"Authorization": f"{self.scheme} {self.raw}"})
            ms = round((time.perf_counter() - t0) * 1000)
            try:
                data = r.json()
            except ValueError:
                data = {"_text": r.text[:500]}
            status = r.status_code
        except httpx.HTTPError as e:
            ms, status, data = round((time.perf_counter() - t0) * 1000), None, {"_error": f"{type(e).__name__}: {e}"}
        (OUT / "raw" / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        ok = status is not None and status < 400
        self.report["calls"].append({"name": name, "path": path, "params": params, "status": status, "ms": ms,
                                     "shape": shape(data) if ok else None, "error": None if ok else data})
        mark = "OK " if ok else "ERR"
        print(f"{mark} {name:28s} HTTP {status}  {ms:5d} ms", flush=True)
        if not ok:
            print("    " + json.dumps(data, ensure_ascii=False)[:300])
        return data if ok else None

    def auth(self) -> bool:
        for scheme in ("Bearer", "Basic"):
            self.scheme = scheme
            if self.get(f"auth_{scheme.lower()}", "/catalog/v1.0/products", page_size=1) is not None:
                self.report["auth_scheme"] = scheme
                print(f"    → the token works with 'Authorization: {scheme} base64(id:token)'")
                return True
        return False


def items_of(d) -> list:
    return (d or {}).get("items") or []


def first_price(p: dict) -> str:
    pr = p.get("price") or {}
    eff, mk = (pr.get("effective") or {}), (pr.get("marked") or {})
    return f"₹{eff.get('min')} (MRP ₹{mk.get('min')})"


def main() -> int:
    print(f"Jio Commerce storefront API: {DOMAIN}\nType the sales channel's application ID and token (hidden).")
    pr = Probe(ask("JIO_APP_ID", "Application ID: "), ask("JIO_APP_TOKEN", "Application token: "))
    rep = pr.report
    if not pr.auth():
        print("\nThe token was refused with both schemes. Check it belongs to the Reliance Digital UAT sales channel.")
        (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
        return 1

    app = pr.get("application", "/configuration/v1.0/application")
    if isinstance(app, dict):
        rep["sales_channel"] = {k: app.get(k) for k in ("name", "_id", "company_id", "domain", "domains", "channel_type")}
        print(f"    → sales channel: {app.get('name')} (company {app.get('company_id')})")

    listing = pr.get("products_all", "/catalog/v1.0/products", page_size=12, page_type="number", page_no=1, filters="true")
    if isinstance(listing, dict):
        page = listing.get("page") or {}
        rep["catalog_total"] = page.get("item_total") or page.get("total")
        rep["filters"] = [{"key": (f.get("key") or {}).get("name"), "display": (f.get("key") or {}).get("display"),
                           "values": [(v.get("display"), v.get("count")) for v in (f.get("values") or [])[:25]]}
                          for f in listing.get("filters") or []]
        rep["sort_on"] = [s.get("value") for s in listing.get("sort_on") or []]
        print(f"    → {rep['catalog_total']} products; filters: {[f['key'] for f in rep['filters']]}")

    rep["categories"] = {}
    sample = None
    for cat, q in CATEGORIES.items():
        d = pr.get(f"search_{cat}", "/catalog/v1.0/products", q=q, page_size=5, page_type="number", page_no=1,
                   filters="false")
        its = items_of(d)
        rows = [{"name": p.get("name"), "brand": (p.get("brand") or {}).get("name"), "slug": p.get("slug"),
                 "uid": p.get("uid"), "item_code": p.get("item_code"), "price": first_price(p),
                 "sellable": p.get("sellable"), "attribute_keys": sorted((p.get("attributes") or {}).keys())}
                for p in its]
        total = ((d or {}).get("page") or {}).get("item_total")
        entry = {"query": q, "total": total, "items": rows}
        for r in rows[:3]:
            print(f"    · {r['name']} | {r['brand']} | {r['price']}")
        if its:
            slug = its[0].get("slug")
            det = pr.get(f"detail_{cat}", f"/catalog/v1.0/products/{slug}/")
            if isinstance(det, dict):
                entry["detail_attributes"] = det.get("attributes")
                entry["detail_grouped_attributes"] = det.get("grouped_attributes")
                entry["detail_highlights"] = det.get("highlights")
                n = len(det.get("attributes") or {})
                print(f"    → {slug}: {n} attributes, e.g. {list((det.get('attributes') or {}).items())[:6]}")
            sizes = pr.get(f"sizes_{cat}", f"/catalog/v1.0/products/{slug}/sizes/")
            entry["sizes"] = sizes
            sample = sample or {"slug": slug, "uid": its[0].get("uid"), "sizes": sizes}
        rep["categories"][cat] = entry

    stores = pr.get("stores", "/catalog/v2.0/locations/", page_size=10)
    near = pr.get("stores_near_juhu", "/catalog/v2.0/locations/", page_size=10, **JUHU)
    rows = []
    for d in (near, stores):
        for s in items_of(d):
            rows.append({k: s.get(k) for k in ("uid", "name", "store_code", "city", "pincode", "address")})
    rep["stores"] = rows[:20]
    for s in rows[:5]:
        print(f"    · store {s.get('uid')} {s.get('name')} ({s.get('store_code')}) {s.get('city') or ''}")

    if sample:
        slug, store = sample["slug"], (rows[0]["uid"] if rows else None)
        size_list = (sample["sizes"] or {}).get("sizes") or []
        size = (size_list[0].get("value") if size_list and isinstance(size_list[0], dict) else None) or "OS"
        pr.get("sizes_at_store", f"/catalog/v1.0/products/{slug}/sizes/", store_id=store)
        pr.get("price_at_store", f"/catalog/v4.0/products/{slug}/sizes/{size}/price/", store_id=store)
        pr.get("sellers", f"/catalog/v4.0/products/{slug}/sizes/{size}/sellers/", page_size=5)
        pr.get("stock_by_id", "/catalog/v1.0/products/stock-status/", item_id=sample["uid"])
        pr.get("in_stock_near_juhu", "/catalog/v2.0/in-stock/locations/", page_size=10, **JUHU)
        promos = pr.get("promotions", "/cart/v1.0/available-promotions", slug=slug, store_id=store, page_size=10)
        promos_any = pr.get("promotions_no_store", "/cart/v1.0/available-promotions", slug=slug, page_size=10)
        offers = (promos or {}).get("available_promotions") or (promos_any or {}).get("available_promotions") or []
        rep["promotions_sample"] = offers[:5]
        for o in offers[:5]:
            print(f"    · offer: {o.get('offer_text') or o.get('promotion_name')} (till {o.get('valid_till')})")
        pr.get("ladder_offers", "/cart/v1.0/available-ladder-prices", slug=slug, store_id=store)

    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    ok = sum(1 for c in rep["calls"] if c["status"] and c["status"] < 400)
    print(f"\nDone: {ok}/{len(rep['calls'])} calls OK. Report: {OUT / 'report.json'} (no credentials in it).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
