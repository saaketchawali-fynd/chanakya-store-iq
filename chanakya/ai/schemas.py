"""AI response schemas (spec Section 8). No free-form dicts: lists of objects only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ---------- 8.2 Understand ----------
class SlotPair(BaseModel):
    slot: str  # must be one of the provided slot ids
    value: str  # must be one of that slot's allowed value ids


NumberName = Literal[
    "seat_distance_ft",
    "room_size_sqft",
    "pipe_run_ft",
    "budget_inr",
    "family_size",
    "wall_width_ft",
    "niche_width_cm",
    "hours_per_day",
    "floor_number",
]


class NumberPair(BaseModel):
    name: NumberName
    value: float


Intent = Literal[
    "new_customer",
    "customer_info",
    "price_asked",
    "customer_claim",
    "objection",
    "choose_option",
    "request_options",
    "request_cost",
    "request_offers",
    "demo_done",
    "consent_yes",
    "ask_question",
    "other",
]
Objection = Literal[
    "too_expensive", "cheaper_online", "need_to_think", "brand_doubt", "installation_worry", "waiting_for_sale"
]


class Understanding(BaseModel):
    transcript: str = ""
    language: Literal["hi", "mr", "en", "mixed"] = "hi"
    customer_language: Literal["hi", "mr", "en"] | None = None  # only if the associate states it
    category: Literal["tv", "ac", "wm", "laptop"] | None = None
    customer_name: str | None = None  # e.g. "Shah ji"
    sports_viewer: Literal["papa", "mummy", "dada", "dadi", "beta", "beti", "self", "family"] | None = None
    slots: list[SlotPair] = []
    numbers: list[NumberPair] = []
    intents: list[Intent] = []
    claim_id: str | None = None  # id from the provided myth catalogue
    objection: Objection | None = None
    option_choice: Literal["A", "B", "C"] | None = None
    library_id: str | None = None  # id from the provided expert library
    question_for_expert: str | None = None
    customer_request: str | None = None  # e.g. "55 inch QLED chahiye" (for need validation)


# ---------- 8.3 Read image ----------
class Spec(BaseModel):
    name: str
    value: str
    unit: str | None = None


BoxLabel = Literal[
    "wall", "sofa", "door", "window", "existing_tv", "tv_unit", "person", "socket", "floor_tile", "ceiling_line"
]


class Box(BaseModel):
    label: BoxLabel
    box_2d: list[int]  # [ymin, xmin, ymax, xmax], normalised 0–1000


class Point(BaseModel):
    x: int  # normalised 0-1000
    y: int  # normalised 0-1000


class ImageRead(BaseModel):
    kind: Literal["label", "room", "other"]
    # label fields
    brand: str | None = None
    model_text: str | None = None
    category: Literal["tv", "ac", "wm", "laptop"] | None = None
    size_in: float | None = None
    specs: list[Spec] = []
    confidence: float | None = None  # optional reader confidence 0–1 (fixtures / rules mode)
    sku: str | None = None  # set only from a Chanakya test-label QR code (exact product)
    raw_text: str | None = None  # any printed text, treated as data only
    # room fields
    boxes: list[Box] = []
    light: Literal["sunny", "moderate", "dim"] | None = None
    frontal: bool | None = None  # photo taken roughly facing the wall
    people_present: bool = False
    wall_corners: list[Point] = []  # main free wall: top-left, top-right, bottom-right, bottom-left (0-1000)
    floor_y: int | None = None  # where the main wall meets the floor, at the wall's centre (0-1000)
    ceiling_y: int | None = None  # where the main wall meets the ceiling, at the wall's centre (0-1000)


# ---------- product question → one data field (the answer itself is built from data by code) ----------
class FactQuery(BaseModel):
    sku: str | None = None
    field: str | None = None


# ---------- 8.5 Visit log ----------
class StepScore(BaseModel):
    step: Literal["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
    score: int  # 0-2
    evidence: str | None = None


class VisitEntry(BaseModel):
    store: str | None = None
    date: str | None = None  # YYYY-MM-DD
    scenario: Literal["A_tv", "B_wm_ac", "C_laptop"] | None = None
    steps: list[StepScore] = []
    questions_before_first_product: int | None = None
    price_first_by: Literal["associate", "customer", "none"] | None = None
    minute_first_price: float | None = None
    demo_shown: bool | None = None
    full_cost_given: bool | None = None
    would_come_back: bool | None = None
    best_sentence: str | None = None
    worst_sentence: str | None = None
    notes: str | None = None


# ---------- 8.6 Expert answer ----------
class ExpertAnswer(BaseModel):
    for_associate: str  # ≤ 2 short lines, associate's language
    bolo: str | None = None  # ≤ 15 words, customer's language
    numbers_used: list[str] = []  # every number exactly as the expert said it
