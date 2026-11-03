"""One live customer conversation (a Floor): what was heard → the Home Card, the wants, A / B / C and ONE cue.

Deterministic and data-only: the same packs, fit rules, wants and offers as the WhatsApp agent. Nothing a model
says is trusted as a fact; `heard()` reports from a live model are validated against the pack like voice notes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from chanakya.ai import rules
from chanakya.ai.schemas import NumberPair, SlotPair, Understanding
from chanakya.brain import packs as P
from chanakya.brain import wants as W
from chanakya.brain.data_questions import skip_slots
from chanakya.brain.fit import fit, in_band
from chanakya.brain.next_question import ordered_next
from chanakya.brain.options import compute_options
from chanakya.core.clock import now, today
from chanakya.core.contracts import HomeCard
from chanakya.i18n.lang import pick
from chanakya.truth import fmt

LANGS = ("hi", "en", "mr")

# The associate's cue lines (their language). {x} are filled from the data only.
CUES = {
    "ask": {"hi": "Poochhiye: {q}", "en": "Ask: {q}", "mr": "विचारा: {q}"},
    "say": {"hi": "Boliye: {q}", "en": "Say: {q}", "mr": "सांगा: {q}"},
    "product": {"hi": "Poochhiye: kya dekh rahe hain — TV, AC, fridge, washing machine ya laptop?",
                "en": "Ask: what are they looking for — TV, AC, fridge, washing machine or laptop?",
                "mr": "विचारा: काय बघताय — टीव्ही, एसी, फ्रिज, वॉशिंग मशीन की लॅपटॉप?"},
    "budget_gap": {"hi": "{budget} mein is ghar ke layak stock mein kuch nahi; sabse kam {low}. Pehle size ya EMI ki baat kariye.",
                   "en": "Nothing in stock that suits this home is under {budget}; the lowest is {low}. Talk size or EMI first.",
                   "mr": "{budget} मध्ये या घराला योग्य स्टॉकमध्ये काही नाही; सर्वात कमी {low}. आधी साइज किंवा EMI बोला."},
    "offer_soon": {"hi": "{label} par '{title}' {date} tak hi hai — abhi bataiye.",
                   "en": "{label}: '{title}' ends {date}. Mention it now.",
                   "mr": "{label} वर '{title}' फक्त {date} पर्यंत — आत्ताच सांगा."},
    "only_best": {"hi": "Sirf {label} '{need}' poora karta hai — wahi pehle dikhaiye.",
                  "en": "Only {label} meets '{need}'. Show it first.",
                  "mr": "फक्त {label} '{need}' पूर्ण करतो — तोच आधी दाखवा."},
    "demo": {"hi": "{label} ka demo: {steps}", "en": "Demo {label}: {steps}", "mr": "{label} चा डेमो: {steps}"},
    "exchange": {"hi": "Purane {cat} ka exchange live hai — poochhiye kitna purana hai.",
                 "en": "Exchange is live for the old {cat}. Ask how old it is.",
                 "mr": "जुन्या {cat} साठी एक्सचेंज चालू आहे — किती जुना आहे ते विचारा."},
    "family": {"hi": "Parivaar decide karega: WhatsApp par Family Card bhejiye.",
               "en": "The family decides: send the Family Card on WhatsApp.",
               "mr": "कुटुंब ठरवेल: WhatsApp वर फॅमिली कार्ड पाठवा."},
    "options_ready": {"hi": "A / B / C taiyaar — {label} se shuru kariye: {why}",
                      "en": "A / B / C ready. Start with {label}: {why}",
                      "mr": "A / B / C तयार — {label} पासून सुरू करा: {why}"},
}
CONSENT = {  # the one line the associate says before the mic starts
    "hi": "Main Chanakya app se aapki zaroorat note kar loon? Kuch record nahi hota, bas sahi product chunne ke liye.",
    "en": "May I let the Chanakya app note your needs while we talk? Nothing is recorded; it helps pick the right one.",
    "mr": "आपण बोलताना Chanakya ॲप तुमच्या गरजा नोंदवू का? काही रेकॉर्ड होत नाही, फक्त योग्य प्रॉडक्ट निवडायला.",
}
CAT_WORD = {"tv": "TV", "ac": "AC", "fridge": "fridge", "wm": "washing machine", "laptop": "laptop"}


@dataclass
class Cue:
    id: str
    kind: str  # ask | say | tip | options
    text: str
    why: str = ""
    slot: str | None = None


@dataclass
class Floor:
    rt: object  # Runtime: .packs, .facts
    lang: str = "hi"  # the associate's language (cues)
    home: HomeCard = field(default_factory=HomeCard)
    wants: dict = field(default_factory=dict)
    heard: list[dict] = field(default_factory=list)
    shown: list[str] = field(default_factory=list)
    cue: Cue | None = None
    force_options: bool = False
    started: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.home.slots and self.home.customer_lang == "en" and self.lang != "en":
            self.home.customer_lang = self.lang  # the customer speaks the associate's language unless told

    # ---------- input ----------
    @property
    def pack(self):
        return self.rt.packs.get(self.home.category)

    @property
    def clang(self) -> str:
        return self.home.customer_lang or "hi"

    def _products(self) -> list:
        return self.rt.facts.products_sync(self.home.category) if self.home.category else []

    def absorb(self, text: str, who: str = "customer") -> Understanding | None:
        """One utterance (a final transcript line or a typed line): apply it, then pick the cue."""
        text = " ".join((text or "").split())
        if not text:
            return None
        self.heard.append({"who": who, "text": text[:300], "at": now().strftime("%H:%M:%S")})
        del self.heard[:-40]
        u = rules.understand_text(text, self.rt.packs, self.home.category, options_shown=bool(self.options()))
        self.apply(u)
        if self.home.category:
            brands = sorted({p.brand for p in self._products() if p.brand})
            self.wants.update(W.parse(text, self.home.category, brands))
        self._next_cue(u)
        return u

    def apply(self, u: Understanding) -> int:
        """Slots and numbers into the Home Card, validated against the pack (as assess.apply_understanding)."""
        home = self.home
        if u.category and (home.category is None or not home.slots) and self.rt.packs.get(u.category):
            home.category = u.category
        if u.customer_name and not home.customer_name:
            home.customer_name = u.customer_name
        if u.customer_language:
            home.customer_lang = u.customer_language
        pack = self.pack
        if pack is None:
            return 0
        new = 0
        for sp in u.slots:
            sd = pack.slot(sp.slot)
            if not sd or (sd.values and sp.value not in sd.values):
                continue
            new += P.set_slot(home, pack, sp.slot, sp.value, sd.value_number(sp.value) if sd.bands else None, "voice", now())
        for n in u.numbers:
            if n.name == "budget_inr":
                home.start_level_inr, home.start_level_source = int(n.value), "associate_voice"
                new += 1
                continue
            sid = pack.slot_for_number(n.name)
            if sid:
                new += P.set_slot(home, pack, sid, "num", float(n.value), "voice", now())
        return new

    def heard_tool(self, args: dict) -> dict:
        """A live model's structured report ({slots: [{slot, value}], numbers: [{name, value}], category}).
        Only pack-valid values are applied; returns what was accepted (the model sees only that)."""
        slots, nums = [], []
        for s in args.get("slots") or []:
            try:
                slots.append(SlotPair(**s))
            except Exception:
                continue
        for n in args.get("numbers") or []:
            try:
                nums.append(NumberPair(**n))
            except Exception:
                continue
        cat = args.get("category") if args.get("category") in CAT_WORD else None
        u = Understanding(category=cat, slots=slots, numbers=nums)
        before = self.home.model_dump_json()
        self.apply(u)
        self._next_cue(u)
        return {"accepted": before != self.home.model_dump_json(), "known": self.known_slots()}

    def done_cue(self) -> None:
        self.cue = None
        self._next_cue(None)

    # ---------- views ----------
    def known_slots(self) -> list[str]:
        return list(self.home.slots)

    def gate(self) -> bool:
        pack = self.pack
        if pack is None:
            return False
        return self.force_options or pack.known_required(self.home) >= pack.gate_min_slots or len(self.wants) >= 2

    def options(self):
        pack = self.pack
        if pack is None or not self.gate():
            return None
        return compute_options(self._products(), self.home, pack, self.rt.facts.snap.guides, self.wants)

    def _offers(self, p) -> list:
        from chanakya.skills.product_view import live_offers_for

        return live_offers_for(SimpleNamespace(facts=self.rt.facts), p)

    def _need(self, rule_id: str) -> str | None:
        pack = self.pack
        d = pack.needs.get(rule_id) if pack else None
        if not d:
            return None
        text = pick(d, "en" if self.lang == "hi" else self.lang)
        dist = P.number(self.home, "seat_distance", pack)
        if "{distance}" in text and dist:
            text = text.replace("{distance}", fmt.num(dist))
        return None if "{" in text else text

    def _next_question(self):
        pack = self.pack
        if pack is None:
            return None
        avail = [p for p in self._products() if p.qty > 0 or p.nearby_qty > 0]
        skip = skip_slots(pack, avail, self.home, self.rt.facts.snap.guides)
        sid = ordered_next(self.home, pack, exclude=skip)
        return pack.slot(sid) if sid else None

    def _cue_text(self, key: str, **kw) -> str:
        return CUES[key][self.lang if self.lang in LANGS else "en"].format(**kw)

    def _candidates(self, u: Understanding | None) -> list[Cue]:
        out: list[Cue] = []
        packs, pack, home = self.rt.packs, self.pack, self.home
        if u is not None:  # what the customer just said comes first
            if u.objection:
                ob = (packs.common.get("objections") or {}).get(u.objection) or {}
                my = packs.myth(ob["use_myth"], home.category) if ob.get("use_myth") else None
                line = (my.line if my else None) or ob.get("line")
                if line:
                    out.append(Cue(f"obj:{u.objection}", "say", self._cue_text("say", q=pick(line, self.clang))))
            if u.claim_id:
                my = packs.myth(u.claim_id, home.category)
                if my and my.line:
                    out.append(Cue(f"myth:{my.id}", "say", self._cue_text("say", q=pick(my.line, self.clang))))
        if pack is None:
            out.append(Cue("product", "ask", self._cue_text("product")))
            return out
        g = self.rt.facts.snap.guides
        opts = self.options()
        avail = [p for p in self._products() if p.qty > 0 or p.nearby_qty > 0]
        budget = home.start_level_inr
        if budget and home.start_level_source != "none":
            suits = [p for p in avail if (b := in_band(p, home, pack, g)) is not None and b >= 1]
            if suits and not any(p.price <= budget * 1.1 for p in suits):
                low = min(suits, key=lambda p: p.price)
                out.append(Cue("budget_gap", "tip", self._cue_text("budget_gap", budget=fmt.inr(budget), low=fmt.inr(low.price))))
        if "replace" in P.values(home, "trigger") and any(e.category == home.category for e in self.rt.facts.snap.exchange):
            out.append(Cue("exchange", "tip", self._cue_text("exchange", cat=CAT_WORD.get(home.category, ""))))
        if opts:
            best = opts.by_role("best")
            if best:
                fb = fit(best.product, home, pack, g)
                val = opts.by_role("value")
                fv = {c.rule_id: c.match for c in fit(val.product, home, pack, g).contributions} if val else {}
                diff = sorted((c for c in fb.contributions if c.match > fv.get(c.rule_id, 0) and self._need(c.rule_id)),
                              key=lambda c: -(c.weight * (c.match - fv.get(c.rule_id, 0))))  # what B has that A lacks
                why = self._need(diff[0].rule_id if diff else (fb.top_rule() or "")) or best.product.short_name
                out.append(Cue("options_ready", "options", self._cue_text("options_ready", label=best.label, why=why)))
                soon = [o for o, _ in self._offers(best.product) if (o.valid_to - today()).days <= 3]
                if soon:
                    o = soon[0]
                    out.append(Cue("offer_soon", "tip", self._cue_text(
                        "offer_soon", label=best.label, title=pick(o.title, "en"), date=fmt.date_short(o.valid_to, "en"))))
                top = fit(best.product, home, pack, g).top_rule()
                need = self._need(top) if top else None
                others = [o for o in opts.options if o.role != "best"]
                if need and others and not any(
                    any(c.rule_id == top and c.match >= 1 for c in fit(o.product, home, pack, g).contributions) for o in others
                ):
                    out.append(Cue("only_best", "tip", self._cue_text("only_best", label=best.label, need=need)))
            if any(v in ("family", "together") for v in P.values(home, "decider")):
                out.append(Cue("family", "tip", self._cue_text("family")))
        sd = self._next_question()
        if sd is not None:
            out.append(Cue(f"ask:{sd.id}", "ask", self._cue_text("ask", q=pick(sd.ask, self.clang)),
                           why=pick(sd.why, self.lang), slot=sd.id))
        return out

    def _next_cue(self, u: Understanding | None) -> None:
        if self.cue and self.cue.slot and P.known(self.home, self.cue.slot):
            self.cue = None  # the question was answered
        cands = self._candidates(u)
        if self.cue and self.cue.kind != "say" and self.cue.id not in {c.id for c in cands}:
            self.cue = None  # no longer true (e.g. the product is known now)
        urgent = [c for c in cands if c.kind in ("say", "tip") and c.id not in self.shown]  # facts pre-empt questions
        fresh = urgent or ([] if self.cue else [c for c in cands if c.id not in self.shown])
        if fresh:
            self.cue = fresh[0]
            self.shown.append(fresh[0].id)

    def view(self) -> dict:
        """Everything the page shows; every number from the data."""
        pack, home = self.pack, self.home
        needs = []
        if pack:
            for sid, vals in home.slots.items():
                sd = pack.slot(sid)
                if not sd:
                    continue
                v = vals[-1]
                val = f"{fmt.num(v.number)}" if v.number is not None and v.value in ("num", "", None) else \
                    ", ".join(sd.value_title(x.value, self.lang) for x in vals)
                needs.append({"slot": sid, "title": pick(sd.title, self.lang), "value": val})
        if home.start_level_inr:
            needs.append({"slot": "budget", "title": "Budget", "value": fmt.inr(home.start_level_inr)})
        wants = [{"key": k, "value": f"{v}" + {"size_in": " inch", "ton": " ton", "capacity_kg": " kg",
                                                "capacity_l": " L", "star": " star", "ram_gb": " GB RAM"}.get(k, "")}
                 for k, v in self.wants.items()]
        opts = self.options()
        cards = []
        if opts:
            g = self.rt.facts.snap.guides
            for o in opts.options:
                p = o.product
                fr = fit(p, home, pack, g)
                reasons = [f"{w} as asked" for w in (x["value"] for x in wants if x["key"] in W.meets(p, self.wants))]
                for c in sorted(fr.contributions, key=lambda c: -(c.weight * c.match)):
                    n = self._need(c.rule_id) if c.match >= 1 else None
                    if n and n not in reasons:
                        reasons.append(n)
                    if len(reasons) >= 3:
                        break
                offs = self._offers(p)
                offer = None
                if offs:
                    of, save = offs[0]
                    offer = {"title": pick(of.title, "en"), "till": fmt.date_short(of.valid_to, "en"),
                             "save": fmt.inr(save) if save else None, "more": len(offs) - 1}
                cards.append({
                    "label": o.label, "role": o.role, "sku": p.sku, "title": pick(p.title, "en") or p.model,
                    "brand": p.brand, "image": p.image if (p.image or "").startswith("http") else None,
                    "price": fmt.inr(p.price), "mrp": fmt.inr(p.mrp) if p.mrp and p.mrp > p.price else None,
                    "save": fmt.inr(p.mrp - p.price) if p.mrp and p.mrp > p.price else None,
                    "stock": "in store" if p.qty > 0 else ("nearby" if p.nearby_qty > 0 else "check"),
                    "fit": fr.score, "reasons": reasons[:3], "offer": offer,
                })
        req = pack.known_required(home) if pack else 0
        return {
            "type": "view",
            "category": home.category, "category_word": CAT_WORD.get(home.category or "", ""),
            "customer": home.customer_name, "customer_lang": self.clang, "lang": self.lang,
            "needs": needs, "wants": wants, "options": cards,
            "gate": {"known": req, "need": pack.gate_min_slots if pack else 0, "open": self.gate()},
            "cue": self.cue.__dict__ if self.cue else None,
            "heard": self.heard[-6:],
            "consent": CONSENT.get(self.clang, CONSENT["en"]),
            "test_data": any(getattr(p, "test_data", False) for p in self._products()[:1]),
        }
