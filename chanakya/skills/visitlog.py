"""Nirikshan — the store visit log by voice note (user, 5 Nov 2026: "seamless, and innovative").

A visitor (a mystery shopper) tells, by voice or text, how an associate served them. Chanakya scores the visit on
the reference's scorecard: seven steps, 0–2 each, out of 14 ("Guide" at 11+), plus who spoke about the price first
and when, the home questions before the first product, the demo, the full cost and "would I come back".

  home → a note → the scorecard card → at most 3 questions for what is missing → save → "visits so far" (the
  reference's "What the team found" table, from the logged visits, beside the October visits) → CSV export.

Steps are scored only from what the visitor said: by the AI with the rubric, or offline from the stated facts (the
price first, the demo, the questions before the first product, the full cost). Names are never stored (capitalised
person names become 'associate')."""

from __future__ import annotations

import csv
import io
import re
import statistics

from chanakya.ai.client import AIUnavailable
from chanakya.ai.schemas import VisitEntry
from chanakya.channel.limits import truncate
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, OutMessage, Reply
from chanakya.skills.base import SkillContext

STEPS = ["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
KEY_FIELDS = ["price_first_by", "demo_shown", "would_come_back"]
ASK_STEPS = ["assess", "translate", "close", "complete", "demonstrate", "validate", "follow_through"]  # most telling first
MAX_ASKS = 3  # at most three questions for what the note left out (key facts first, then steps)
GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|namaskaar|menu|help|start|हाय|नमस्ते|नमस्कार)\b", re.I)
# the October visits (Store_Visit_Findings_Reference_27Oct2026): Reliance Digital, 6 scored conversations
BASELINE = {"median": 6, "range": "3–13", "guide": "2 / 6", "price_assoc": "6 / 6", "minute": "7.5",
            "come_back": "2 / 6", "questions": "2"}
SAFE_WORDS = {
    "Reliance", "Digital", "Croma", "Vijay", "Sales", "Juhu", "Andheri", "Belapur", "Ulwe", "Citi", "Mall", "TV", "AC",
    "HDFC", "SBI", "ICICI", "EMI", "QLED", "OLED", "Meru", "Arka", "Novex", "The", "He", "She", "Mumbai", "Navi",
    "West", "CBD",
}


PHONE = re.compile(r"(?:\+?\s?9\s?1[\s-]?)?(?:\d[\s-]?){9}\d")  # a phone number, spaced or not (+91 98200 12345)
NAMED = re.compile(  # a name said after who they are: 'associate ravi sharma ne', 'naam Ravi', 'my name is …'
    r"\b(associate|salesman|salesperson|staff|executive|naam|name is|named|called|नाव|नाम)\s+"
    r"((?:[a-z\u0900-\u097f]+\s?){1,2}?)(?=\s*(?:ne|ka|ki|ke|ji|ने|चा|ची|चे|ला|,|\.|$|\s(?:ne|ka|ki|ke)\b))", re.I)


def scrub_pii(text: str | None) -> str | None:
    """No person's name or phone number is kept (the visit log promises it): numbers become '[number]', a name said
    after 'associate' / 'naam' becomes 'associate', and capitalised names too."""
    if not text:
        return text
    from chanakya.truth.fmt import latin_digits

    t = PHONE.sub("[number]", latin_digits(text))
    t = NAMED.sub(lambda m: m.group(1) if m.group(1).lower() in ("associate", "salesman", "salesperson", "staff",
                                                                    "executive") else m.group(1) + " [name]", t)
    return scrub_names(t)


def scrub_names(text: str | None) -> str | None:
    if not text:
        return text

    def rep(m):
        w = m.group(0)
        return w if w in SAFE_WORDS or w.isupper() else "associate"

    return re.sub(r"\b[A-Z][a-z]{2,}\b", rep, text)


