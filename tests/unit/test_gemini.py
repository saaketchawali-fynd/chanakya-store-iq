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


async def test_voice_note_understood_with_audio_part():
    u = Understanding(
        transcript="sofa se das feet",
        language="hi",
        category="tv",
        numbers=[{"name": "seat_distance_ft", "value": 10}],
        intents=["customer_info"],
    )
    ai, fm = live_client([ok(u)])
    out = await ai.understand(None, b"OggS...", {"category": "tv"}, mime="audio/ogg; codecs=opus")
    assert out.numbers[0].value == 10
    part = fm.calls[0]["contents"][0]
    assert part.inline_data.mime_type == "audio/ogg" and part.inline_data.data == b"OggS..."
    assert fm.calls[0]["config"].response_schema is Understanding
    assert diag.snapshot()["ai_last_understand_audio"]["ok"] is True


async def test_fallback_model_and_thinking_retry():
    u = Understanding(transcript="x", intents=["other"])
    ai, fm = live_client([Exception("thinking_level is not supported"), RuntimeError("503 overloaded"), ok(u)])
    out = await ai.understand(None, b"a", {})
    assert out.transcript == "x"
    models = [c["model"] for c in fm.calls]
    assert models == ["gemini-flash-latest", "gemini-flash-latest", "gemini-flash-lite-latest"]
    assert fm.calls[0]["config"].thinking_config is not None and fm.calls[1]["config"].thinking_config is None


async def test_retired_models_fall_back_to_the_latest_alias():
    u = Understanding(transcript="y", intents=["other"])
    gone = "404 NOT_FOUND. This model is no longer available to new users."
    ai = AIClient(Settings(_env_file=None, ai_mode="live", gemini_api_key="k"), PACKS)
    fm = FakeModels([RuntimeError(gone), RuntimeError(gone), ok(u)])
    ai.gemini.client = SimpleNamespace(aio=SimpleNamespace(models=fm))
    out = await ai.understand(None, b"a", {})
    assert out.transcript == "y"
    assert [c["model"] for c in fm.calls] == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]


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
    ai, fm = live_client([ok(room), ok(FactQuery(sku="TV-MER-55-QL120", field="hdmi_ports")), edited])
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
