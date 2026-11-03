"""Step 8 — Rishta (Follow through): consent (QR), Parivaar Card, tasks, installation job."""

from __future__ import annotations

import base64
import io
import random
import urllib.parse
from datetime import datetime, timedelta

import qrcode
from PIL import Image, ImageDraw

from chanakya.brain import packs as P
from chanakya.core import repo, state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import CUSTOMER_PHONE_LANG, Button, Card, OutMessage, Reply, Step
from chanakya.core.status import short_name
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext
from chanakya.truth import fmt

SLOT_TIMES = {"t1": "10–1", "t2": "2–5", "d2": "10–1"}
INSTALL_SLOTS = {  # id → (label key, day offset, start hour, end hour)
    "t1": ("b_slot_t1", 1, 10, 13),
    "t2": ("b_slot_t2", 1, 14, 17),
    "d2": ("b_slot_d2", 2, 10, 13),
}
TASK_WHEN = {"today_evening": "b_task_eve", "tomorrow": "b_task_tmr", "weekend": "b_task_wkd"}


def needs_install(ctx: SkillContext) -> bool:
    """AC, washing machine and a wall-mounted TV need an installer; a fridge or a TV on a stand is delivered."""
    s = ctx.session
    cat = s.home.category
    if cat in ("ac", "wm"):
        return True
    return cat == "tv" and "wall" in P.values(s.home, "mounting")


def slot_choices(ctx: SkillContext) -> dict[str, tuple[str, object, int, int]]:
    """id → (button label, date, start h, end h). From the delivery day when one was picked (never before it);
    else from today. Labels name the date when it is not simply tomorrow / the day after."""
    from datetime import date as _date

    s = ctx.session
    today = ctx.now.date()
    base = today + timedelta(days=1)
    iso = ctx.x.get("delivery_choice")
    paused = bool(s.extra.get("install_paused"))
    if iso and not paused:
        try:
            base = max(base, _date.fromisoformat(iso))
        except ValueError:
            pass
    out = {}
    for sid, (key, days, h1, h2) in INSTALL_SLOTS.items():
        d = base + timedelta(days=days - 1)
        if base == today + timedelta(days=1):
            label = ctx.t(key)
        else:
            label = ctx.reg.raw(f"{fmt.date_short(d, ctx.lang)} {h1 if h1 <= 12 else h1 - 12}–{h2 - 12 if h2 > 12 else h2}",
                                "choice:install_slot")
        out[sid] = (label, d, h1, h2)
    return out


async def parivaar(ctx: SkillContext, note: str | None = None, presale: bool = False) -> Reply:
    """Consent first (QR), then the Parivaar Card. presale=True: the customer will ask the family before buying —
    the same card goes now (recommendation, full cost, offers), so the sale stays alive at home."""
    s = ctx.session
    if presale:
        ctx.x["presale_parivaar"] = True
    if not s.consent or not s.customer_wa_id:
        ctx.x["pending_parivaar"] = True
        cites = C.known_cites(ctx, ["decider"])
        btns = [
            ctx.b("b_qr", ctx.sid("qr")),
            ctx.b("b_consent_tap", ctx.sid("consent_tap")),
            ctx.b("b_back", ctx.sid("go")),
        ]
        say = ctx.pick(ctx.packs.common_line("consent_ask"), ctx.clang)
        why = ctx.t("kyun_consent_note", note=note) if note else ctx.t("kyun_consent")
        if not cites:
            return ctx.reply(info_reply(ctx, f'{ctx.t("label_bolo")}: "{say}"\n{why}', btns))
        return ctx.reply(coach_reply(ctx, say, why, cites, btns))
    return await send_parivaar(ctx)


def qr_image(link: str, caption: str) -> bytes:
    q = qrcode.QRCode(box_size=10, border=2)
    q.add_data(link)
    q.make(fit=True)
    img = q.make_image(fill_color="#1B2A4A", back_color="white").convert("RGB")
    w, h = img.size
    card = Image.new("RGB", (w + 80, h + 140), "white")
    card.paste(img, (40, 40))
    d = ImageDraw.Draw(card)
    d.text((40, h + 60), caption, fill="#1B2A4A")
    d.text((40, h + 90), "TEST DATA", fill="#C0392B")
    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=90)
    return buf.getvalue()