def _merge(old: dict, new: VisitEntry) -> dict:
    out = dict(old)
    for k, v in new.model_dump().items():
        if k == "steps":
            cur = {s["step"]: s for s in out.get("steps", [])}
            for s in v:
                s["evidence"] = scrub_names(s.get("evidence"))
                cur[s["step"]] = s
            out["steps"] = list(cur.values())
        elif k == "notes" and v:  # every note of the visit, kept together, never a name or a number
            prev = out.get("notes") or ""
            out[k] = (prev + " | " + scrub_pii(v) if prev else scrub_pii(v))[:1000]
        elif v not in (None, [], ""):
            out[k] = scrub_pii(v) if isinstance(v, str) and k in ("best_sentence", "worst_sentence") else v
    return out


def derive_steps(entry: dict) -> dict:
    """Score the steps the stated facts decide (the reference rubric), where the note scored nothing itself:
    price first by the associate → Close 0, by the customer → 2; no demo → Demonstrate 0, a demo → 1 (2 side by
    side); home questions before the first product 4+ → Assess 2, 2–3 → 1, 0–1 → 0; the full cost → Complete 2 / 0."""
    steps = {s["step"]: s for s in entry.get("steps") or [] if not s.get("derived")}  # re-derived from the facts
    notes = (entry.get("notes") or "").lower()

    def put(step: str, score: int, why: str, **args) -> None:  # why = a visit_ev_* string, shown in the viewer's language
        if step not in steps:
            steps[step] = {"step": step, "score": score, "why": why, "args": args, "derived": True}

    pf, minute = entry.get("price_first_by"), entry.get("minute_first_price")
    if pf == "associate":
        put("close", 0, "close_assoc_at" if isinstance(minute, (int, float)) else "close_assoc", m=minute)
    elif re.search(r"today only|aaj hi|sirf aaj|last piece|aakhri piece", notes):
        put("close", 0, "pressure")
    elif pf == "customer":
        put("close", 2, "close_cust_at" if isinstance(minute, (int, float)) else "close_cust", m=minute)
    if entry.get("demo_shown") is False:
        put("demonstrate", 0, "no_demo")
    elif entry.get("demo_shown") is True:
        side = bool(re.search(r"side by side|saath mein|compare", notes))
        put("demonstrate", 2 if side else 1, "side" if side else "demo")
    q = entry.get("questions_before_first_product")
    if isinstance(q, int):
        put("assess", 2 if q >= 4 else (1 if q >= 2 else 0), "questions", q=q)
    if entry.get("full_cost_given") is True:
        put("complete", 2, "cost")
    elif entry.get("full_cost_given") is False:
        put("complete", 0, "price_only")
    entry["steps"] = [steps[s] for s in STEPS if s in steps]
    return entry


def _total(entry: dict) -> tuple[int, int]:
    sc = [s.get("score") for s in entry.get("steps") or [] if isinstance(s.get("score"), int)]
    return sum(sc), len(sc)


async def home(ctx: SkillContext) -> Reply:
    """The visitor's start: what this is, the visits so far in one line, and the three things to do. A greeting
    never starts a visit."""
    rows = await repo.visits_all(final_only=True)
    draft = await repo.visit_draft(ctx.actor_wa_id)
    totals = [_total(r.entry or {})[0] for r in rows]
    body = ctx.t("visit_home")
    if rows:
        body += "\n" + ctx.t("visit_home_count", n=ctx.reg.num(len(rows), "visit:count"),
                               m=ctx.reg.num(statistics.median(totals), "visit:median"))
    first = (Button(id=encode("v", draft.id, "more", "1"), title=ctx.t("b_visit_continue")) if draft and draft.entry
             else Button(id=encode("v", 0, "new", "1"), title=ctx.t("b_new_visit")))
    btns = [first, Button(id=encode("v", 0, "sum", "1"), title=ctx.t("b_visits_sofar")),
            Button(id=encode("v", 0, "export", "1"), title=ctx.t("b_export"))]
    ctx.reg.static("14")
    return ctx.reply(info_reply(ctx, body, btns))


