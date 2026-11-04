"""AIClient — the AI seam (spec Section 8).

Modes (AI_MODE):
  auto    — Gemini when GEMINI_API_KEY is set, else rules (default)
  live    — Gemini (voice, text, photos, visit log, expert formatting); falls back to rules for text
  rules   — deterministic offline understanding from pack keywords (no voice)
  fixture — canned JSON from data/fixtures (tests, simulator; no network)

The model only extracts and classifies into fixed schemas. Products, prices and every number shown come from
the database through the skills and the number firewall."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from chanakya.ai import prompts, rules
from chanakya.ai.gemini import AIUnavailable
from chanakya.ai.schemas import ExpertAnswer, FactQuery, ImageRead, Spec, StepScore, Understanding, VisitEntry
from chanakya.brain.packs import PackRegistry
from chanakya.config import ROOT, Settings
from chanakya.log import get_log

log = get_log("ai")
FIX_DIR = ROOT / "data" / "fixtures"
__all__ = ["AIClient", "AIUnavailable"]


def _audio_mime(mime: str | None) -> str:
    m = (mime or "audio/ogg").split(";")[0].strip()
    return m or "audio/ogg"


class AIClient:
    def __init__(self, s: Settings, packs: PackRegistry, fixtures: Path = FIX_DIR):
        mode = (s.ai_mode or "auto").lower()
        if mode == "auto":
            mode = "live" if s.gemini_api_key else "rules"
        if mode == "live" and not s.gemini_api_key:
            log.warning("ai_live_without_key", using="rules")
            mode = "rules"
        if mode not in ("live", "rules", "fixture"):
            mode = "rules"
        self.mode = mode
        self.packs = packs
        self.fix = fixtures
        self.gemini = None
        self.heard: dict[str, str] = {}  # voice note (sha1) → its words, so a retry never asks the model twice
        if mode == "live":
            from chanakya.ai.gemini import GeminiClient

            self.gemini = GeminiClient(s)

    @property
    def live(self) -> bool:
        return self.gemini is not None

    # ---------- 8.2 ----------
    async def understand(
        self, text: str | None, audio: bytes | None, ctx: dict, mime: str | None = None
    ) -> Understanding:
        if text and text.startswith("FIX:"):
            return self._fixture("understand", text[4:].strip(), Understanding)
        if audio is not None:
            key = hashlib.sha1(audio).hexdigest()
            if (self.fix / "understand" / f"{key}.json").exists():
                return self._fixture("understand", key, Understanding)
        if self.live and audio is not None:
            # voice (3 Nov 2026 RCA: Gemini overloaded): the light plain transcript first, across models with retries;
            # then the meaning from the text (Gemini text, else the offline rules). Only the transcript can fail.
            from chanakya.app import diag

            heard = await self.hear(audio, mime)
            u = await self._understand_text(heard, ctx)
            u.transcript = heard
            diag.mark("ai_last_understanding", {
                "source": "audio", "language": u.language, "category": u.category, "slots": len(u.slots),
                "numbers": len(u.numbers), "intents": u.intents, "transcript_chars": len(heard)})
            return u
        if self.live:
            return await self._understand_text(text or "", ctx)
        if audio is not None:
            raise AIUnavailable("voice")
        return self._rules(text or "", ctx)

    async def _understand_text(self, text: str, ctx: dict) -> Understanding:
        """The meaning of a note's words: Gemini (text only: fast and light), else the offline rules. Never fails."""
        from chanakya.app import diag

        try:
            u = await self.gemini.hedged([text, "CONTEXT:\n" + json.dumps(ctx, ensure_ascii=False)], Understanding,
                                         prompts.UNDERSTAND_SYSTEM, 4.0, "understand_text")  # then the rules: < 5 s
            if not u.transcript:
                u.transcript = text
            return u
        except AIUnavailable:
            diag.bump("understand_by_rules")
            u = self._rules(text, ctx)
            u.transcript = text
            return u

    async def hear(self, audio: bytes, mime: str | None = None, timeout: float = 24.0) -> str:
        """The words of a voice note. Cached by content, so a retry (or the second step) never pays twice.
        Raises AIUnavailable only when every model failed for the whole time."""
        from chanakya.app import diag

        key = hashlib.sha1(audio).hexdigest()
        if key in self.heard:
            return self.heard[key]
        if not self.live:
            raise AIUnavailable("voice")
        from google.genai import types

        diag.bump("voice_notes")
        part = types.Part.from_bytes(data=audio, mime_type=_audio_mime(mime))
        try:
            text = await self.gemini.transcribe([part], prompts.TRANSCRIBE_SYSTEM, timeout=timeout)
        except AIUnavailable:
            diag.bump("voice_failed")
            raise
        diag.bump("voice_ok")
        self.heard[key] = text
        while len(self.heard) > 64:
            self.heard.pop(next(iter(self.heard)))
        return text

    async def transcribe(self, audio: bytes, mime: str | None = None) -> str:
        """Plain words of a voice note (manager commands, the associate's name)."""
        return await self.hear(audio, mime)

    def _rules(self, text: str, ctx: dict) -> Understanding:
        myths = [m["id"] for m in ctx.get("myths", [])] or None
        return rules.understand_text(
            text, self.packs, ctx.get("category"), myths, options_shown=bool(ctx.get("options_shown")),
            brands=ctx.get("brands") or [],
        )

    # ---------- 8.3 ----------
    async def read_image(
        self, img: bytes, filename: str | None = None, caption: str | None = None, hint: str | None = None
    ) -> ImageRead:
        """Fixture by filename stem; a Chanakya label QR; Gemini vision when live; else a caption/hint-based read."""
        if filename:
            stem = Path(filename).stem
            if (self.fix / "image_read" / f"{stem}.json").exists():
                return self._fixture("image_read", stem, ImageRead)
        if hint != "room":
            from chanakya.ai.qr import decode_sku

            sku = decode_sku(img)
            if sku:
                return ImageRead(kind="label", sku=sku, model_text=sku, confidence=1.0)
        if self.live:
            from google.genai import types

            ask = "Read this photo." + (f" Caption from the associate: {caption}" if caption else "")
            if hint == "room":
                ask += " It is a photo of a room in the customer's home."
            try:
                return await self.gemini.hedged(  # a busy model never holds the room photo up
                    [types.Part.from_bytes(data=img, mime_type="image/jpeg"), ask],
                    ImageRead,
                    prompts.READ_IMAGE_SYSTEM + prompts.READ_IMAGE_GEOMETRY,
                    12.0,
                    "read_image",
                )
            except AIUnavailable:
                pass
        cap = (caption or "").strip()
        if hint == "room" or re.search(
            r"\b(room|kamra|kamre|deewar|diwar|wall|hall)\b|कमरा|खोली|भिंत|दीवार", cap.lower()
        ):
            return ImageRead(kind="room")
        if cap:
            m = re.search(r"(\d{2})\s*(?:\"|inch|in\b|इंच)", cap)
            specs = [Spec(name=k, value=v, unit=u) for v, u, k in re.findall(r"(\d+)\s*(hz|nits)()", cap.lower())]
            brand = next((b for b in ("Arka", "Meru", "Novex") if b.lower() in cap.lower()), None)
            if brand:
                return ImageRead(
                    kind="label",
                    brand=brand,
                    model_text=cap[:60],
                    size_in=float(m.group(1)) if m else None,
                    specs=specs,
                    confidence=0.8,
                    raw_text=cap,
                )
        if hint == "label":
            return ImageRead(kind="label", confidence=0.0, raw_text=cap or None)
        return ImageRead(kind="other", raw_text=cap or None)

    # ---------- 8.4 ----------
    async def edit_image(self, image_bytes: bytes, prompt: str) -> bytes | None:
        if self.live:
            return await self.gemini.edit_image(image_bytes, prompt)
        return None  # no image model: try-on keeps the deterministic composite

    # ---------- product question → one data field ----------
    async def map_fact_question(self, question: str, products: list[dict], fields: list[dict]) -> FactQuery | None:
        if not self.live:
            return None
        ctx = {"PRODUCTS": products[:40], "FIELDS": fields}
        try:
            return await self.gemini.json(
                [question, json.dumps(ctx, ensure_ascii=False)],
                FactQuery,
                prompts.FACT_QUERY_SYSTEM,
                timeout=5.0,
                kind="fact_query",
            )
        except AIUnavailable:
            return None

    # ---------- 8.5 ----------
    async def extract_visit(self, text: str | None, audio: bytes | None, mime: str | None = None) -> VisitEntry:
        if text and text.startswith("FIX:"):
            return self._fixture("visit", text[4:].strip(), VisitEntry)
        if self.live:
            from google.genai import types

            part = types.Part.from_bytes(data=audio, mime_type=_audio_mime(mime)) if audio is not None else (text or "")
            try:
                return await self.gemini.json([part], VisitEntry, prompts.VISIT_SYSTEM, timeout=10.0, kind="visit")
            except AIUnavailable:
                if audio is not None:
                    raise
        if audio is not None:
            raise AIUnavailable("voice")
        return _visit_rules(text or "")

    # ---------- 8.6 ----------
    async def format_expert_answer(
        self, text: str | None, audio: bytes | None, assoc_lang: str, cust_lang: str, mime: str | None = None
    ) -> ExpertAnswer:
        if self.live:
            from google.genai import types

            part = types.Part.from_bytes(data=audio, mime_type=_audio_mime(mime)) if audio is not None else (text or "")
            names = {"hi": "Hindi (Roman script, Hinglish)", "mr": "Marathi (Devanagari)", "en": "English"}
            cust = {"hi": "Hindi (Devanagari)", "mr": "Marathi (Devanagari)", "en": "English"}
            try:
                return await self.gemini.json(
                    [part, f"ASSOCIATE language: {names.get(assoc_lang)}. CUSTOMER language: {cust.get(cust_lang)}."],
                    ExpertAnswer,
                    prompts.FORMAT_EXPERT_SYSTEM,
                    timeout=10.0,
                    kind="expert",
                )
            except AIUnavailable:
                if audio is not None:
                    raise
        if audio is not None:
            raise AIUnavailable("voice")
        t = " ".join((text or "").split())
        bolo = None
        m = re.search(r"(?:bolo|say|बोलो|बोला)\s*[:\-]\s*(.+)$", t, re.I)
        if m:
            bolo = m.group(1).strip().strip('"')
            t = t[: m.start()].strip()
        return ExpertAnswer(
            for_associate=t[:300], bolo=bolo, numbers_used=re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")
        )

    def _fixture(self, kind: str, key: str, model):
        p = self.fix / kind / f"{key}.json"
        if not p.exists():
            raise AIUnavailable(f"fixture {kind}/{key}")
        return model.model_validate(json.loads(p.read_text(encoding="utf-8")))


