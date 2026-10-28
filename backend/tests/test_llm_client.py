from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import settings
from app.llm.client import LLMConfigError, LLMError, LiveLLM
from app.llm.profiles import ProviderProfile
from app.llm.trace import TraceCollector


class Output(BaseModel):
    ok: bool


def _profile(profile_id: str, *, supports_json_mode: bool = True) -> ProviderProfile:
    return ProviderProfile(
        id=profile_id,
        label=profile_id,
        base_url="https://example.test/v1",
        model=f"model-{profile_id}",
        api_key="test-key",
        supports_json_mode=supports_json_mode,
    )


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


async def test_fallback_to_next_profile(monkeypatch):
    trace = TraceCollector()
    llm = LiveLLM(
        trace,
        "extract",
        [_profile("primary"), _profile("backup")],
        config=replace(settings, llm_deadline_s=10, llm_timeout_s=5),
    )

    async def fake_call(profile, messages, temperature):
        if profile.id == "primary":
            raise RuntimeError("primary is down")
        return _response('{"ok": true}')

    monkeypatch.setattr(llm, "_call", fake_call)

    result = await llm.chat_json("test", "system", "user", Output)

    assert result.ok is True
    assert [(item.profile, item.success) for item in trace.traces] == [
        ("primary", False),
        ("backup", True),
    ]
    assert trace.traces[-1].model == "model-backup"


async def test_all_profiles_fail(monkeypatch):
    trace = TraceCollector()
    llm = LiveLLM(
        trace,
        "extract",
        [_profile("primary"), _profile("backup")],
        config=replace(settings, llm_deadline_s=10, llm_timeout_s=5),
    )

    async def fake_call(profile, messages, temperature):
        raise RuntimeError(f"{profile.id} is down")

    monkeypatch.setattr(llm, "_call", fake_call)

    with pytest.raises(LLMError, match="已尝试 2 个 profile"):
        await llm.chat_json("test", "system", "user", Output)

    assert [item.success for item in trace.traces] == [False, False]


def test_json_mode_flag_is_per_profile():
    trace = TraceCollector()
    llm = LiveLLM(
        trace,
        "extract",
        [_profile("no-json", supports_json_mode=False), _profile("json")],
    )
    assert llm._json_mode == {"no-json": False, "json": True}


def test_empty_profiles_raise():
    with pytest.raises(LLMConfigError):
        LiveLLM(TraceCollector(), "extract", [])