async def start(ctx: SkillContext) -> Reply:
    await repo.visit_save(None, ctx.actor_wa_id, {}, "draft")
    return ctx.reply(info_reply(ctx, ctx.t("visit_start")))


NEW_VISIT = re.compile(r"^\s*(new|naya|nayi|nai|new visit|nayi visit|नवीन|नई|नया)\s*$", re.I)


def _has_visit(entry: VisitEntry) -> bool:
    """Did the note tell anything about a visit (a store, a product, what the associate did, a step)?"""
    d = entry.model_dump()
    return bool(d.get("steps")) or any(d.get(k) is not None for k in (
        "store", "scenario", "price_first_by", "minute_first_price", "questions_before_first_product", "demo_shown",
        "full_cost_given", "would_come_back"))


async def on_note(ctx: SkillContext) -> Reply:
    text = ctx.inbound.text or ""
    draft = await repo.visit_draft(ctx.actor_wa_id)
    if ctx.inbound.type == "text" and NEW_VISIT.match(text):
        return await start(ctx)  # 'new' / 'naya': a new visit, never a scored note
    if ctx.inbound.type == "text" and GREETING.match(text) and len(text.split()) <= 3:
        return await home(ctx)  # 'namaste' is not a visit
    if draft is None:
        draft = await repo.visit_save(None, ctx.actor_wa_id, {}, "draft")
    audio, amime = None, None
    if ctx.inbound.type == "audio" and ctx.inbound.media_id:
        try:
            audio, amime = await ctx.svc.channel.download_media(ctx.inbound.media_id)
        except Exception:  # an expired or missing media id: ask for the note again, never a crash
            ctx.ev("media_download_failed", kind="voice")
            return ctx.reply(info_reply(ctx, ctx.t("media_resend_voice")))
    try:
        entry = await ctx.ai.extract_visit(ctx.inbound.text, audio, mime=amime)
    except AIUnavailable:
        return ctx.reply(info_reply(ctx, ctx.t("voice_retry" if ctx.ai.live else "voice_unavailable")))
    if not _has_visit(entry):  # 'ok', 'thanks', '👍', the weather: nothing to score, never a 0/14 visit
        ctx.ev("visit_note_empty")
        return ctx.reply(info_reply(ctx, ctx.t("visit_too_short"), [Button(id=encode("v", 0, "home", 1),
                                                                         title=ctx.t("b_visit_home"))]))
    merged = derive_steps(_merge(draft.entry or {}, entry))
    draft = await repo.visit_save(draft.id, ctx.actor_wa_id, merged, "draft")
    ctx.ev("visit_note", log_id=draft.id, steps=len(merged.get("steps") or []))
    return ctx.reply(scorecard(ctx, draft.id, merged), _next_missing(ctx, draft.id, merged))


def _asked(entry: dict) -> int:
    return int(entry.get("_asked") or 0)


