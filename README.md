# Chanakya — Store IQ (Reliance Digital)

An AI sales partner for store associates on one WhatsApp number, in Hindi, Marathi and English.
**Chanakya holds the facts; the associate owns the conversation.** All data is test data. The brands (Arka, Meru, Novex) are fictional.

> **AI:** Gemini handles voice notes (Hindi, Marathi, English and mixes), room and label photos, and try-on blending. It only extracts and classifies. Products, prices, offers and every number shown come from the database, checked by the number firewall.
> - `AI_MODE=auto` (default) uses Gemini when `GEMINI_API_KEY` is set and falls back to offline keyword rules for typed text.
> - `AI_MODE=fixture` replays canned JSON and is what tests and the simulator use.

## Run locally (10 commands)
```bash
cp .env.example .env                       # fill WA_* and the test phones
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install pytest pytest-asyncio respx ruff
python -m playwright install chromium
python scripts/make_product_images.py && python scripts/make_screen_image.py
pytest -q                                  # 110 tests: unit, golden path, red team, cards, try-on
python -m chanakya.sim --script tests/golden/script.txt --render   # golden path, cards saved to ./sim_out
uvicorn chanakya.app.main:app --reload --port 8000
ngrok http --url=<your-static-domain> 8000 # set https://<domain>/webhook + WA_VERIFY_TOKEN in Meta
# send "Namaste" from the associate phone
```
SQLite is the default locally. In production, set `DATABASE_URL=postgresql://…`; it is converted to `postgresql+asyncpg://`. Tables are created at startup and the seed loads if `products` is empty. Set `TEST_DATABASE_URL` to run the tests on Postgres.

## Deploy on Render
`render.yaml` describes one always-on Docker web service (Starter plan) and a managed Postgres 16, both in Singapore.
1. In the Render dashboard, choose **New → Blueprint** and pick this GitHub repo.
2. When asked, type the `sync: false` values yourself: `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`, `WA_TEST_NUMBER`, `GEMINI_API_KEY` and the test phones. `WA_VERIFY_TOKEN` and `ADMIN_TOKEN` are generated for you. Leave `AI_MODE` as `auto`.
3. Once the deploy is live, open `https://<service>.onrender.com/health`. It should show `"ok": true`.
4. In Meta → WhatsApp → Configuration → Webhook, set the callback URL to `https://<service>.onrender.com/webhook`. Use the generated `WA_VERIFY_TOKEN` (from the service's Environment page) and subscribe to `messages`.

5. **Required on new Meta apps:** subscribe your app to the test number's WhatsApp Business Account. Until you do, the account forwards real messages only to Meta's internal "WA DevX Webhook Events 1P App": the dashboard **Test** button reaches Chanakya, but real messages never do. Run this once and enter the token at the hidden prompt:
   ```bash
   python scripts/wa_check.py --subscribe
   ```
   It reads the account ID from the phone number (the ID in the Business Settings URL is a different one), subscribes the app, and checks that **YOUR app is subscribed ✓**. `/health` then shows each real message under `delivery`.

Every push to `main` redeploys automatically.

## Using it on WhatsApp
- **First message from a staff phone:** Chanakya asks the language (English / हिंदी / मराठी). Everything it tells that person stays in that language. Only the "Bolo" line to read out follows the customer's language, which is chosen per customer. To change it later, send `language`.
- **The 9 steps follow the spec:** Namaste (brief) → Naya customer → voice note → questions → photos (label, room) → Options → Dikha diya ✓ → choose → Kharcha → Offers → Parivaar Card (QR consent) → task → Khatam (score).
- **`catalog`** shows the store's models for the current category, e.g. `catalog ac`. Prices appear only outside a customer conversation, or after the demo.
- **Product questions** such as "Meru 55 mein Dolby Atmos hai?" are answered only from the database. If the data doesn't have the answer, the question goes to the expert.
- **Room photo with a TV already on the wall:** Chanakya asks that TV's size once (the most accurate scale), then draws the new TVs to true size.

## Simulator
`python -m chanakya.sim` starts an interactive session. The commands are `as <role>`, `say`, `voice <fixture>`, `photo`, `tap`, `pick`, `clock`, `dump` and `quit`. Add `--rules` to use the offline understanding instead of fixtures, and `--render` to save card images.

## Architecture
```
webhook (HMAC verify, 200 in <100 ms) → orchestrator: dedupe → read+typing → per-sender lock
  → router (role · commands · button ids · intents) → skill (9 steps + extras)
  → validator (≤3 lines · ≤3 buttons · titles ≤20 · Kyun cites a Home Card slot · number firewall · banned phrases)
  → sender (card → Playwright JPEG → upload → interactive header) → persist events/session → background jobs (try-on)
scheduler (20 s tick, demo-clock aware): task reminders · install D−1 / check / D+7 · expert SLA · 21:00 digest · media cleanup
```
| Folder | What it holds |
|---|---|
| `truth/` | ORM, seed loader, FactsService, Hisaab (full cost), offers, finance, number firewall, INR formatting |
| `brain/` | packs (YAML: tv, ac, wm, laptop, common) + fit, options, next-best question, demo picker |
| `skills/` | brief, assess, validate, translate, recommend, complete, close, follow, review, customer, manager, expert, visitlog, try-on glue |
| `render/` | 8 Jinja2 card templates → Playwright (Pillow fallback) |
| `tryon/` | Ghar Mein Dekho: blur people → scale from geometry → to-scale composite → verify → labels |

## Safety
- Every number shown comes from a data row, a registered calculation, the Home Card or an expert's answer. The validator blocks anything else and falls back to a safe reply.
- Price stays locked until the demo and at least 4 home facts. The associate can override with one tap; every override is logged.
- Start level (budget) is set only from what the associate says or taps. Room photos are read for geometry and light only, and people in them are blurred.
- Nothing goes to a customer without consent (QR opt-in or tap). A customer can reply STOP.

## Admin and demo
- `GET /health` reports DB, renderer and pack status.
- `GET /admin/sessions` and `GET /admin/events?session=` need the `X-Admin-Token` header. So does `POST /admin/reset`.
- Demo commands from allowed phones: `/role manager|expert|visitor|associate`, `/clock +1d|install|+7d|reset`, `reset`.

See `CONTRACTS.md`, `docs/design-note.md` and the build specification (shared separately).
