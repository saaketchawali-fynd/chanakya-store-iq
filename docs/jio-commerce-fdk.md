# Jio Commerce FDK as Chanakya's data source — assessment (2 Nov 2026)

Source: `FDK_Method_Reference_v1.9.5_UAT.docx`: 5 pages, last edited 2 Nov 2026 by Aryan10 Sharma (Consultant).
It documents two storefront ("application") SDK methods of Jio Commerce, which is Fynd's commerce platform under
Jio branding, plus the UAT URLs.

## What the document covers

| Method | HTTP | Inputs | Returns |
|---|---|---|---|
| `getProducts` (Catalog) | `GET /service/application/catalog/v1.0/products` | `q` (search), `f` (filters: `\|\|` = OR, `:::` = AND, e.g. `brand:X:::l3_categories:Y`), `filters`, `sort_on`, `page_id` / `page_no`, `page_size` (12), `page_type` (cursor / number) | `items[]`: uid, name, slug, item_code, brand, categories, price (effective / marked / selling, min–max), medias, sizes, variants, rating, highlights, discount, attributes, sellable, teaser_tag, country_of_origin, moq. Also filters, sort options and paging. |
| `getPromotionOffers` (Cart) | `GET /service/application/cart/v1.0/available-promotions` | `slug`, `store_id`, `page_size`, `promotion_group`, `cart_type` | `available_promotions[]`: id, offer_text, promotion_name / type / group, `valid_till`, buy_rules, discount_rules, free_gift_items, description (T&C). |

UAT URLs:
- Storefront: `reliance-digital.jiox5.de`
- Platform panel: `platform.jiox5.de/company/1/home/`
- Partners portal: `partners.jiox5.de`

## Can we reach it? (checked 2 Nov 2026 from a Mac on the public internet)

- **API host:** `api.jiox5.de` resolves and answers. Both documented endpoints exist and return
  `401 {"error":"Invalid authorization token"}` without credentials. So they are live and need a sales-channel token.
- **Platform and partners hosts:** both return 200.
- **Storefront** `reliance-digital.jiox5.de`: returns 403 to scripts (Akamai). We don't need it; the API host is the
  right entry point.
- **SDK docs** `partners.jiox5.de/help/docs/...`: return 403 and need a partner login. The public Fynd docs and SDK
  describe the same API.
- **Render (Singapore):** should reach `api.jiox5.de` too (public Google load balancer). This is not tested from
  Render yet.

### Authentication (from Fynd's public SDK: fdk-client-python, docs.fynd.com)

- **Header:** `Authorization: Bearer base64("<application_id>:<application_token>")`. The public docs write `Basic`
  with the same base64 value, so the first test call should settle which one UAT accepts.
- **Signature:** the SDK also signs each call (`x-fp-date`, and `x-fp-signature` = "v1.1:" + an HMAC-SHA256). It is
  not known whether UAT enforces this. Using the official SDK, or copying its signing, avoids the question.
- **Optional location header:** `x-location-detail` (pincode, city, …) for location-aware prices.
- **Where the ID and token come from:** in the platform panel, Company → Settings → Developers → Application Token.
  On Fynd this is per sales channel, so we need the **Reliance Digital UAT** sales channel. The user enters them as
  `JIO_APP_ID` / `JIO_APP_TOKEN`, in Render's dashboard or a local `.env`, never in the repo.

## Probe results with the real token (2 Nov 2026, `scripts/jio_probe.py`, read-only)

- **Auth:** `Authorization: Bearer base64(id:token)` works, with no signing headers needed. The token belongs to the
  sales channel **"Reliance Digital"**, company 1 (RELIANCE RETAIL LTD).
- **Catalog:** 4,130 real Reliance Digital products (Apple 601, Samsung 203, Sony 166, …), real names, MRPs and
  images. Searches return TV 112, AC 62 (including resQ plans), washing machine 23, fridge 83, laptop 379.
- **Per-store stock and price:** `getProductStockByIds(item_id)` lists every store with quantity, effective and
  marked price, store id, code and name. ✅
  - UAT has a handful of stores and DCs, e.g. "Reliance digital -RD" (RDX05) and "RRL Mumbai Digital DC". There is
    no Juhu store.
  - The quantities are test values (10000 / 0).
- **Dimensions:** `getProductSizesBySlug` gives width / height / length in cm, plus weight. This is usable for the
  to-scale try-on, but check whether it is the product or the box.
- **Specs:** they depend on the product.
  - Laptops are rich: RAM 431 products, screen 440, processor, GPU, OS.
  - Fridges are fair: total capacity 61, type 96, star rating, dimensions.
  - Washing machines are thin: type 17, loading 16, capacity about 20.
  - TVs and ACs are poor:
    - TV screen size is filled on 16 products in the whole catalog.
    - There is no refresh-rate or brightness attribute.
    - AC room size is on 31 products, and there is no tonnage attribute.
    - Size and tonnage do appear in the product names ("108 cm (43 inch)", "1.5 Ton").
- **Prices:** MRPs look real, but UAT selling prices are test values, e.g. a 43" Sony 4K TV at ₹1,000 (MRP ₹68,900),
  and prices differ per store (₹1,000 vs ₹11,100). They cannot be shown to customers as they are.
- **Promotions:** `getPromotionOffers` and `getLadderOffers` work, but returned no offers for the product tried.
  We still need to scan more products.