async def show_qr(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not s.optin_token:
        s.optin_token = f"{s.code}-{random.randint(1000, 9999)}"
    settings = ctx.svc.settings
    link = f"https://wa.me/{settings.wa_test_number}?text=" + urllib.parse.quote(f"CHANAKYA {s.optin_token}")
    img = qr_image(link, "Scan -> WhatsApp -> Send")
    ctx.x["pending_parivaar"] = True
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="image",
            image_bytes=img,
            body=ctx.t("consent_qr_caption"),
            allowed_numbers=ctx.reg.allowed(),
        )
    )


async def consent_tap(ctx: SkillContext) -> Reply:
    phone = ctx.svc.settings.customer_phone
    last = await repo.last_inbound_at(phone) if phone else None
    if not phone or not last or (ctx.now - last) > timedelta(hours=24):
        r = await show_qr(ctx)
        r.messages.insert(0, info_reply(ctx, ctx.t("consent_tap_needs_qr")))
        return r
    return await record_consent(ctx, phone, "tap")


async def record_consent(ctx: SkillContext, customer_wa_id: str, method: str) -> Reply:
    """Shared by the QR opt-in (customer role) and the tap fallback. ctx.session is the associate's session."""
    s = ctx.session
    s.customer_wa_id = customer_wa_id
    s.consent = {"method": method, "at": ctx.now.isoformat(), "customer_wa_id": customer_wa_id}
    ctx.ev("consent_given", method=method)
    staff = await repo.staff_by_wa(s.associate_wa_id)
    alang = staff.lang if staff else "hi"
    name = s.home.customer_name or ctx.t("customer_word", alang)
    msgs = []
    # to the customer
    cust_body = "\n".join(
        [
            ctx.t("cust_welcome", CUSTOMER_PHONE_LANG, associate=staff.name if staff else "Chanakya"),
            ctx.t("cust_photo_hint", CUSTOMER_PHONE_LANG),
        ]
    )
    msgs.append(OutMessage(to=customer_wa_id, kind="text", body=cust_body))
    # to the associate
    msgs.append(
        OutMessage(
            to=s.associate_wa_id,
            kind="text",
            body=ctx.t(
                "consent_done",
                alang,
                name=ctx.reg.raw(short_name(name), "card:name"),
                time=ctx.reg.raw(fmt.time_short(ctx.now), "clock:time"),
            ),
        )
    )
    if s.extra.pop("pending_parivaar", False):
        r = await send_parivaar(ctx, assoc_lang=alang)
        msgs += r.messages
    return ctx.reply(*msgs)


def _reasons(ctx: SkillContext, sku: str, lang: str) -> list[str]:
    from chanakya.skills.recommend import _reason
    from chanakya.skills.translate import matching

    p = ctx.facts.product_sync(sku)
    pack = ctx.pack
    out = []
    for b in matching(ctx, p)[:3]:
        out.append(ctx.reg.static(ctx.pick(b.card, lang)))
    if len(out) < 3:
        from chanakya.brain.fit import fit

        for c in sorted(
            fit(p, ctx.session.home, pack, ctx.facts.snap.guides).contributions, key=lambda c: -(c.weight * c.match)
        ):
            if c.match >= 1 and c.rule_id in pack.reasons:
                r = _reason(ctx, c.rule_id, lang)
                if r not in out:
                    out.append(r)
            if len(out) >= 3:
                break
    return out[:3]


def _store(ctx: SkillContext, staff) -> str:
    return ctx.facts.store_name(staff.store_id if staff else None).replace(" (TEST)", "")


