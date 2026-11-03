"""Gemini wiring with a fake SDK client (no network): fallback model, thinking retry, rules fallback for text,
clean failure for voice, room geometry, fact mapping, image editing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chanakya.ai.client import AIClient, AIUnavailable
from chanakya.ai.schemas import FactQuery, ImageRead, Understanding
from chanakya.app import diag
from chanakya.brain.packs import load_packs
from chanakya.config import Settings

PACKS = load_packs()


class FakeModels:
    def __init__(self, script):
        self.script = list(script)  # each item: an object to return, or an Exception to raise
        self.calls = []

    async def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def live_client(script) -> tuple[AIClient, FakeModels]:
    ai = AIClient(
        Settings(
            _env_file=None,
            ai_mode="live",
            gemini_api_key="test-key",
            gemini_fast_model="gemini-flash-latest",
            gemini_fallback_model="gemini-flash-lite-latest",
        ),
        PACKS,
    )
    fm = FakeModels(script)
    ai.gemini.client = SimpleNamespace(aio=SimpleNamespace(models=fm))
    return ai, fm


def ok(obj):
    return SimpleNamespace(parsed=obj, text=obj.model_dump_json())


def test_mode_selection():
    assert AIClient(Settings(_env_file=None, ai_mode="auto", gemini_api_key=""), PACKS).mode == "rules"
    assert AIClient(Settings(_env_file=None, ai_mode="auto", gemini_api_key="k"), PACKS).mode == "live"
    assert AIClient(Settings(_env_file=None, ai_mode="live", gemini_api_key=""), PACKS).mode == "rules"
    assert AIClient(Settings(_env_file=None, ai_mode="fixture", gemini_api_key="k"), PACKS).mode == "fixture"


def said(text: str):
    """A plain-text answer (the transcript call has no schema)."""
    return SimpleNamespace(parsed=None, text=text)


class ByModel:
    """A fake Gemini that answers per model and per kind of request (transcript: no schema; meaning: a schema), as
    the real service does: calls now run two at a time, so a fixed answer order would not hold."""

    def __init__(self, plan: dict, default=None):
        self.plan, self.default, self.calls = plan, default, []

    async def generate_content(self, model, contents, config):
        import asyncio

        self.calls.append({"model": model, "contents": contents, "config": config})
        kind = "text" if config.response_schema is None else "json"
        item = self.plan.get((model, kind), self.plan.get(model, self.default))
        if callable(item):
            item = item(config)
        if isinstance(item, (int, float)):
            await asyncio.sleep(item)  # a hanging model
            raise RuntimeError("timeout")
        if isinstance(item, Exception):
            raise item
        return item


def client(models) -> AIClient:
    ai = AIClient(Settings(_env_file=None, ai_mode="live", gemini_api_key="k"), PACKS)
    ai.gemini.client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return ai


async def test_voice_note_understood_with_audio_part():
    """Voice in two steps (3 Nov 2026 RCA): the audio goes only to the light transcript call; the meaning is read
    from the words (a text call with the schema)."""
    u = Understanding(transcript="sofa se das feet", language="hi", category="tv",
                      numbers=[{"name": "seat_distance_ft", "value": 10}], intents=["customer_info"])
    fm = ByModel({}, default=lambda cfg: said("sofa se das feet") if cfg.response_schema is None else ok(u))
    ai = client(fm)
    out = await ai.understand(None, b"OggS...", {"category": "tv"}, mime="audio/ogg; codecs=opus")
    assert out.numbers[0].value == 10 and out.transcript == "sofa se das feet"
    audio = [c for c in fm.calls if c["config"].response_schema is None]
    text = [c for c in fm.calls if c["config"].response_schema is Understanding]
    part = audio[0]["contents"][0]
    assert part.inline_data.mime_type == "audio/ogg" and part.inline_data.data == b"OggS..."
    assert text and text[0]["contents"][0] == "sofa se das feet"  # the meaning from the words
    assert diag.snapshot()["ai_last_transcribe"]["ok"] is True


async def test_the_quickest_models_start_together():
    """4 Nov 2026 on the server: the lite model answered in ~2 s, the bigger one took > 4 s. Both start at once."""
    import asyncio

    fm = ByModel({"gemini-3.8-flash": 5.0, ("gemini-3.5-flash-lite", "text"): said("ek do teen")},
                 default=RuntimeError("down"))
    ai = client(fm)
    t0 = asyncio.get_event_loop().time()
    text = await ai.hear(b"OggS-fast")
    assert text == "ek do teen" and asyncio.get_event_loop().time() - t0 < 1.0
    assert {c["model"] for c in fm.calls[:2]} == {"gemini-3.5-flash-lite", "gemini-3.8-flash"}


async def test_fallback_model_and_thinking_retry():
    seen = {"n": 0}

    def lite(cfg):
        seen["n"] += 1
        if seen["n"] == 1:
            return Exception("thinking_level is not supported")  # first try: retried without the thinking config
        return said("x") if cfg.response_schema is None else RuntimeError("down")

    fm = ByModel({"gemini-3.5-flash-lite": lite}, default=RuntimeError("503 overloaded"))
    ai = client(fm)
    out = await ai.understand(None, b"a", {})
    assert out.transcript == "x"  # heard; the meaning from the offline rules while the text models are down
    lite_calls = [c for c in fm.calls if c["model"] == "gemini-3.5-flash-lite"]
    assert lite_calls[0]["config"].thinking_config is not None and lite_calls[1]["config"].thinking_config is None


async def test_retired_models_fall_back_to_the_latest_alias():
    u = Understanding(transcript="y", intents=["other"])
    gone = RuntimeError("404 NOT_FOUND. This model is no longer available to new users.")
    fm = ByModel({"gemini-3.8-flash": gone, "gemini-3.5-flash-lite": gone},
                 default=lambda cfg: said("y") if cfg.response_schema is None else ok(u))
    ai = client(fm)
    out = await ai.understand(None, b"a", {})
    assert out.transcript == "y"
    assert {c["model"] for c in fm.calls} >= {"gemini-flash-lite-latest"}  # the always-current alias answered


async def test_busy_model_never_loses_a_voice_note():
    """3 Nov 2026 on production: the main model said 503 (high demand) and others hung. The first good transcript
    wins; the meaning comes from the offline rules when the text models are down too."""
    import time

    busy = RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
    fm = ByModel({"gemini-3.8-flash": busy, "gemini-3.5-flash-lite": 30.0,
                  ("gemini-flash-lite-latest", "text"): said("sofa se das feet, dhoop aati hai")}, default=busy)
    ai = client(fm)
    t0 = time.perf_counter()
    out = await ai.understand(None, b"OggS", {"category": "tv"}, mime="audio/ogg")
    assert out.transcript.startswith("sofa se das feet") and time.perf_counter() - t0 < 9
    assert ("room_light", "sunny") in {(s.slot, s.value) for s in out.slots}  # understood even with no model


async def test_text_falls_back_to_rules_but_voice_fails_cleanly():
    ai, _ = live_client([RuntimeError("down")] * 4)
    u = await ai.understand("sofa se das feet, dhoop aati hai", None, {"category": "tv"})
    assert {(s.slot, s.value) for s in u.slots} >= {("room_light", "sunny")}
    ai2, _ = live_client([RuntimeError("down")] * 4)
    with pytest.raises(AIUnavailable):
        await ai2.understand(None, b"audio", {"category": "tv"})


async def test_room_geometry_and_fact_mapping_and_edit():
    room = ImageRead(
        kind="room",
        boxes=[{"label": "wall", "box_2d": [100, 100, 700, 900]}],
        wall_corners=[{"x": 100, "y": 100}, {"x": 900, "y": 120}, {"x": 900, "y": 700}, {"x": 100, "y": 690}],
        floor_y=700,
        ceiling_y=90,
        light="moderate",
        frontal=False,
    )
    edited = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(inline_data=None),
                        SimpleNamespace(inline_data=SimpleNamespace(data=b"JPEG")),
                    ]
                )
            )
        ]
    )
    ai, fm = live_client([ok(room), ok(room), ok(FactQuery(sku="TV-MER-55-QL120", field="hdmi_ports")), edited])  # room read: two models at once
    r = await ai.read_image(b"jpg", None, None, hint="room")
    assert r.kind == "room" and len(r.wall_corners) == 4 and r.floor_y == 700
    assert "wall_corners" in fm.calls[0]["config"].system_instruction
    fq = await ai.map_fact_question(
        "Meru 55 mein kitne HDMI?",
        [{"sku": "TV-MER-55-QL120", "model": "Meru 55 QLED Pro"}],
        [{"id": "hdmi_ports", "means": "HDMI ports"}],
    )
    assert fq.field == "hdmi_ports"
    assert await ai.edit_image(b"jpg", "harmonise") == b"JPEG"
    rules_ai = AIClient(Settings(_env_file=None, ai_mode="rules"), PACKS)
    assert await rules_ai.map_fact_question("x", [], []) is None and await rules_ai.edit_image(b"x", "p") is None