_STEP_WORDS = {
    "validate": ["validate", "maana", "agree"],
    "assess": ["assess", "sawaal", "questions", "prashn"],
    "translate": ["translate", "samjhaya", "explain"],
    "demonstrate": ["demo", "dikhaya", "demonstrate"],
    "complete": ["complete", "poora kharcha", "full cost"],
    "close": ["close", "offer"],
    "follow_through": ["follow", "whatsapp", "card bheja"],
}


def _visit_rules(text: str) -> VisitEntry:
    low = text.lower()
    e = VisitEntry(notes=text[:500] or None)
    m = re.search(r"\b(juhu|andheri|belapur|ulwe|citi mall|croma|vijay sales)\b", low)
    if m:
        e.store = m.group(1).title()
    if re.search(r"\btv\b|टीवी|टीव्ही", low):
        e.scenario = "A_tv"
    elif re.search(r"washing|\bac\b|वॉशिंग", low):
        e.scenario = "B_wm_ac"
    elif re.search(r"laptop|लैपटॉप", low):
        e.scenario = "C_laptop"
    if re.search(r"(associate|salesman|usne) (ne )?(pehle )?(price|daam)", low):
        e.price_first_by = "associate"
    elif re.search(r"(maine|customer ne|humne) (pehle )?(price|daam)", low):
        e.price_first_by = "customer"
    m = re.search(r"(\d+)\s*(?:min|minute|minat)", low)
    if m:
        e.minute_first_price = float(m.group(1))
    m = re.search(r"(\d+)\s*(?:sawaal|questions|prashn)", low)
    if m:
        e.questions_before_first_product = int(m.group(1))
    if re.search(r"demo (nahi|nahin|no)|no demo", low):
        e.demo_shown = False
    elif re.search(r"demo (dikhaya|diya|kiya|shown)", low):
        e.demo_shown = True
    if re.search(r"(wapas|vapas) (aaunga|aayenge|aaungi)|come back", low):
        e.would_come_back = True
    elif re.search(r"(wapas|vapas) nahi|won't come back|not come back", low):
        e.would_come_back = False
    for step, words in _STEP_WORDS.items():
        m = re.search(r"(" + "|".join(words) + r")\D{0,12}(\d)\s*(?:/\s*2|out of 2|mein se)?", low)
        if m and int(m.group(2)) <= 2:
            e.steps.append(StepScore(step=step, score=int(m.group(2))))  # type: ignore[arg-type]
    return e
