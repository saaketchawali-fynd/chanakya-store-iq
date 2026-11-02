"""Save the Jio Commerce catalog and its promotions locally, using only the two documented methods
(getProducts, getPromotionOffers). Used to build and test the field mapping from real data.

Run it yourself. The application ID and token are typed at hidden prompts (or read from JIO_APP_ID / JIO_APP_TOKEN)
and are never printed or saved.

    python scripts/jio_dump.py

Writes .jio_probe/catalog.json (every product, as returned) and .jio_probe/promotions.json (offers per product
slug, for the products in Chanakya's categories). Read-only."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chanakya.truth.jio import JioClient, category_of  # noqa: E402
from scripts.jio_probe import ask  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / ".jio_probe"


async def main() -> int:
    domain = os.environ.get("JIO_DOMAIN", "https://api.jiox5.de")
    print(f"Jio Commerce storefront API: {domain}\nType the sales channel's application ID and token (hidden).")
    client = JioClient(domain, ask("JIO_APP_ID", "Application ID: "), ask("JIO_APP_TOKEN", "Application token: "))
    t0 = time.perf_counter()
    items = []
    async for page_no, page_items, total in client.iter_products():
        items += page_items
        print(f"\r  getProducts: page {page_no}, {len(items)}/{total} products", end="", flush=True)
    print()
    OUT.mkdir(exist_ok=True)
    (OUT / "catalog.json").write_text(json.dumps(items, ensure_ascii=False))
    ours = [p for p in items if category_of(p)]
    by_cat: dict[str, int] = {}
    for p in ours:
        by_cat[category_of(p)] = by_cat.get(category_of(p), 0) + 1
    print(f"  {len(items)} products saved; in Chanakya's categories: {by_cat}")
    promos: dict[str, list] = {}
    sem = asyncio.Semaphore(4)

    async def one(slug: str) -> None:
        async with sem:
            promos[slug] = await client.promotions(slug)

    done = 0
    for chunk in range(0, len(ours), 40):
        await asyncio.gather(*(one(p["slug"]) for p in ours[chunk:chunk + 40]))
        done = min(len(ours), chunk + 40)
        print(f"\r  getPromotionOffers: {done}/{len(ours)} products", end="", flush=True)
    print()
    (OUT / "promotions.json").write_text(json.dumps(promos, ensure_ascii=False))
    with_offers = sum(1 for v in promos.values() if v)
    print(f"  offers found on {with_offers} products. Done in {time.perf_counter() - t0:.0f} s.")
    print(f"  Files: {OUT / 'catalog.json'}, {OUT / 'promotions.json'} (no credentials in them).")
    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