async def family_card(ctx: SkillContext, p, presale: bool, image, is_photo: bool, staff, name: str) -> Card:
    """The Family Card, one English card for the family at home: their room with the product, why it is right
    for THEIR home (each benefit with the spec that proves it), one clear price (one final number, the same as
    on the bill and the offer card), and what happens next. Every number from the data or the snapshots."""
    from chanakya.skills import fitview as FV

    s = ctx.session
    lang = "en"
    with ctx.as_lang(lang):
        fam = short_name(name) if name else ""
        points = FV.points(ctx, p, lang, 3)
        if len(points) < 3:
            for r in _reasons(ctx, p.sku, lang):
                if all(r != x["text"] for x in points) and len(points) < 3:
                    points.append({"text": r, "proof": None})
        ladder, total, total_label, optional, emi = [], None, None, None, None
        cs = s.cost_snapshot if (s.cost_snapshot or {}).get("sku") == p.sku else None
        os_ = s.offer_snapshot if (s.offer_snapshot or {}).get("sku") == p.sku else None
        if cs:
            ladder.append({"label": ctx.t("fc_price"), "value": ctx.reg.inr(p.price, p.price_source)})
            extras = int(cs["zaroori_total"]) - int(p.price)
            if extras > 0:
                ladder.append({"label": ctx.t("fc_essentials"), "value": "+" + ctx.reg.inr(extras, f"snapshot:hisaab:{p.sku}")})
            total, total_label = ctx.reg.raw(cs["zaroori_total_str"], f"snapshot:hisaab:{p.sku}"), ctx.t("fc_full_cost")
            if os_ and os_.get("applied"):
                from datetime import date

                for a in os_["applied"][:2]:
                    till = fmt.date_short(date.fromisoformat(a["valid_to"]), lang)
                    ladder.append({"label": ctx.t("fc_offer", title=ctx.reg.static(_offer_title(ctx, a["id"], lang)),
                                                  till=ctx.reg.raw(till, f"offer:{a['id']}:valid_to")),
                                   "value": "−" + ctx.reg.raw(a["amount_str"], f"offer:{a['id']}"), "offer": True})
                total = ctx.reg.raw(os_.get("total_today_str") or os_["payable_str"], "snapshot:total_today")
                total_label = ctx.t("fc_pay_today")
            sug = int(cs["with_suggestions_total"]) - int(cs["zaroori_total"])
            if sug > 0 and not s.extra.get("suggestions_off"):
                optional = ctx.t("fc_optional", amount=ctx.reg.inr(sug, f"snapshot:hisaab:{p.sku}"))
            if os_ and os_.get("finance"):
                from chanakya.skills.close import finance_sentence
                from chanakya.truth.finance import FinanceQuote

                q = FinanceQuote.model_validate(os_["finance"])
                for v in (q.monthly_str, q.fee_str, q.total_str, q.extra_str, q.months_str):
                    ctx.reg.raw(v, f"snapshot:finance:{q.plan_id}")
                emi = finance_sentence(ctx, q, lang)
        steps = []
        if s.extra.get("delivery_choice"):
            from datetime import date

            d = date.fromisoformat(s.extra["delivery_choice"])
            steps.append({"icon": "truck", "k": ctx.t("fc_delivery"),
                          "v": ctx.reg.raw(f"{fmt.day_name(d, lang)} {fmt.date_short(d, lang)}", "choice:delivery")})
        if s.extra.get("install_job_id"):
            job = await repo.install_get(s.extra["install_job_id"])
            if job and job.status != "cancelled":
                steps.append({"icon": "calendar", "k": ctx.t("fc_install"),
                              "v": ctx.reg.raw(f"{fmt.date_short(job.slot_date, lang)} · {job.slot_label}", f"install:{job.id}")})
        w = (p.info or {}).get("warranty") or {}
        wt = (p.info or {}).get("warranty_text")
        if isinstance(w, dict) and w.get("years"):
            steps.append({"icon": "shield", "k": ctx.t("fc_warranty"),
                          "v": ctx.t("fc_warranty_years", n=ctx.reg.num(w["years"], f"product:{p.sku}:warranty"))})
        elif wt:
            steps.append({"icon": "shield", "k": ctx.t("fc_warranty"), "v": ctx.reg.raw(str(wt)[:40], f"product:{p.sku}:warranty")})
        aname = staff.name if staff else ""
        return Card(
            template="parivaar",
            lang=lang,
            data={  # type: ignore[arg-type]
                "kicker": ctx.t("fc_kicker_presale" if presale else "fc_kicker", name=fam) if fam else ctx.t("fc_kicker_you"),
                "title": ctx.t("fc_title", product=C.product_title(ctx, p, lang)),
                "image": image,
                "image_is_photo": is_photo,
                "key_specs": PV.key_specs(ctx, p, lang, 4),
                "points": points,
                "ladder": ladder,
                "total": total,
                "total_label": total_label,
                "optional": optional,
                "emi": emi,
                "no_price": None if total else ctx.t("fc_price_at_store"),
                "steps": steps,
                "associate_name": aname,
                "store_name": _store(ctx, staff),
                "reply_line": ctx.t("fc_reply", associate=aname) if aname else None,
                "test_data": bool(getattr(p, "test_data", False)),
            },
        )


