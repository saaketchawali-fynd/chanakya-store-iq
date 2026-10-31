"""Gemini calls (spec 8.1): schema-bound JSON with a fast model and a fallback, timeouts, image editing.
The model only extracts and classifies; it never chooses products or writes numbers shown to users."""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel

from chanakya.app import diag
from chanakya.config import Settings
from chanakya.log import get_log

log = get_log("gemini")


class AIUnavailable(Exception):
    """The requested capability needs a model that is not configured or did not answer in time."""


class GeminiClient:
    def __init__(self, s: Settings):
        from google import genai

        self.client = genai.Client(api_key=s.gemini_api_key)
        self.fast, self.fallback, self.image = s.gemini_fast_model, s.gemini_fallback_model, s.gemini_image_model

    def _config(self, model: str, system: str, schema: type[BaseModel] | None, thinking: bool = True):
        from google.genai import types

        kw: dict = {"system_instruction": system, "temperature": 0.1}
        if schema is not None:
            kw.update(response_mime_type="application/json", response_schema=schema)
        if thinking:  # keep latency low: no / minimal thinking
            if "2.5" in model:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            else:
                kw["thinking_config"] = types.ThinkingConfig(thinking_level="low")
        return types.GenerateContentConfig(**kw)

    async def json(self, parts: list, schema: type[BaseModel], system: str, timeout: float, kind: str) -> BaseModel:
        last_err = ""
        for model, t in ((self.fast, timeout), (self.fallback, timeout * 0.7)):
            for thinking in (True, False):  # some models reject the thinking config: retry without it
                t0 = time.perf_counter()
                try:
                    r = await asyncio.wait_for(
                        self.client.aio.models.generate_content(
                            model=model, contents=parts, config=self._config(model, system, schema, thinking)
                        ),
                        t,
                    )
                    out = r.parsed if getattr(r, "parsed", None) is not None else schema.model_validate_json(r.text)
                    if isinstance(out, dict):
                        out = schema.model_validate(out)
                    ms = int((time.perf_counter() - t0) * 1000)
                    diag.mark(f"ai_last_{kind}", {"ok": True, "model": model, "ms": ms})
                    return out
                except TimeoutError:
                    last_err = f"timeout after {t:.1f}s"
                    break  # a timeout is not a config problem: go to the fallback model
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)[:200]
                    if thinking and "think" in last_err.lower():
                        continue
                    break
            log.warning("gemini_fail", model=model, kind=kind, err=last_err)
        diag.bump("ai_errors")
        diag.mark(f"ai_last_{kind}", {"ok": False, "error": last_err})
        raise AIUnavailable(last_err)

    async def edit_image(self, image_bytes: bytes, prompt: str, timeout: float = 25) -> bytes | None:
        from google.genai import types

        t0 = time.perf_counter()
        try:
            r = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.image,
                    contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                ),
                timeout,
            )
            for p in r.candidates[0].content.parts:
                if getattr(p, "inline_data", None) and p.inline_data.data:
                    diag.mark("ai_last_edit", {"ok": True, "ms": int((time.perf_counter() - t0) * 1000)})
                    return p.inline_data.data
        except Exception as e:  # noqa: BLE001
            log.warning("gemini_edit_fail", err=str(e)[:200])
            diag.mark("ai_last_edit", {"ok": False, "error": str(e)[:160]})
        return None
