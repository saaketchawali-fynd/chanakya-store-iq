"""Catalogue card: the store's models for a category, straight from the database.
Prices only outside a customer conversation, or once the demo has opened the price gate (price last)."""

from __future__ import annotations

from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Card, Reply
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext

PAGE = 6


async def show(ctx: SkillContext, category: str | None = None, page: int = 0) -> Reply:
    s = ctx.session
    cat = category if category in ctx.packs.packs else None
    if cat is None and s and s.home.category in ctx.packs.packs:
        cat = s.home.category
    if cat is None:
        cat = ctx.staff.dept if ctx.staff and ctx.staff.dept in ctx.packs.packs else "tv"
    items_all = [p for p in ctx.facts.products_sync(cat) if p.qty > 0 or p.nearby_qty > 0]
    if s and s.option_labels:  # the customer's three picks first
        picks = list(s.option_labels.values())
        items_all.sort(key=lambda p: (p.sku not in picks, picks.index(p.sku) if p.sku in picks else 0))
    page = max(0, page)
    items = items_all[page * PAGE : (page + 1) * PAGE]
    show_price = s is None or state.price_gate_open(s, ctx.pack)
    rows = []
    for p in items:
        if p.qty > 0:
            stock = ctx.t("stock_in_store", qty=ctx.reg.num(p.qty, f"stock:{p.sku}@{p.store_id}"))
        else:
            stock = ctx.t(
                "stock_nearby",
                days=ctx.reg.num(p.delivery_days, f"stock:{p.sku}:days"),
                store=ctx.reg.raw(
                    ctx.facts.store_name(p.nearby_store_id).replace("Reliance Digital ", "").replace(" (TEST)", ""),
                    "store",
                ),
            )
        rows.append(
            {
                "title": ctx.reg.raw(p.model, f"product:{p.sku}"),
                "image": p.image,
                "specs": PV.spec_strings(ctx, p, ctx.lang, 3),
                "stock_line": stock,
                "offer_badge": PV.offer_badge(ctx, p, ctx.lang),
                "price_str": ctx.reg.inr(p.price, p.price_source) if show_price else None,
            }
        )
    label = ctx.pick(ctx.packs.get(cat).label)
    n = ctx.reg.num(len(items_all), "catalog:count")
    subtitle = ctx.t("catalog_sub_price" if show_price else "catalog_sub_noprice")
    card = Card(
        template="catalog",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "title": ctx.t("catalog_title", cat=label),
            "subtitle": subtitle,
            "items": rows,
        },
    )
    btns = []
    if (page + 1) * PAGE < len(items_all):
        btns.append(ctx.b("b_more", encode("g", "catalog", cat, page + 1)))
    btns.append(ctx.b("b_options", ctx.sid("options")) if s else ctx.b("b_new_customer", encode("g", "new")))
    btns.append(ctx.b("b_status", encode("g", "status")))
    body = ctx.t("catalog_body", cat=label, n=n)
    ctx.ev("catalog_shown", category=cat, page=page, prices=show_price)
    return ctx.reply(info_reply(ctx, body, btns, card=card))
