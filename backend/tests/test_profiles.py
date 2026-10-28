from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.llm import profiles
from app.llm.profiles import RouteNotFound

PROFILES_YAML = """
version: 1
default_route: main

providers:
  p1:
    label: Provider One
    base_url: https://one.example/v1
    model: model-one
    api_key_env: TEST_KEY_A
  p2:
    label: Provider Two
    base_url: https://two.example/v1
    model: model-two
    api_key_env: TEST_KEY_B
  p3:
    label: Provider Three
    base_url: https://three.example/v1
    model: model-three
    api_key_env: TEST_KEY_C
  disabled-provider:
    label: Disabled Provider
    base_url: https://off.example/v1
    model: model-off
    api_key_env: TEST_KEY_A
    enabled: false

routes:
  main:
    label: Main Route
    description: test route
    default: [p1, p2, p3]
    tasks:
      extract: [p2]
      agent_liability: [p3]
  disabled:
    label: Disabled Route
    default: [disabled-provider]
"""


def _write(tmp_path: Path, text: str = PROFILES_YAML) -> Path:
    path = tmp_path / "llm_profiles.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _config(path: Path, **overrides):
    return replace(settings, llm_profiles_path=str(path), **overrides)


def test_registry_resolves_task_chains_and_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_A", "key-a")
    monkeypatch.setenv("TEST_KEY_B", "key-b")
    monkeypatch.delenv("TEST_KEY_C", raising=False)
    path = _write(tmp_path)
    registry = profiles.load_registry(_config(path), path=path)

    route = registry.resolve("main")
    assert registry.legacy is False
    assert route.available is True
    assert [profile.id for profile in route.profiles_for("extract")] == ["p2", "p1"]
    assert [profile.id for profile in route.profiles_for("agent_liability")] == ["p1", "p2"]
    assert [profile.id for profile in route.profiles_for("redline")] == ["p1", "p2"]
    assert route.models_by_task()["extract"] == ["model-two", "model-one"]
    assert "key-a" not in repr(registry.providers["p1"])

    disabled = registry.resolve("disabled")
    assert disabled.available is False
    assert "未启用 disabled-provider" in disabled.reason


def test_registry_missing_key_route_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY_C", raising=False)
    path = _write(
        tmp_path,
        """
default_route: only_missing
providers:
  p3:
    base_url: https://three.example/v1
    model: model-three
    api_key_env: TEST_KEY_C
routes:
  only_missing:
    default: [p3]
""",
    )
    registry = profiles.load_registry(_config(path), path=path)
    route = registry.resolve("only_missing")
    assert route.available is False
    assert "TEST_KEY_C" in route.reason


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_BASE", "https://override.example/v1")
    monkeypatch.setenv("TEST_MODEL", "override-model")
    path = _write(
        tmp_path,
        """
providers:
  env_provider:
    base_url: https://original.example/v1
    base_url_env: TEST_BASE
    model: original-model
    model_env: TEST_MODEL
routes:
  main:
    default: [env_provider]
""",
    )
    registry = profiles.load_registry(_config(path), path=path)
    provider = registry.providers["env_provider"]
    assert provider.base_url == "https://override.example/v1"
    assert provider.model == "override-model"


def test_legacy_registry_when_file_missing(tmp_path):
    config = replace(
        settings,
        api_key="legacy-key",
        base_url="https://legacy.example/v1",
        llm_model="legacy-model",
    )
    registry = profiles.load_registry(config, path=tmp_path / "missing.yaml")
    assert registry.legacy is True
    route = registry.resolve(None)
    assert route.id == "legacy"
    assert route.available is True
    assert route.models_by_task()["extract"] == ["legacy-model"]


def test_default_route_falls_back_to_first_available(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_A", "key-a")
    monkeypatch.delenv("TEST_KEY_C", raising=False)
    path = _write(tmp_path)
    config = _config(path, llm_route="only_missing")
    # only_missing 不存在，_pick_default 应回到第一个可用路由 main
    registry = profiles.load_registry(config, path=path)
    assert registry.effective_default_route_id == "main"


def test_chat_runtime_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_A", "key-a")
    monkeypatch.setenv("TEST_KEY_B", "key-b")
    monkeypatch.delenv("TEST_KEY_C", raising=False)
    path = _write(tmp_path)
    profiles.reset_registry()
    live = profiles.chat_runtime(_config(path, llm_mode="live"))
    assert live.mode == "live"
    assert live.default_route is not None
    assert live.default_route.id == "main"

    mock = profiles.chat_runtime(_config(path, llm_mode="mock"))
    assert mock.mode == "mock"
    assert "mock" in mock.reason


def test_chat_runtime_mock_when_no_available_route(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY_C", raising=False)
    path = _write(
        tmp_path,
        """
providers:
  p3:
    base_url: https://three.example/v1
    model: model-three
    api_key_env: TEST_KEY_C
routes:
  main:
    default: [p3]
""",
    )
    profiles.reset_registry()
    runtime = profiles.chat_runtime(_config(path, llm_mode="live"))
    assert runtime.mode == "mock"
    assert "没有可用模型路由" in runtime.reason


def test_unknown_route_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY_A", "key-a")
    path = _write(tmp_path)
    registry = profiles.load_registry(_config(path), path=path)
    with pytest.raises(RouteNotFound):
        registry.resolve("missing-route")