def _next_missing(ctx: SkillContext, log_id: int, entry: dict) -> OutMessage:
    """At most three questions for what the note left out: the key facts first, then the steps nobody scored."""
    if _asked(entry) < MAX_ASKS:
        for f in KEY_FIELDS:
            if entry.get(f) is None:
                if f == "price_first_by":
                    opts = [("associate", "b_v_associate"), ("customer", "b_v_customer"), ("none", "b_v_none")]
                else:
                    opts = [("yes", "b_yes"), ("no", "b_no")]
                btns = [Button(id=encode("v", log_id, f, val), title=ctx.t(key)) for val, key in opts]
                return info_reply(ctx, ctx.t(f"visit_ask_{f}"), btns)
        scored = {s["step"] for s in entry.get("steps") or []}
        for step in ASK_STEPS:
            if step not in scored:
                btns = [Button(id=encode("v", log_id, f"s_{step}", str(v)), title=ctx.t(f"b_visit_score_{v}"))
                        for v in (0, 1, 2)]
                ctx.reg.static("0 1 2")
                return info_reply(ctx, ctx.t("visit_ask_step", step=ctx.t(f"step_{step}"),
                                             rubric=ctx.t(f"visit_rubric_{step}")), btns)
    total, n = _total(entry)
    body = ctx.t("visit_ready", total=ctx.reg.num(total, "visit:total"), n=ctx.reg.num(n, "visit:steps"))
    if n < len(STEPS):
        body += "\n" + ctx.t("visit_ready_missing", k=ctx.reg.num(len(STEPS) - n, "visit:steps"))
    ctx.reg.static("14 7")
    return info_reply(ctx, body, [Button(id=encode("v", log_id, "save", "1"), title=ctx.t("b_save")),
                                  Button(id=encode("v", log_id, "more", "1"), title=ctx.t("b_add_more"))])


def evidence(ctx: SkillContext, s: dict) -> str:
    """What earned a step its score: the visitor's own words, the fact it was derived from, or their tap."""
    if s.get("why"):
        args = {k: ctx.reg.num(v, "visit:evidence") for k, v in (s.get("args") or {}).items() if v is not None}
        return ctx.t(f"visit_ev_{s['why']}", **args)
    if s.get("by_visitor"):
        return ctx.t("visit_ev_by_visitor")
    return truncate(scrub_names(s.get("evidence")) or "", 70)


def scorecard(ctx: SkillContext, log_id: int, entry: dict) -> OutMessage:
    """The visit as the reference's scorecard: the seven steps 0–2 with their evidence, the total and the level,
    and the facts that matter most (price first and when, questions before the product, demo, full cost, come back)."""
    total, n = _total(entry)
    steps = {s["step"]: s for s in entry.get("steps") or []}
    rows = [{"name": ctx.t(f"step_{st}"), "score": steps[st].get("score") if st in steps else None,
             "evidence": evidence(ctx, steps[st]) if st in steps else ""} for st in STEPS]
    yes, no, unk = ctx.t("visit_yes"), ctx.t("visit_no"), "—"

    def yn(v):
        return yes if v is True else (no if v is False else unk)

    pf = entry.get("price_first_by")
    minute = entry.get("minute_first_price")
    facts = [
        {"k": ctx.t("visit_f_price"), "v": (ctx.t(f"b_v_{pf}") if pf else unk)
         + (f" · {ctx.t('visit_minute', m=ctx.reg.num(minute, 'visit:minute'))}" if minute is not None else "")},
        {"k": ctx.t("visit_f_questions"), "v": ctx.reg.num(entry["questions_before_first_product"], "visit:questions")
         if isinstance(entry.get("questions_before_first_product"), int) else unk},
        {"k": ctx.t("visit_f_demo"), "v": yn(entry.get("demo_shown"))},
        {"k": ctx.t("visit_f_cost"), "v": yn(entry.get("full_cost_given"))},
        {"k": ctx.t("visit_f_back"), "v": yn(entry.get("would_come_back"))},
    ]
    scen = {"A_tv": "TV", "B_wm_ac": "WM / AC", "C_laptop": "Laptop"}.get(entry.get("scenario") or "", "")
    head = " · ".join(x for x in (entry.get("store") or "", scen) if x)
    for st in STEPS:
        if st in steps and not steps[st].get("why"):
            ctx.reg.raw(rows[STEPS.index(st)]["evidence"], "visit:evidence")
    for x in (entry.get("best_sentence"), entry.get("worst_sentence"), head):
        if x:
            ctx.reg.raw(x, "visit:note")
    ctx.reg.static("14 0 1 2 11")
    card = Card(template="visit", lang=ctx.lang, data={  # type: ignore[arg-type]
        "head": head, "rows": rows, "total": ctx.reg.num(total, "visit:total"), "scored": n,
        "guide": total >= 11, "facts": facts, "best": scrub_names(entry.get("best_sentence")) or "",
        "worst": scrub_names(entry.get("worst_sentence")) or "", "id": log_id})
    body = ctx.t("visit_card_caption", total=ctx.reg.num(total, "visit:total"), n=ctx.reg.num(n, "visit:steps"))
    return info_reply(ctx, body, card=card)