- **resQ protection plans** are catalog items, with price, cover, exclusions and the purchase-price band they apply to.
- **Not reached:** `getStores` / `getInStockLocations` (v2.0 paths), `getProductPriceBySlug` / `getProductSellersBySlug`
  (v4.0 paths) all returned 404. This is likely an older API version on this server (SDK v1.9.5); retry the v1.0 /
  v3.0 paths.
- **Test-data noise:** e.g. `keywords: test`, a department named "Aaliyah4045…", "NaN kg".

## What Chanakya needs vs what the FDK gives

Today the agent reads everything from DB tables, seeded from `data/seed/*.yaml` and loaded by
`FactsService.refresh()`. A sync job that writes Jio data into the same tables needs **no change to the agent**.
Every number keeps a data source tag (`spec:…`, `price:…`) for the number firewall.

| Chanakya data | FDK source | Fit |
|---|---|---|
| Products: sku, brand, model, title, category, image | `getProducts` items (item_code / uid / slug, name, brand, categories, medias) | ✅ |
| Specs the fit logic uses (size_in, refresh_hz, brightness_nits, panel, capacity_l / kg, ton, star rating, width_mm / height_mm for try-on, …) | `attributes`, `highlights`; full detail via `getProductDetailBySlug` | ⚠️ Free-form attributes: the actual keys are unknown until a real call. We need a key → spec mapping per category. If dimensions are missing, try-on cannot draw to scale. |
| Price, MRP | `price.effective` / `price.marked` (listing, not store-specific); store price via `getProductPriceBySlug(slug, size, store_id)` | ✅ |
| Stock in this store, nearby store | `getProductSizesBySlug(slug, store_id)` ("sizes with quantities"), `getInStockLocations`, `getProductStockByIds` (no store) | ⚠️ Possible; exact quantity fields are not confirmed |
| Stores | `getStores` (by lat/long, store_code), `getLocationDetailsById` | ✅ We need the Juhu store's uid |
| Product / brand / instant offers with end date | `getPromotionOffers(slug, store_id)`: offer_text, `valid_till`, discount_rules | ✅ We must turn discount_rules into amounts |
| Bank / card offers (e.g. HDFC 10% up to ₹5,000) | `getPromotionPaymentOffers` (needs a cart), `validateCouponForPayment` | ⚠️ Needs a cart; not confirmed |
| EMI plans | none in the storefront SDK (`getPaymentModeRoutes` needs a cart / amount) | ❌ Keep `finance.yaml` |
| Installation / accessory charges | none | ❌ Keep `charges.yaml` (store policy) |
| Exchange values | none | ❌ Keep `exchange.yaml` |
| resQ protection plans | unknown (they may be catalog items) | ❓ |

## Risks and open questions

1. **UAT catalog contents:** whether it holds Reliance Digital electronics (TV, AC, WM, fridge, laptop) with real
   attributes, or placeholder data. This decides whether the fit logic, which needs numbers, can run on it.
2. **Auth details:** Bearer vs Basic, and whether signing is enforced.
3. **SDK version:** v1.9.5 (Jio) vs Fynd's public 3.x SDK. The paths match, but response fields may differ.
4. **Read-only use:** use only catalog, promotion and store reads. No cart or order writes in UAT unless agreed.

## Implemented (2 Nov 2026)

- **Live mode by default** (`DATA_SOURCE=jio`): product, price, availability and offer data come only from
  `getProducts` and `getPromotionOffers` (`chanakya/truth/jio.py`, `jio_map.py`, `jio_sync.py`).
  - Sync runs at start-up and every `JIO_SYNC_MINUTES` (30).
  - The tables are replaced only after a complete fetch.
  - With no token the catalogue is empty; sample data is never used, and is removed if present.
- **Kept from files:** only the agent's own rules (seat distance → TV size, room → tonnage, family → capacity) and
  the shift-brief clips. The electricity tariff is excluded.
- **Not shown, because the documented API has no data for them:**
  - stock counts, nearby stores and delivery days (stock shows only "Available" / "Not available right now";
    delivery is asked as the customer's preferred day, for the store to confirm);
  - installation charges;
  - EMI (not offered);
  - bank offers (the bank question is skipped);
  - exchange values;
  - running cost.
- **resQ plans:** taken from the catalogue and offered as optional add-ons within their stated price band.
- **Option roles checked against real prices:** "costs less" really costs less; "one step up" costs more and fits
  at least as well.
- **`/health` → `data`:** source, token set, last sync time, counts per category, last error.
- **Tests:** `tests/unit/test_jio.py` and `tests/golden/test_jio_flow.py`, on recorded UAT responses.
- `scripts/jio_probe.py` and `scripts/jio_dump.py` are read-only tools. You type the token at a hidden prompt.

## Plan (original)

1. **Probe** (read-only, about 1 hour once we have the token): a script that reads the ID and token from env or a
   hidden prompt and calls `getProducts` (per category), `getProductDetailBySlug`, `getProductSizesBySlug` /
   `getProductPriceBySlug` for the store, `getPromotionOffers` and `getStores`. It writes a field inventory: which
   attribute keys exist, sample values, stock fields. It never prints the token.
2. **Mapping:** attribute keys → our spec keys per category; promotions → our offer rows. Products without the
   numbers the fit logic needs are shown "facts only" (no fit verdict), as label scans already are.
3. **Sync job:** pull every N minutes into the existing tables, tagged `source=jio`. Keep the yaml rows for EMI,
   charges, exchange and protection. Show the last sync time and counts on `/health`.
4. **Switch:** `DATA_SOURCE=jio|seed`, with seed as the fallback if Jio is unreachable.
