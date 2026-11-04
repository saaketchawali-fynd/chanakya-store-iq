"""Complete it (user, 5 Nov 2026: smart cross-sell and upsell, with the best offers from the data on the combos or
individually). After they choose and the setup questions are answered, before the full cost:

  - what goes well with their product FOR THEIR HOME: the bill's own suggestions from their answers (a soundbar for
    the elders or a far sofa, a mount that states their size, a stabiliser for voltage swings, a stand for a wet
    floor, a bag for daily carrying), the suggested charges, and the protection plan for its price band;
  - each with its photo (when it is a product), why it is for them, its price and MRP saving, and its own live offer;
  - combo offers, honestly: a bundle offer whose rule names this TV, or an 'on bills over ₹X' offer the bill with the
    add-ons crosses (only items in that offer's categories count); nothing else is claimed;
  - a step up, only when the next option on their shortlist adds something for their needs.

What they add goes into the full cost (bundle discounts included in that line, said in its reason); what they turn
down is not suggested again. One English card for the customer; the message in the associate's language."""

from __future__ import annotations

from chanakya.core import state
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Card, ListRow, ListSection, Reply
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext
from chanakya.truth import fmt, hisaab
from chanakya.truth import offers as offers_engine

MAX_ROWS = 4


def _state(ctx: SkillContext) -> dict | None:
    """This product's add-on outcome, or None when the step has not happened for it yet."""
    a = ctx.x.get("addons")
    return a if isinstance(a, dict) and a.get("sku") == ctx.session.chosen_sku else None


def decided(ctx: SkillContext) -> dict | None:
    """For hisaab.compute: {added, declined} once the step happened for the chosen product."""
    return _state(ctx)


async def candidates(ctx: SkillContext) -> list[dict]:
    """[{code, title, amount, source, reason, product, kind}] what goes well with their product, best first."""
    s = ctx.session
    with ctx.as_lang("en"):
        sheet = await hisaab.compute(s.chosen_sku, s.home, ctx.facts, ctx.pack, ctx.reg, lang="en",
                                     include_suggestions=True)
    plans = {x.code: x for x in await ctx.facts.protection(ctx.pack.category)}
    mine = {}  # the same reasons in the associate's own language, for the list they tap
    if ctx.lang != "en":
        with ctx.as_lang(ctx.lang):
            mine = {ln.code: ln.reason for ln in (await hisaab.compute(s.chosen_sku, s.home, ctx.facts, ctx.pack, ctx.reg,
                                                                       lang=ctx.lang, include_suggestions=True)).lines}
    rows = []
    for ln in sheet.lines:
        if ln.kind != "sujhav":
            continue
        prod = ctx.facts.product_sync(ln.code)
        kind = "product" if prod else ("protection" if ln.code in plans else "charge")
        src = prod.price_source if prod else (f"protection:{ln.code}" if kind == "protection" else f"charge:{ln.code}")
        row = {"code": ln.code, "title": ln.title, "amount": ln.amount, "source": src, "reason": ln.reason,
               "reason_l": mine.get(ln.code) or ln.reason, "product": prod, "kind": kind}
        if kind == "protection":  # its cover and the price per day, from the plan
            pl = plans[ln.code]
            days = max(1, int(pl.years or 1) * 365)
            row.update(years=int(pl.years or 0),
                       per_day_str=ctx.reg.inr(max(1, round(pl.price / days)), f"calc:protection_day:{pl.code}"))
        rows.append(row)
    ctx.x["addons_base"] = sheet.zaroori_total  # product + required installation: the full cost before add-ons
    prot = sheet.protection
    if prot and not any(r["code"] == prot["code"] for r in rows):
        pl = next((x for x in await ctx.facts.protection(ctx.pack.category) if x.code == prot["code"]), None)
        if pl is not None:
            rows.append({"code": pl.code, "title": prot["title"], "amount": pl.price, "source": f"protection:{pl.code}",
                         "reason": None, "product": None, "kind": "protection", "years": prot["years"],
                         "per_day_str": prot["per_day_str"]})
    rows.sort(key=lambda r: {"product": 0, "charge": 1, "protection": 2}[r["kind"]])  # the ones with a photo first
    return rows[:MAX_ROWS]


async def _combos(ctx: SkillContext, main, rows: list[dict]) -> list[dict]:
    from chanakya.core.clock import today

    prods = [r["product"] for r in rows if r["product"] is not None]
    if not prods:
        return []
    seen, offs = set(), []
    for cat in {main.category, *(a.category for a in prods)}:
        for o in await ctx.facts.offers(cat, today()):
            if o.id not in seen:
                seen.add(o.id)
                offs.append(o)
    return offers_engine.combo_offers(offs, main, prods, ctx.session.home, today())


