# Chanakya Live: the whisper copilot on the shop floor

*Product and technical plan. Status: v0 built (`chanakya/live/`, page at `/live`), off by default (`LIVE_ENABLED`).*

## 1. The idea in one line

Today, the associate talks to the customer and then reports to Chanakya on WhatsApp. With Live, Chanakya **listens to
the conversation itself**, quietly, and the associate never has to look away from the customer:

- it fills in the Home Card from what is said;
- it picks A / B / C from today's stock;
- it shows **one line at a time**: what to ask, show or say next.

It uses the same brain, packs, fit rules, wants, offers and data-only rules as the WhatsApp agent. Nothing is
invented.

## 2. Who gets what

| | Customer | Associate | Manager |
|---|---|---|---|
| **Value** | Only questions that matter for *their* home, and a product that fits. Offers they would have missed, and no pushy upsell. | No typing and no voice notes to record. The next best question arrives in time, with a reason from today's stock. Objection lines come in the customer's language. | The same sessions and events as WhatsApp, for the digest and voice log. |
| **How** | The associate's eyes stay on them. A screen turn shows A/B/C with their own needs ticked. | A phone in hand or on the counter, an optional earbud for the whisper, Hindi / English / Marathi. | No change in v0. Later: live floor view. |

## 3. Principles (the "non-intrusive" contract)

1. **One cue at a time.** A single whisper line on screen, spoken in the earbud if earbud mode is on. It is never a
   list, and the same cue never appears twice per customer (as with the WhatsApp copilot `tips_shown`).
2. **Facts only from tools.** The live model *hears*. Facts, prices, offers and stock come from
   `chanakya.brain` and `FactsService`. The model can only report what it heard (slot ids and allowed values from
   the packs), and the server validates every value against the pack before it touches the Home Card.
3. **Consent first.** The mic starts only after the associate taps *"Customer agreed"*, after a one-line consent
   script in the customer's language. Audio is never stored. Only the transcript lines and the Home Card are kept
   in memory for the session.
4. **It always works.** Providers fail over automatically, from Gemini Live to OpenAI Realtime to on-device speech
   recognition (the browser's Web Speech API, no key). Typing a line works too. The engine behind them is
   deterministic.
5. **The associate is the seller.** Cues say *what to ask or show and why*. Words for the customer are labelled
   *Say:* and come from the packs' `line` / `ask` texts.

## 4. The experience, moment by moment

| Moment | What Live does (from the data) |
|---|---|
| **Greet** | Consent card, language choice, then "Listening". The whisper reads: *"Ask: which room is it for?"* (the pack's `ask_order`, skipping questions the stock can't use, from `data_questions.skip_slots`). |
| **Needs** | "Hall mein lagega, sofa se das feet, dhoop aati hai" adds three ticks to the Home Card (room, 10 ft, sunny). The next question jumps to what is still unknown. |
| **Wants** | "55 inch Sony chahiye" becomes hard wants (`brain.wants.parse`). A/B/C are re-picked with products that meet them first, and each card shows *"55 inch ✓ as asked"*. |
| **Budget** | "50 hazaar tak": if nothing that suits this home fits, the whisper says *"Nothing in stock that suits this home is under ₹50,000; the lowest is ₹54,990. Talk size or EMI first."* |
| **Options** | A / B / C appear as soon as the gate is met, with price, MRP saving and the top live offer with its last day. The whisper names the one fact that matters: *"Only B meets 'Bright enough for the sunny room'"* or *"B's HDFC offer ends Fri"*. |
| **Objection** | "Online sasta hai" makes the whisper switch to the pack's line in the customer's language: *"Say: True, online prices can look lower…"*. |
| **Old product** | "Purana TV hai" with exchange live in the data: *"Ask about the old TV: exchange is live for TVs."* |
| **Family decides** | *"Send the Family Card on WhatsApp"*, with a one-tap hand-off: the same Home Card continues on WhatsApp. |

## 5. Architecture

```
Associate phone (web page /live, PWA-able)
  mic → AudioWorklet (PCM16) ─┐               ┌─ Gemini Live (BidiGenerateContent, input transcription + tool calls)
  Web Speech (fallback) ──────┤ WebSocket     ├─ OpenAI Realtime (transcription session, gpt-4o-transcribe)
  typed line ─────────────────┤ /live/ws ──── └─ none: browser speech text
                              ▼
                    chanakya.live.engine.Floor  (one per customer)
                      rules.understand_text → slots, numbers, budget, objection, myth
                      wants.parse            → hard wants
                      options.compute_options→ A/B/C (fit + wants + band)
                      product_view.live_offers_for → offers
                      cues.pick              → ONE whisper line
                    ◄── view (JSON) pushed to the page after every utterance
```

- **Keys stay on the server.** The browser never sees a Gemini or OpenAI key. The server bridges the audio.
- **Model role:** a realtime *listener*. Gemini Live is configured with TEXT output and input transcription. A tool
  `heard(slots, numbers, budget_inr)` lets it report structured facts the keyword rules miss (for example "papa
  cricket dekhte hain"). Each one is validated against the pack before use. OpenAI is used in transcription mode
  only, and the same engine reads the text.
- **Failover:** if the server provider errors or goes quiet for 8 s while audio flows, the page is told
  `{"type":"provider","name":"browser"}` and switches to Web Speech with no gap. This addresses the 3 Nov Gemini
  503 incident.
- **Memory (Render 512 MB):** each Floor holds a few KB. The audio is streamed through and never buffered beyond a
  frame. At most `LIVE_MAX_SESSIONS` sessions at once (default 4).

## 6. Settings

| Env | Default | Meaning |
|---|---|---|
| `LIVE_ENABLED` | `false` | `/live` and `/live/ws` return 404 when off |
| `LIVE_KEY` | `""` | if set, the page needs `?k=<key>` (share per store) |
| `LIVE_PROVIDER` | `auto` | `auto` (Gemini, then OpenAI, then browser), `gemini`, `openai` or `browser` |
| `GEMINI_LIVE_MODEL` | `gemini-live-2.5-flash-preview` | check the current Live model name in AI Studio before turning it on |
| `OPENAI_API_KEY` | `""` | enables the OpenAI Realtime path (you enter it in Render) |
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-4o-transcribe` | |

## 7. Risks and how they are handled

| Risk | Handling |
|---|---|
| Consent / DPDP | Explicit consent tap, the script in the customer's language, no audio stored, transcript in memory only, the "Stop" button always visible |
| Shop-floor noise | The phone close to the conversation. Short utterances. Hints from the slot keywords. A typed line or quick chips as a fallback. |
| Wrong facts | The model cannot set a value outside the pack's lists. Every number shown comes from the data. |
| Cost | Gemini Live / OpenAI transcription are billed per audio minute. A 10-minute conversation is a few rupees for transcription-only. Browser mode is free. |
| Latency | Cues are computed locally in under 50 ms after each utterance. Speech-to-text is about 0.3–1.5 s. |
| Provider outage | Automatic failover to on-device speech |

## 8. Roll-out

1. **v0 (built):**
   - the engine, cues, the page, the three providers, and tests;
   - turned on with `LIVE_ENABLED=true` on a staging service first.
2. **Pilot (2 weeks, 1 store, 2 associates), measured against WhatsApp-only:**
   - questions per sale;
   - time to options;
   - conversion;
   - offer attach;
   - how often cues are used (associate taps *Done*);
   - provider failovers.
3. **v1:**
   - hand-off to WhatsApp (Family Card, cost, consent) from the same session;
   - events into the manager digest;
   - earbud voice from a realtime voice in the associate's language;
   - a live floor view for the manager.
