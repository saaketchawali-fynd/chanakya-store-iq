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


LATEST = "gemini-flash-latest"  # Google's always-current alias: used only if both pinned models are retired (404)
LITE_LATEST = "gemini-flash-lite-latest"
HEDGE_STAGGER = 1.2  # seconds before the next model joins when one has no answer yet (target: reply < 5 s)


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
                kw["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")
        return types.GenerateContentConfig(**kw)

    async def json(self, parts: list, schema: type[BaseModel], system: str, timeout: float, kind: str) -> BaseModel:
        last_err = ""
        deadline = time.perf_counter() + timeout * 1.2  # never more than this in total, whatever fails
        not_found = 0
        for i, (model, share) in enumerate(((self.fast, 0.6), (self.fallback, 1.0), (LATEST, 1.0))):
            if i == 2 and (not_found < 2 or LATEST in (self.fast, self.fallback)):
                break  # the alias only when Google retired both pinned models
            for thinking in (True, False):  # some models reject the thinking config: retry without it
                t0 = time.perf_counter()
                t = min(timeout * share if model == self.fast else timeout, deadline - t0)
                if t < 0.8:
                    break
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
            not_found += "NOT_FOUND" in last_err or "404" in last_err[:8]
            log.warning("gemini_fail", model=model, kind=kind, err=last_err)
            if model == self.fast:  # why the main model was skipped (shown on /health)
                diag.mark("ai_fast_model_error", {"model": model, "kind": kind, "error": last_err[:200]})
        diag.bump("ai_errors")
        diag.mark(f"ai_last_{kind}", {"ok": False, "error": last_err})
        raise AIUnavailable(last_err)

    async def _call(self, model: str, parts: list, schema: type[BaseModel] | None, system: str):
        """One model, one answer: the parsed schema (or plain text). Retries once without the thinking config."""
        last: Exception | None = None
        for thinking in (True, False):
            try:
                r = await self.client.aio.models.generate_content(
                    model=model, contents=parts, config=self._config(model, system, schema, thinking))
                if schema is None:
                    text = (r.text or "").strip()
                    if not text:
                        raise ValueError("empty")
                    return text
                out = r.parsed if getattr(r, "parsed", None) is not None else schema.model_validate_json(r.text)
                return schema.model_validate(out) if isinstance(out, dict) else out
            except Exception as e:  # noqa: BLE001
                last = e
                if thinking and "think" in str(e).lower():
                    continue
                raise
        raise last or RuntimeError("no answer")

    async def hedged(self, parts: list, schema: type[BaseModel] | None, system: str, timeout: float, kind: str):
        """Voice notes: one busy or slow model must never lose the note. The main model starts at once; if it has
        no answer after a few seconds (or fails, e.g. 503 high demand), the next model joins, and so on. The first
        good answer wins; the rest are cancelled."""
        first = list(dict.fromkeys([self.fast, LATEST, self.fallback, LITE_LATEST]))
        models = first + first[:2]  # 503 'high demand' passes in seconds: the first two get a second try
        t0 = time.perf_counter()
        deadline = t0 + timeout
        running: dict[asyncio.Task, str] = {}
        errors: list[str] = []
        nxt = 0

        def launch() -> None:
            nonlocal nxt
            if nxt < len(models):
                m = models[nxt]
                nxt += 1
                running[asyncio.create_task(self._call(m, parts, schema, system))] = m

        launch()
        next_at = t0 + HEDGE_STAGGER
        try:
            while running:
                now = time.perf_counter()
                if now >= deadline:
                    break
                wait = min(deadline, next_at if nxt < len(models) else deadline) - now
                done, _ = await asyncio.wait(running, timeout=max(0.05, wait), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    model = running.pop(task)
                    if task.exception() is None:
                        ms = int((time.perf_counter() - t0) * 1000)
                        diag.mark(f"ai_last_{kind}", {"ok": True, "model": model, "ms": ms, "tried": nxt})
                        return task.result()
                    err = str(task.exception())[:160]
                    errors.append(f"{model}: {err}")
                    log.warning("gemini_fail", model=model, kind=kind, err=err)
                    if model == self.fast:
                        diag.mark("ai_fast_model_error", {"model": model, "kind": kind, "error": err})
                    launch()  # a failure: the next model now, not after the stagger
                    next_at = time.perf_counter() + HEDGE_STAGGER
                if not done and time.perf_counter() >= next_at:
                    launch()
                    next_at = time.perf_counter() + HEDGE_STAGGER
                if not running and nxt < len(models):
                    launch()
        finally:
            for task in running:
                task.cancel()
        diag.bump("ai_errors")
        msg = "; ".join(errors)[-300:] or f"timeout after {timeout:.0f}s"
        diag.mark(f"ai_last_{kind}", {"ok": False, "error": msg})
        raise AIUnavailable(msg)

    async def transcribe(self, parts: list, system: str, timeout: float) -> str:
        """Plain text out (no schema): the safety net when the structured read of a voice note fails."""
        return await self.hedged(parts, None, system, timeout, "transcribe")

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