async def button(ctx: SkillContext, log_id: str, field: str, val: str) -> Reply:
    if field == "sum":
        return await summary(ctx)
    draft = await repo.visit_draft(ctx.actor_wa_id)
    if draft is None or str(draft.id) != log_id:
        return ctx.reply(info_reply(ctx, ctx.t("visit_none")))
    entry = dict(draft.entry or {})
    if field == "save":
        from chanakya.core.clock import today

        entry.setdefault("date", today().isoformat())
        entry.pop("_asked", None)
        await repo.visit_save(draft.id, ctx.actor_wa_id, entry, "final")
        ctx.ev("visit_saved", log_id=draft.id, total=_total(entry)[0])
        btns = [Button(id=encode("v", 0, "sum", "1"), title=ctx.t("b_visits_sofar")),
                Button(id=encode("v", 0, "new", "1"), title=ctx.t("b_new_visit")),
                Button(id=encode("v", 0, "export", "1"), title=ctx.t("b_export"))]
        total, _ = _total(entry)
        return ctx.reply(info_reply(ctx, ctx.t("visit_saved", id=ctx.reg.raw(str(draft.id), "visit:id"),
                                               total=ctx.reg.num(total, "visit:total")), btns))
    if field == "more":
        return ctx.reply(info_reply(ctx, ctx.t("visit_more")))
    if field == "price_first_by":
        entry[field] = val
    elif field in ("demo_shown", "would_come_back"):
        entry[field] = val == "yes"
    elif field.startswith("s_") and field[2:] in STEPS and val in ("0", "1", "2"):
        steps = {s["step"]: s for s in entry.get("steps") or []}
        steps[field[2:]] = {"step": field[2:], "score": int(val), "evidence": "", "by_visitor": True}
        entry["steps"] = [steps[s] for s in STEPS if s in steps]
    entry["_asked"] = _asked(entry) + 1
    entry = derive_steps(entry)
    await repo.visit_save(draft.id, ctx.actor_wa_id, entry, "draft")
    return ctx.reply(_next_missing(ctx, draft.id, entry))


