"""Price watch: after every catalogue sync, the products a customer left without buying (the last 14 days) are
checked against the backend. If one is now cheaper, or a live offer now applies to it, the associate gets one
nudge to call the customer back, with the numbers from the data. Each change is told once."""

from __future__ import annotations

from datetime import datetime, timedelta

from chanakya.core import repo
from chanakya.core.contracts import Button, OutMessage
from chanakya.core.status import short_name
from chanakya.i18n.lang import t
from chanakya.log import get_log
from chanakya.truth import fmt
from chanakya.truth.firewall import NumberRegistry

log = get_log("price_watch")
WINDOW_DAYS = 14
MIN_DROP = 0.01  # a drop of at least 1% (rounding noise is not news)


def _live_offers(facts, p, today) -> list[str]:
    return sorted(o.id for o in facts.snap.offers
                  if p.category in (o.categories or []) and o.valid_from <= today <= o.valid_to
                  and (o.min_amount or 0) <= p.price and not any(k.startswith("with_tv") for k in (o.filter or {})))


def remember(s, p, facts, today) -> None:
    """What the customer saw: the price and the live offers (when the options or the cost are shown)."""
    s.extra.setdefault("seen_prices", {})[p.sku] = p.price
    s.extra.setdefault("seen_offers", {})[p.sku] = _live_offers(facts, p, today)


async def run(svc, now: datetime) -> int:
    facts = svc.facts
    today = now.date()
    sent = 0
    for _row, s in await svc.sessions.since(now - timedelta(days=WINDOW_DAYS)):
        if s.extra.get("outcome") == "sold" or not s.associate_wa_id or not s.extra.get("seen_prices"):
            continue
        skus = [s.chosen_sku] if s.chosen_sku else list(s.option_labels.values())
        changed = False
        for sku in skus:
            p = facts.product_sync(sku)
            was = (s.extra.get("seen_prices") or {}).get(sku)
            if p is None or was is None or not (p.qty > 0 or p.nearby_qty > 0):
                continue
            offers_was = set((s.extra.get("seen_offers") or {}).get(sku) or [])
            offers_now = _live_offers(facts, p, today)
            new_offers = [o for o in offers_now if o not in offers_was]
            drop = was - p.price if p.price < was * (1 - MIN_DROP) else 0
            if not drop and not new_offers:
                continue
            staff = await repo.staff_by_wa(s.associate_wa_id)
            lang = (staff.lang if staff else None) or "hi"
            reg = NumberRegistry()
            code = reg.raw(s.code, "session:code")
            name = reg.raw(short_name(s.home.customer_name), "card:name") if s.home.customer_name else ""
            model = reg.raw(p.model, f"product:{sku}")
            if drop:
                body = t("pw_drop", lang, reg, code=code, name=name, model=model, was=reg.inr(was, f"seen:{sku}"),
                         now=reg.inr(p.price, p.price_source), diff=reg.inr(drop, f"calc:drop:{sku}"))
            else:
                o = next(x for x in facts.snap.offers if x.id == new_offers[0])
                body = t("pw_offer", lang, reg, code=code, name=name, model=model,
                         offer=reg.raw((o.title or {}).get("en") or o.id, f"offer:{o.id}"),
                         till=reg.raw(fmt.date_short(o.valid_to, lang), f"offer:{o.id}:valid_to"))
            msg = OutMessage(to=s.associate_wa_id, kind="buttons", body=body, session_id=s.id,
                             buttons=[Button(id=f"s:{s.code}:called", title=t("b_called", lang)[:20]),
                                      Button(id=f"s:{s.code}:task", title=t("b_task", lang)[:20])],
                             allowed_numbers=reg.allowed())
            try:
                await svc.deliver(svc.validate(msg, reg, None, lang, None))
                sent += 1
            except Exception:
                log.exception("price_watch_send_failed")
            s.extra["seen_prices"][sku] = p.price
            s.extra.setdefault("seen_offers", {})[sku] = offers_now
            changed = True
        if changed:
            await svc.sessions.save(s, now)
    if sent:
        log.info("price_watch_sent", n=sent)
    return sent
