"""AIClient — the AI seam. The Google/Gemini model is intentionally NOT wired in this build.

Modes (AI_MODE):
  fixture — canned JSON from data/fixtures (tests, simulator; no network)
  rules   — deterministic offline understanding from pack keywords (default for live WhatsApp)

To plug a model later, implement the same five coroutines (understand, read_image, edit_image,
extract_visit, format_expert_answer) using the prompts in ai/prompts.py and schemas in ai/schemas.py."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from chanakya.ai import rules
from chanakya.ai.schemas import ExpertAnswer, ImageRead, Spec, StepScore, Understanding, VisitEntry
from chanakya.brain.packs import PackRegistry
from chanakya.config import ROOT, Settings
from chanakya.log import get_log

log = get_log("ai")
FIX_DIR = ROOT / "data" / "fixtures"


class AIUnavailable(Exception):
    """The requested capability needs a model that is not configured (e.g. voice transcription)."""


class AIClient:
    def __init__(self, s: Settings, packs: PackRegistry, fixtures: Path = FIX_DIR):
        self.mode = s.ai_mode if s.ai_mode in ("fixture", "rules") else "rules"
        self.packs = packs
        self.fix = fixtures
        if s.ai_mode not in ("fixture", "rules"):
            log.warning("ai_mode_unsupported", mode=s.ai_mode, using="rules")

    # ---------- 8.2 ----------
    async def understand(self, text: str | None, audio: bytes | None, ctx: dict) -> Understanding:
        if text and text.startswith("FIX:"):
            return self._fixture("understand", text[4:].strip(), Understanding)
        if audio is not None:
            key = hashlib.sha1(audio).hexdigest()
            if (self.fix / "understand" / f"{key}.json").exists():
                return self._fixture("understand", key, Understanding)
            raise AIUnavailable("voice")
        myths = [m["id"] for m in ctx.get("myths", [])] or None
        return rules.understand_text(
            text or "", self.packs, ctx.get("category"), myths, options_shown=bool(ctx.get("options_shown"))
        )

    # ---------- 8.3 ----------
    async def read_image(
        self, img: bytes, filename: str | None = None, caption: str | None = None, hint: str | None = None
    ) -> ImageRead:
        """Fixture by filename stem; else a caption/hint-based read (no vision model)."""
        if filename:
            stem = Path(filename).stem
            if (self.fix / "image_read" / f"{stem}.json").exists():
                return self._fixture("image_read", stem, ImageRead)
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
        return None  # no image model: try-on keeps the deterministic composite

    # ---------- 8.5 ----------
    async def extract_visit(self, text: str | None, audio: bytes | None) -> VisitEntry:
        if text and text.startswith("FIX:"):
            return self._fixture("visit", text[4:].strip(), VisitEntry)
        if audio is not None:
            raise AIUnavailable("voice")
        return _visit_rules(text or "")

    # ---------- 8.6 ----------
    async def format_expert_answer(
        self, text: str | None, audio: bytes | None, assoc_lang: str, cust_lang: str
    ) -> ExpertAnswer:
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