def _offer_text(ctx: SkillContext, c: dict) -> str:
    o = c["offer"]
    till = ctx.reg.raw(fmt.date_short(o.valid_to, "en"), f"offer:{o.id}:valid_to")
    return ctx.t("ao_combo", "en", offer=ctx.pick(o.title, "en"), amount=ctx.reg.inr(c["amount"], f"offer:{o.id}"),
                 till=till)


def _step_up(ctx: SkillContext, main) -> dict | None:
    """The next option on their shortlist above their pick, only when it adds something for them (from the data)."""
    from chanakya.skills.recommend import _gains

    s = ctx.session
    if not state.price_gate_open(s, ctx.pack):
        return None
    ups = [(ctx.facts.product_sync(v), lab) for lab, v in s.option_labels.items() if v != main.sku]
    ups = [(p, lab) for p, lab in ups if p is not None and p.price > main.price]
    for p, lab in sorted(ups, key=lambda x: x[0].price):
        gains = _gains(ctx, main, p)
        if gains:
            diff = p.price - main.price
            src = f"calc:upgrade:{main.sku}:{p.sku}"
            return {"label": lab, "title": C.product_name(ctx, p), "image": p.image, "sku": p.sku,
                    "diff_str": ctx.reg.inr(diff, src), "day_str": ctx.reg.inr(max(1, round(diff / (5 * 365))), src),
                    "adds": ", ".join(gains[:2])}
    return None


async def offer(ctx: SkillContext) -> Reply | None:
    """The "Complete it" message and card, or None when nothing goes with it (the step is then skipped)."""
    s = ctx.session
    main = ctx.facts.product_sync(s.chosen_sku) if s.chosen_sku else None
    if main is None or _state(ctx) is not None:
        return None
    rows = await candidates(ctx)
    with ctx.as_lang("en"):
        up = _step_up(ctx, main)
    if not rows and not up:
        ctx.x["addons"] = {"sku": main.sku, "added": [], "declined": []}
        return None
    combos = await _combos(ctx, main, rows)
    ctx.x["addons_offer"] = {"sku": main.sku, "rows": [{k: v for k, v in r.items() if k != "product"} for r in rows],
                             "bundles": {c["on"][0]: {"id": c["offer"].id, "amount": c["amount"]}
                                         for c in combos if c["kind"] == "bundle" and not c["bank"]}}
    card = _card(ctx, main, rows, combos, up)
    ctx.ev("addons_offered", sku=main.sku, items=[r["code"] for r in rows], combos=[c["offer"].id for c in combos],
           step_up=up["sku"] if up else None)
    why = ctx.t("kyun_addons")
    for c in combos[:1]:
        why += " · " + ctx.t("kyun_addons_combo", offer=ctx.pick(c["offer"].title),
                             amount=ctx.reg.inr(c["amount"], f"offer:{c['offer'].id}"))
    if up:
        why += " · " + ctx.t("kyun_addons_up", label=up["label"], diff=up["diff_str"], day=up["day_str"])
    btns = ([ctx.b("b_addons_all", ctx.sid("addons", "all"))] if rows else []) + [
        ctx.b("b_addons_pick", ctx.sid("addons", "pick")), ctx.b("b_addons_none", ctx.sid("addons", "none"))]
    say = ctx.tc("bolo_addons", product=C.product_name(ctx, main))
    return ctx.reply(coach_reply(ctx, say, why, C.known_cites(ctx), btns[:3], card=card, do="do_addons"))


def _card(ctx: SkillContext, main, rows: list[dict], combos: list[dict], up: dict | None) -> Card:
    bundle = {c["on"][0]: c for c in combos if c["kind"] == "bundle" and not c["bank"]}
    with ctx.as_lang("en"):
        items = []
        total = int(ctx.x.get("addons_base") or main.price)
        for r in rows:
            p = r["product"]
            item = {"title": ctx.reg.raw(r["title"], f"line:{r['code']}"), "why": r["reason"],
                    "price_str": ctx.reg.inr(r["amount"], r["source"]), "image": p.image if p else None,
                    "icon": {"protection": "shield", "charge": "box"}.get(r["kind"], "sparkle")}
            if r["kind"] == "protection":  # their reason (kids at home …) and the cover, per day
                cover = ctx.t("ao_protect_why", years=ctx.reg.num(r["years"], f"protection:{r['code']}"),
                              day=r["per_day_str"])
                item["why"] = f"{r['reason']} · {cover}" if r.get("reason") else cover
            if p is not None:
                if p.mrp and p.mrp > p.price:
                    item["mrp_str"] = ctx.t("ao_mrp", save=ctx.reg.inr(p.mrp - p.price, f"calc:mrp_saving:{p.sku}"))
                own = PV.live_offers_for(ctx, p)
                if own:
                    item["offer"] = ctx.pick(own[0][0].title)
                if p.sku in bundle:
                    item["combo"] = _offer_text(ctx, bundle[p.sku])
            total += r["amount"] - (bundle[p.sku]["amount"] if p is not None and p.sku in bundle else 0)
            items.append(item)
        bank_lines = [ctx.t("ao_bank", bank=c["bank"], offer=ctx.pick(c["offer"].title),
                            amount=ctx.reg.inr(c["amount"], f"offer:{c['offer'].id}"))
                      for c in combos if c["kind"] == "bill" and c["bank"]][:1]
        bill_lines = [_offer_text(ctx, c) for c in combos if c["kind"] == "bill" and not c["bank"]][:1]
        data = {"product": {"title": C.product_name(ctx, main), "image": main.image,
                            "price_str": ctx.reg.inr(main.price, main.price_source)},
                "items": items, "together": bill_lines + bank_lines,
                "total_str": ctx.reg.inr(total, f"calc:addons_total:{main.sku}") if items else None,
                "step_up": {k: v for k, v in up.items() if k != "sku"} if up else None, "chips": []}
    return Card(template="addons", lang="en", data=data)  # type: ignore[arg-type]


