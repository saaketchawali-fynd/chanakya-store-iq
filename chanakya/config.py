"""Settings (pydantic-settings). Read from env / .env. Secrets never committed."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_business_account_id: str = ""
    wa_app_secret: str = ""
    wa_verify_token: str = "chanakya-verify"
    wa_api_version: str = "v23.0"
    wa_test_number: str = "15551234567"

    # AI: the Google/Gemini model is intentionally not wired in this build.
    #   fixture → canned JSON from data/fixtures (tests, simulator)
    #   rules   → deterministic offline understanding (keywords from packs) — default for live WhatsApp
    ai_mode: str = "auto"  # auto = Gemini when GEMINI_API_KEY is set, else offline rules
    gemini_api_key: str = ""
    gemini_fast_model: str = "gemini-3.8-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_image_model: str = "gemini-nano-banana-2.1"

    # Live (docs/live-mode-plan.md): the in-store whisper copilot web page /live. Off unless LIVE_ENABLED=true.
    live_enabled: bool = False
    live_key: str = ""  # if set, the page needs ?k=<key>
    live_provider: str = "auto"  # auto (gemini → openai → browser) | gemini | openai | browser
    live_max_sessions: int = 4
    gemini_live_model: str = "gemini-live-2.5-flash-preview"
    openai_api_key: str = ""
    openai_transcribe_model: str = "gpt-4o-transcribe"

    # Product, price and offer data. "jio" (default): only the Jio Commerce storefront API (getProducts,
    # getPromotionOffers); nothing else is ever loaded, and with no token the catalogue stays empty.
    # "seed": data/seed/*.yaml, for the automated tests and the offline simulator only.
    data_source: str = "jio"
    jio_domain: str = "https://api.jiox5.de"
    jio_app_id: str = ""
    jio_app_token: str = ""
    jio_store_id: int | None = None  # optional store uid passed to getPromotionOffers
    jio_sync_minutes: int = 30
    store_name: str = "Reliance Digital"

    database_url: str = "sqlite+aiosqlite:///./chanakya.db"
    public_base_url: str = "http://localhost:8000"
    admin_token: str = "change-me"
    tz_name: str = "Asia/Kolkata"
    store_id: str = "RD-JUHU"

    associate_phone: str = ""
    associate_name: str = ""  # no dummy name: the associate is asked their name at first contact
    extra_associate_phones: str = ""  # more associates (demo), comma-separated: '919870279367, 9876543210'
    customer_phone: str = ""
    manager_phone: str = ""
    manager_name: str = "Meena"
    expert_phone: str = ""
    expert_name: str = "Sunil"
    visitor_phone: str = ""

    demo_mode: bool = True
    demo_role_switch_allowed: str = ""
    harmonise_tryon: bool = False  # off: the blend adds ~10 s; the composite is realistic and true to scale
    render_cards: bool = True  # False → skip Playwright (falls back to Pillow cards)
    first_reply_hold_s: float = 3.5  # floor rule: first reply < 5 s; a short holding line goes out if nothing has by then
    scheduler_enabled: bool = True
    log_level: str = "INFO"
    privacy_contact: str = ""  # shown on /privacy (optional)

    @field_validator("database_url")
    @classmethod
    def _normalise_db(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    @field_validator(
        "associate_phone", "customer_phone", "manager_phone", "expert_phone", "visitor_phone", "wa_test_number"
    )
    @classmethod
    def _digits_only(cls, v: str) -> str:
        """'+91 98765-43210' / '09876543210' / '9876543210' → '919876543210' (WhatsApp sends senders as
        country code + number, digits only). A bare 10-digit number is taken as Indian."""
        d = "".join(ch for ch in (v or "") if ch.isdigit())
        if d.startswith("00"):
            d = d[2:]
        if len(d) == 11 and d.startswith("0"):
            d = d[1:]
        if len(d) == 10:
            d = "91" + d
        return d

    @property
    def associate_phones(self) -> list[str]:
        """Every associate number: ASSOCIATE_PHONE first, then EXTRA_ASSOCIATE_PHONES, normalised, no repeats."""
        out = [self.associate_phone] + [self._digits_only(p) for p in self.extra_associate_phones.split(",")]
        return list(dict.fromkeys(p for p in out if p))

    @property
    def role_switch_phones(self) -> set[str]:
        return {self._digits_only(p) for p in self.demo_role_switch_allowed.split(",") if p.strip()}

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