async def send_parivaar(ctx: SkillContext, assoc_lang: str | None = None) -> Reply:
    s = ctx.session
    lang = "en"  # the message to the customer's phone is in English, like every customer-facing visual
    alang = assoc_lang or ctx.alang
    sku = s.chosen_sku or state.best_sku(s)
    if not sku:
        return ctx.reply(info_reply(ctx, ctx.t("parivaar_need_product", alang)))
    p = ctx.facts.product_sync(sku)
    name = s.home.customer_name or ""
    presale = bool(s.extra.get("presale_parivaar")) and s.extra.get("outcome") != "sold"
    image: str | None = p.image
    hero = await repo.media_latest(s.id, "tryon_hero")
    if hero and s.extra.get("tryon_hero_sku") == sku:  # their own room, with this product in it
        image = "data:image/jpeg;base64," + base64.b64encode(hero.data).decode()
    staff = await repo.staff_by_wa(s.associate_wa_id)
    card = await family_card(ctx, p, presale, image, bool(hero and s.extra.get("tryon_hero_sku") == sku), staff, name)
    s8 = s.id[:8]
    aname = staff.name if staff else "Chanakya"
    from chanakya.channel.limits import truncate_title

    btns = [
        Button(id=encode("u", s8, "talk"), title=truncate_title(ctx.t("cb_talk", lang, associate=aname))),
        Button(id=encode("u", s8, "visit"), title=ctx.t("cb_visit", lang)),
        Button(id=encode("u", s8, "ask"), title=ctx.t("cb_question", lang)),
    ]
    caption = ctx.t("parivaar_caption", lang, name=short_name(name) if name else "", product=C.product_name(ctx, p))
    to_customer = OutMessage(
        to=s.customer_wa_id, kind="buttons", body=caption, buttons=btns, card=card, allowed_numbers=ctx.reg.allowed()
    )
    s.extra["parivaar_sent"] = True
    state.advance(s, Step.FOLLOW)
    ctx.ev("parivaar_sent", to="customer")
    nm = ctx.reg.raw(short_name(name) if name else ctx.t("customer_word", alang), "card:name")
    if presale:  # not bought yet: the next step is the follow-up task
        abtns = [Button(id=ctx.sid("task"), title=ctx.t("b_task", alang)),
                 Button(id=ctx.sid("voice_add"), title=ctx.t("b_voice_add", alang)),
                 Button(id=ctx.sid("khatam"), title=ctx.t("b_khatam", alang))]
        body = ctx.t("parivaar_sent_presale", alang, name=nm)
    else:  # sold: installation (when needed), then a task, then the score
        first = (Button(id=ctx.sid("sold"), title=ctx.t("b_install", alang))
                 if needs_install(ctx) and not s.extra.get("install_job_id")
                 else Button(id=ctx.sid("voice_add"), title=ctx.t("b_voice_add", alang)))
        abtns = [first, Button(id=ctx.sid("task"), title=ctx.t("b_task", alang)),
                 Button(id=ctx.sid("khatam"), title=ctx.t("b_khatam", alang))]
        body = ctx.t("parivaar_sent", alang, name=nm)
    to_assoc = OutMessage(to=s.associate_wa_id, kind="buttons", body=body, buttons=abtns)
    return ctx.reply(to_customer, to_assoc)


def _offer_title(ctx: SkillContext, offer_id: str, lang: str) -> str:
    from chanakya.i18n.lang import pick

    o = next((o for o in ctx.facts.snap.offers if o.id == offer_id), None)
    return pick(o.title, lang) if o else offer_id