def _added_line(ctx: SkillContext, r: dict) -> dict:
    """A row they added, as a bill line: a bundle discount is included in the line and said in its reason."""
    b = ((ctx.x.get("addons_offer") or {}).get("bundles") or {}).get(r["code"])
    amount, reason = r["amount"], r.get("reason")
    if b:
        amount -= b["amount"]
        off = next((o for o in ctx.facts.snap.offers if o.id == b["id"]), None)
        with ctx.as_lang("en"):
            note = ctx.t("ao_bundle_in_line", offer=ctx.pick(off.title) if off else "",
                         amount=ctx.reg.inr(b["amount"], f"offer:{b['id']}"))
        reason = f"{reason} · {note}" if reason else note
    return {"code": r["code"], "title": r["title"], "amount": amount,
            "source": f"calc:addon:{r['code']}" if b else r["source"], "reason": reason}


async def choose(ctx: SkillContext, what: str) -> Reply:
    """'all' / 'none' / 'pick' (the list) / a row's code (add that one, then the rest or the full cost)."""
    from chanakya.skills.complete import show_cost

    s = ctx.session
    off = ctx.x.get("addons_offer") or {}
    if off.get("sku") != s.chosen_sku:
        return await show_cost(ctx)
    rows = off.get("rows") or []
    st = ctx.x.setdefault("addons", {"sku": s.chosen_sku, "added": [], "declined": []})
    if st.get("sku") != s.chosen_sku:
        st.update(sku=s.chosen_sku, added=[], declined=[])
    have = {x["code"] for x in st["added"]}
    if what == "pick":
        return ctx.reply(_list(ctx, [r for r in rows if r["code"] not in have], first=not have))
    if what == "all":
        st["added"] += [_added_line(ctx, r) for r in rows if r["code"] not in have]
    elif what in ("none", "done"):
        st["declined"] += [r["code"] for r in rows if r["code"] not in have]
    else:
        r = next((r for r in rows if r["code"] == what), None)
        if r is not None and r["code"] not in have:
            st["added"].append(_added_line(ctx, r))
            have.add(r["code"])
        left = [r for r in rows if r["code"] not in have]
        if left:  # one tap each: the next one, or 'that's all'
            return ctx.reply(_list(ctx, left, first=False,
                                   added=ctx.reg.raw(r["title"], f"line:{r['code']}") if r else None))
    ctx.ev("addons_chosen", sku=s.chosen_sku, added=[x["code"] for x in st["added"]], declined=st["declined"])
    return await show_cost(ctx)


def _list(ctx: SkillContext, rows: list[dict], first: bool, added: str | None = None):
    from chanakya.channel.limits import truncate, truncate_title

    out = [ListRow(id=ctx.sid("addon", r["code"]), title=truncate(r["title"], 24),
                   description=truncate(" · ".join(x for x in [r.get("reason_l") or r.get("reason"),
                                                               ctx.reg.inr(r["amount"], r["source"])] if x), 72))
           for r in rows[:8]]
    out.append(ListRow(id=ctx.sid("addons", "done"), title=ctx.t("row_addons_done"),
                       description=ctx.t("row_addons_done_d")))
    for r in out:
        ctx.reg.static(r.title + " " + (r.description or ""))
    body = ctx.t("addons_list_body") if first or not added else ctx.t("addons_more_body", item=added)
    m = info_reply(ctx, body)
    m.kind = "list"
    m.list_button = truncate_title(ctx.t("list_addons"))
    m.sections = [ListSection(rows=out)]
    return m
