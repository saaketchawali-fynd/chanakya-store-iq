"""Binding contracts (spec Section 5). Keep field names exactly; see CONTRACTS.md."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Lang = Literal["hi", "mr", "en"]
Category = Literal["tv", "ac", "wm", "laptop"]
Role = Literal["associate", "manager", "expert", "visitor", "customer", "unknown"]


# ---------- Inbound (normalised from the WhatsApp webhook) ----------
class InboundMessage(BaseModel):
    wa_message_id: str
    from_wa_id: str  # digits, e.g. "917225882392"
    profile_name: str | None = None
    timestamp: datetime
    type: Literal[
        "text",
        "audio",
        "image",
        "button_reply",
        "list_reply",
        "template_button",
        "document",
        "video",
        "location",
        "unsupported",
    ]
    text: str | None = None  # text body, or button/list title
    reply_id: str | None = None  # button/list reply id
    media_id: str | None = None  # audio/image/document id
    mime_type: str | None = None
    is_voice: bool = False
    context_message_id: str | None = None  # message being replied to
    received_at: datetime  # server receipt time (latency t0)


# ---------- Outbound ----------
class Button(BaseModel):
    id: str = Field(max_length=256)
    title: str = Field(max_length=20)


class ListRow(BaseModel):
    id: str = Field(max_length=200)
    title: str = Field(max_length=24)
    description: str | None = Field(default=None, max_length=72)


class ListSection(BaseModel):
    title: str | None = Field(default=None, max_length=24)
    rows: list[ListRow]


class Card(BaseModel):
    template: Literal["home", "benefit", "options", "kharcha", "parivaar", "score", "brief", "digest", "catalog", "product", "compare"]
    lang: Lang  # language of the card text
    data: dict[str, Any]  # template variables (numbers already formatted via firewall)


class OutMessage(BaseModel):
    to: str
    kind: Literal["text", "buttons", "list", "image", "audio", "video", "document", "template", "reaction", "cta_url"]
    body: str | None = None  # ≤ 3 lines (validator)
    footer: str | None = None  # ≤ 60 chars
    buttons: list[Button] = []  # ≤ 3
    list_button: str | None = None  # ≤ 20 chars
    sections: list[ListSection] = []  # ≤ 10 rows total
    card: Card | None = None  # rendered to an image (standalone image, or header of a buttons message)
    image_bytes: bytes | None = None  # e.g. try-on output, QR
    media_id: str | None = None  # pre-uploaded media
    caption: str | None = None  # image/video/document caption (≤ 3 lines)
    filename: str | None = None  # document
    voice: bool = False  # audio as voice note (OGG/OPUS)
    template_name: str | None = None
    template_lang: str | None = None
    template_components: list[dict] = []
    reaction_to: str | None = None
    emoji: str | None = None
    url: str | None = None  # cta_url
    url_text: str | None = None
    # --- safety metadata (not sent) ---
    coach: bool = False  # True → must contain a Kyun line citing ≥1 slot
    why_cites: list[str] = []  # slot ids cited by the Kyun line
    allowed_numbers: list[str] = []  # filled by NumberRegistry (normalised numerals)


class Event(BaseModel):
    type: str  # see Section 7.9 for the list
    session_id: str | None = None
    actor_wa_id: str | None = None
    step: str | None = None
    payload: dict[str, Any] = {}
    sources: list[str] = []  # data row ids behind numbers
    latency_ms: int | None = None


class Reply(BaseModel):
    messages: list[OutMessage] = []
    events: list[Event] = []
    background: list[str] = []  # names of background jobs to start (e.g. "tryon", "parivaar_voice")


# ---------- Session / Home Card ----------
class Step(str, Enum):  # noqa: UP042 — binding contract (spec 5)
    ASSESS = "assess"
    RECOMMEND = "recommend"
    DEMONSTRATED = "demonstrated"
    COMPLETE = "complete"
    CLOSE = "close"
    FOLLOW = "follow"
    CLOSED = "closed"


class SlotValue(BaseModel):
    slot: str
    value: str  # allowed value id from the pack (or "num" for numeric)
    number: float | None = None  # numeric value when relevant (ft, sqft, kg, inr)
    source: Literal["voice", "tap", "text", "photo", "default"] = "voice"
    at: datetime


class HomeCard(BaseModel):
    category: Category | None = None
    customer_name: str | None = None
    customer_lang: Lang = "hi"
    slots: dict[str, list[SlotValue]] = {}  # multi-valued slots allowed (e.g. content)
    start_level_inr: int | None = None  # ONLY from associate statement/tap
    start_level_source: Literal["associate_voice", "associate_tap", "none"] = "none"
    sports_viewer: str | None = None  # e.g. "papa" (for personal lines)
    notes: list[str] = []


class SessionState(BaseModel):
    id: str
    associate_wa_id: str
    code: str  # "C1"
    step: Step = Step.ASSESS
    home: HomeCard
    shortlist: list[str] = []  # SKUs [best, value, stepup]
    option_labels: dict[str, str] = {}  # {"A": sku, "B": sku, "C": sku}
    chosen_sku: str | None = None
    demo_done: bool = False
    demo_recipe: str | None = None
    tryon_done: bool = False
    overrides: list[dict] = []  # {"gate": "options|price", "reason": "...", "at": iso}
    consent: dict | None = None  # {"method": "qr|tap", "at": iso, "customer_wa_id": "..."}
    customer_wa_id: str | None = None
    optin_token: str | None = None
    cost_snapshot: dict | None = None
    offer_snapshot: dict | None = None
    awaiting: str | None = None  # e.g. "room_photo", "voice_for_parivaar", "wall_width"
    created_at: datetime
    updated_at: datetime
    # --- additive working state (not in the original contract; does not rename anything) ---
    extra: dict[str, Any] = {}