async def voice_add(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not s.customer_wa_id:
        return await parivaar(ctx)
    s.awaiting = "voice_for_parivaar"
    return ctx.reply(info_reply(ctx, ctx.t("voice_add_prompt")))


async def forward_voice(ctx: SkillContext) -> Reply:
    s = ctx.session
    s.awaiting = None
    data, mime = await ctx.svc.channel.download_media(ctx.inbound.media_id)
    mid = await ctx.svc.channel.upload_media(data, mime.split(";")[0] or "audio/ogg", "voice.ogg")
    return ctx.reply(
        OutMessage(to=s.customer_wa_id, kind="audio", media_id=mid, voice=True),
        info_reply(ctx, ctx.t("voice_sent"), [ctx.b("b_task", ctx.sid("task")), ctx.b("b_khatam", ctx.sid("khatam"))]),
    )


async def task_menu(ctx: SkillContext) -> Reply:
    btns = [ctx.b(key, ctx.sid("task_when", when)) for when, key in TASK_WHEN.items()]
    return ctx.reply(info_reply(ctx, ctx.t("task_when_ask"), btns))


def _due(now: datetime, when: str) -> datetime:
    if when == "today_evening":
        d = now.replace(hour=19, minute=0, second=0, microsecond=0)
        return d if d > now else now + timedelta(hours=1)
    if when == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    days = (5 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=11, minute=0, second=0, microsecond=0)


async def create_task(ctx: SkillContext, when: str) -> Reply:
    s = ctx.session
    due = _due(ctx.now, when)
    name = s.home.customer_name or s.code
    title = {lg: ctx.t("task_title", lg, name=name) for lg in ("hi", "mr", "en")}
    t = await repo.task_create(ctx.actor_wa_id, "followup", title, due, s.id, {"code": s.code})
    ctx.ev("task_created", task_id=t.id, kind="followup", owner=ctx.actor_wa_id, due_at=due.isoformat())
    s.extra["task_created"] = True
    label = ctx.t(TASK_WHEN.get(when, "b_task_tmr"))
    btns = [ctx.b("b_khatam", ctx.sid("khatam")), ctx.b("b_new_customer", encode("g", "new"))]
    return ctx.reply(info_reply(ctx, ctx.t("task_done", when=label), btns))


async def sold(ctx: SkillContext) -> Reply:
    ctx.x["outcome"] = "sold"
    if needs_install(ctx) and not ctx.x.get("install_job_id"):
        pending = [sid for sid in ctx.pack.site_needs if not P.known(ctx.session.home, sid) and ctx.pack.slot(sid)]
        if pending:  # what the installer must know (one tap each), before the slot
            ctx.x["pending_install"] = True
            return ctx.reply(C.question_reply(ctx, pending[0]))
        ctx.x.pop("pending_install", None)
        return install_menu(ctx)
    from chanakya.skills import review

    return await review.finish(ctx)


def install_menu(ctx: SkillContext) -> Reply:
    btns = [Button(id=ctx.sid("islot", sid), title=label[:20]) for sid, (label, *_r) in slot_choices(ctx).items()]
    for b in btns:
        ctx.reg.static(b.title)
    return ctx.reply(info_reply(ctx, ctx.t("install_slot_ask"), btns))


def site_summary(ctx: SkillContext) -> tuple[dict, str, list[str]]:
    s = ctx.session
    pack = ctx.pack
    needs = {}
    parts = []
    for sid in pack.site_needs:
        sd = pack.slot(sid)
        vals = P.values(s.home, sid)
        v = vals[-1] if vals else "unknown"
        needs[sid] = v
        if sd:
            parts.append(f"{ctx.pick(sd.title)}: {sd.value_title(v, ctx.lang) if vals else '?'}")
    risks = []
    if (
        pack.category == "tv"
        and "wall" in P.values(s.home, "mounting")
        and needs.get("wall_type") in ("panel", "unknown")
    ):
        risks.append("wall_type")
    if needs.get("socket_near") == "no" or needs.get("socket_15a") == "no":
        risks.append("socket")
    if needs.get("wall_hole") == "needed" and not any(
        ln.get("code") == "AC_DRILL" for ln in (s.cost_snapshot or {}).get("lines", [])
    ):
        risks.append("drilling_not_in_cost")
    p = ctx.facts.product_sync(s.chosen_sku) if s.chosen_sku else None
    if needs.get("floor_lift") == "stairs" and p and (p.spec("weight_kg") or 0) > 25:
        risks.append("stairs_heavy")
    return needs, " · ".join(parts), risks


async def create_install(ctx: SkillContext, slot_id: str) -> Reply:
    s = ctx.session
    key = INSTALL_SLOTS.get(slot_id, INSTALL_SLOTS["t1"])[0]
    label, slot_date, h1, h2 = slot_choices(ctx).get(slot_id) or slot_choices(ctx)["t1"]
    needs, summary, risks = site_summary(ctx)
    sku = s.chosen_sku or state.best_sku(s)
    if sku and ctx.x.get("outcome") != "sold":  # an installation needs a sale first
        from chanakya.skills import review

        return await review.khatam(ctx)
    if not sku:  # nothing chosen yet (e.g. an old installation button): the product first
        return ctx.reply(info_reply(ctx, ctx.t("install_needs_product"), [ctx.b("b_options", ctx.sid("options"))]))
    tryon = await repo.media_latest(s.id, "tryon")
    old_job = s.extra.get("install_job_id")
    if old_job:  # a re-booking: the earlier slot is cancelled
        await repo.install_update(old_job, status="cancelled")
    s.extra.pop("install_paused", None)
    job = await repo.install_create(
        session_id=s.id,
        sku=sku,
        slot_label=label,
        slot_date=slot_date,
        site_needs={
            **needs,
            "_risks": risks,
            "_start_h": h1,
            "_end_h": h2,
            "_slot_time": SLOT_TIMES.get(slot_id, "10–1"),
            "_slot_labels": {lg: (ctx.t(key, lg) if label == ctx.t(key) else label) for lg in ("hi", "mr", "en")},
        },
        tryon_media=tryon.data if tryon else None,
        status="created",
        timeline={},
    )
    s.extra["install_job_id"] = job.id
    ctx.ev("install_job_created", job_id=job.id, slot=label)
    msgs = [
        info_reply(
            ctx,
            ctx.t(
                "install_job_body",
                id=ctx.reg.raw(str(job.id), "job:id"),
                slot=ctx.reg.static(label),
                site=ctx.reg.static(summary or "-"),
            ),
        )
    ]
    if s.customer_wa_id:
        msgs.append(
            OutMessage(
                to=s.customer_wa_id,
                kind="text",
                body=ctx.t(
                    "cust_install_booked", CUSTOMER_PHONE_LANG, slot=ctx.reg.static(ctx.t(key, CUSTOMER_PHONE_LANG))
                ),
            )
        )
    if risks:
        for m in await repo.staff_by_role("manager"):
            msgs.append(
                OutMessage(
                    to=m.wa_id,
                    kind="text",
                    body=ctx.t(
                        "install_risk_manager",
                        m.lang or "en",
                        id=ctx.reg.raw(str(job.id), "job:id"),
                        risks=", ".join(risks),
                        site=ctx.reg.static(summary),
                    ),
                )
            )
    if s.step == Step.CLOSED:  # re-booked after the conversation was scored: no second score card
        return ctx.reply(*msgs)
    from chanakya.skills import review

    r = await review.finish(ctx)
    return ctx.reply(*(msgs + r.messages))


async def task_action(ctx: SkillContext, task_id: int, done: bool) -> Reply:
    """A task reminder's [Ho gaya ✓] / [Kal yaad dilao]: acts on that task only."""
    if done:
        await repo.task_update(task_id, status="done")
        ctx.ev("task_done", task_id=task_id)
        return ctx.reply(info_reply(ctx, ctx.t("task_done_ack"), [ctx.b("b_new_customer", encode("g", "new"))]))
    tasks = [t for t in await repo.tasks_open(ctx.actor_wa_id) if t.id == task_id]
    if tasks:
        p = dict(tasks[0].payload or {})
        p.pop("reminded", None)
        await repo.task_update(task_id, due_at=ctx.now + timedelta(days=1), payload=p)
    return ctx.reply(info_reply(ctx, ctx.t("task_snoozed")))