async def summary(ctx: SkillContext, to: str | None = None) -> Reply:
    """'Visits so far': the reference's "What the team found" table, from the logged visits, beside the October
    visits (Reliance Digital). For the visitor and, from the menu, the department manager."""
    rows = [r.entry or {} for r in await repo.visits_all(final_only=True)]
    if not rows:  # a 'new visit' button only for the visitor: the manager's voice notes are not visit notes
        btns = [Button(id=encode("v", 0, "new", "1"), title=ctx.t("b_new_visit"))] if ctx.role == "visitor" else []
        return ctx.reply(info_reply(ctx, ctx.t("visits_none"), btns, to=to))
    n = len(rows)
    num = ctx.reg.num
    totals = [_total(e)[0] for e in rows]
    pf = [e.get("price_first_by") for e in rows]
    mins = [e["minute_first_price"] for e in rows if isinstance(e.get("minute_first_price"), (int, float))]
    qs = [e["questions_before_first_product"] for e in rows if isinstance(e.get("questions_before_first_product"), int)]
    back = [e.get("would_come_back") for e in rows if e.get("would_come_back") is not None]
    demo = [e.get("demo_shown") for e in rows if e.get("demo_shown") is not None]

    def of(k, total):
        return f"{num(k, 'visit:sum')} / {num(total, 'visit:sum')}"

    table = [
        {"k": ctx.t("vs_score"), "v": f"{num(min(totals), 'visit:sum')}–{num(max(totals), 'visit:sum')} "
                                       f"({num(statistics.median(totals), 'visit:sum')})", "b": f"{BASELINE['range']} ({BASELINE['median']})"},
        {"k": ctx.t("vs_guide"), "v": of(sum(1 for t in totals if t >= 11), n), "b": BASELINE["guide"]},
        {"k": ctx.t("vs_price_assoc"), "v": of(pf.count("associate"), n), "b": BASELINE["price_assoc"]},
        {"k": ctx.t("vs_minute"), "v": num(statistics.median(mins), "visit:sum") if mins else "—", "b": BASELINE["minute"]},
        {"k": ctx.t("vs_questions"), "v": num(statistics.median(qs), "visit:sum") if qs else "—", "b": BASELINE["questions"]},
        {"k": ctx.t("vs_demo"), "v": of(sum(1 for d in demo if d), len(demo)) if demo else "—", "b": "—"},
        {"k": ctx.t("vs_back"), "v": of(sum(1 for b in back if b), len(back)) if back else "—", "b": BASELINE["come_back"]},
    ]
    steps = []
    for st in STEPS:
        sc = [s["score"] for e in rows for s in e.get("steps") or [] if s.get("step") == st and isinstance(s.get("score"), int)]
        steps.append({"name": ctx.t(f"step_{st}"), "avg": round(sum(sc) / len(sc), 1) if sc else None})
    for r in table:
        ctx.reg.raw(r["b"], "reference:store_visits_oct")
    ctx.reg.static("14 11 2 6 7.5 3 13")
    card = Card(template="visits", lang=ctx.lang, data={"n": num(n, "visit:sum"), "table": table,  # type: ignore[arg-type]
                                                         "steps": steps})
    weakest = min((s for s in steps if s["avg"] is not None), key=lambda s: s["avg"], default=None)
    body = ctx.t("visits_caption", n=num(n, "visit:sum"), m=num(statistics.median(totals), "visit:sum"))
    if weakest:
        body += "\n" + ctx.t("visits_weakest", step=weakest["name"])
    btns = [Button(id=encode("v", 0, "export", "1"), title=ctx.t("b_export"))]
    if not to and ctx.role == "visitor":
        btns.insert(0, Button(id=encode("v", 0, "new", "1"), title=ctx.t("b_new_visit")))
    m = info_reply(ctx, body, btns, card=card, to=to)
    return ctx.reply(m)


def to_csv(rows: list) -> bytes:
    cols = (
        [
            "id",
            "store",
            "date",
            "scenario",
            "questions_before_first_product",
            "price_first_by",
            "minute_first_price",
            "demo_shown",
            "full_cost_given",
            "would_come_back",
            "best_sentence",
            "worst_sentence",
            "notes",
        ]
        + STEPS
        + ["total"]
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        e = r.entry or {}
        steps = {s["step"]: s.get("score") for s in e.get("steps", [])}
        total = sum(v for v in steps.values() if isinstance(v, int))
        w.writerow([r.id] + [e.get(c) for c in cols[1:13]] + [steps.get(s) for s in STEPS] + [total])
    return buf.getvalue().encode("utf-8")


async def export(ctx: SkillContext) -> Reply:
    rows = await repo.visits_all(final_only=True)
    if not rows:  # nothing saved yet: one line, never an empty file
        return ctx.reply(info_reply(ctx, ctx.t("visit_export_none")))
    data = to_csv(rows)
    mid = await ctx.svc.channel.upload_media(data, "text/csv", "visit_log.csv")
    n = ctx.reg.num(len(rows), "visit:count")
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="document",
            media_id=mid,
            filename="visit_log.csv",
            caption=ctx.t("visit_export_one" if len(rows) == 1 else "visit_export", n=n),
            allowed_numbers=ctx.reg.allowed(),
        )
    )
