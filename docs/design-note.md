# Chanakya — Store IQ · one-page design note (TEST DATA · RRVL / Jio Platforms / Fynd internal)

**1. Problem.** We scored nine store conversations, six at Reliance Digital and three at competitors. Reliance Digital conversations had a median of 6 out of 14, against a best of 13. Our associate raised price first in 6 of 6, and the median first price came at minute 7.5. Associates asked about two questions before showing a product, and nobody checked that the AC actually worked after installation.

**2. Principle.** Chanakya holds the facts; the associate owns the conversation. Every coaching reply has three parts:
- **Bolo:** one line in the customer's language.
- **Kyun:** one reason in the associate's language, citing a Home Card fact.
- **Status:** a line such as `📍 C1 · Shah · TV · Ghar 4/7 · Demo – · Daam 🔒`.

Every message has at most 3 lines and 3 buttons.

**3. The nine steps (Niti).** Each step has one rule:
- **Taiyari** (start the day prepared): a brief card, a clip and one quiz question.
- **Ghar Samjho** (the home before the product): Chanakya picks the next-best question, the one whose answer most changes the top 3 products, and builds a Home Card.
- **Pehle Maano** (agree first, then explain): confirm-then-correct lines for customer myths, plus an honest price range if the customer asks the price early.
- **Saral Samjhao** (not specs: daily life): label photo → SKU → benefits for this home, plus what to ignore.
- **Dikhao** (tell less, show more): Value, Best and Step-up options with no prices, and the one demo that shows their difference.
- **Poora Kharcha** (full cost, no surprise): the cost charge by charge, plus AC/WM running cost. Site needs that change the cost are asked first.
- **Daam Aakhir Mein** (price last, with the truth): only offers in the data, and finance stated in one sentence with its full extra cost.
- **Rishta** (after the sale, the relationship begins): QR consent, the Parivaar Card to the family, a task with an owner, and an installation job with site needs and risk flags.
- **Seekh** (every customer, a lesson): a 7-step score, one praise and one tip.

**4. Ghar Mein Dekho.** Chanakya places the TV on a photo of the customer's own wall, to scale. Scale comes from geometry: the wall width the associate taps, a standard 205 cm door, 60 cm tiles, or the ceiling. The image model never decides size. People in the photo are blurred. If any step fails, the composite still goes out.

**5. Correct and safe.**
- A number firewall: every numeral a user sees is registered from a data row or a calculation, and anything else is replaced with a safe reply.
- A price lock, with an override that is logged.
- Banned pressure phrases ("sirf aaj", "last piece") get the honest version from the data instead.
- No budget guessing: the start level comes only from what the associate says or taps, and photos are read for space and light only.
- Consent by QR, STOP is honoured, and all data is test data. 20 red-team tests cover these rules.

**6. Fit for the floor.**
- First reply in text and buttons, with cards following.
- Pipeline time to first reply: p50 14 ms, p95 101 ms, without media download or a model.
- Associates use Hinglish, Devanagari Marathi or English; customers can get Hindi, Marathi or English.
- Every button title is 20 characters or fewer.

**7. After the sale.** The Parivaar Card goes to the decider. The installation job reaches the installer with wall, socket and floor details. The customer gets a reminder the day before and a "चालू आहे ✓" confirmation check on install day, and a problem alerts the manager. This fixes the AC story.

**8. Scope choices.** Gemini handles voice and photos, but it only extracts and classifies. Product answers are built from the database by code, and offline rules take over for typed text if the model is unavailable. Cut to the roadmap: perspective warping, credit attribution, queues and multiple instances, and live resQ/POS integration. Next: a resQ household record, credit that follows the customer, and more languages.

**9. Prediction.** With Chanakya, a new associate reaches Guide level (11 or more of 14) on the TV scenario. The golden path scores 14/14.
