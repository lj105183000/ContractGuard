"""多 LLM 供应商 profile 与按任务路由。

配置只保存环境变量名，不保存 API Key 值；API 层只暴露路由元数据。
路由的 default 是全局回退链，tasks 是各任务的优先前缀：
实际链 = tasks[task] + default（去重、过滤未配置/未启用的 provider）。
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.config import Settings, settings

logger = logging.getLogger(__name__)

TASK_KEYS = (
    "extract",
    "agent_commercial",
    "agent_liability",
    "agent_compliance",
    "redline",
)
MAX_PROFILES_PER_TASK = 3


class RouteError(RuntimeError):
    """路由解析失败。"""


class RouteNotFound(RouteError):
    """路由 ID 不存在。"""


class RouteUnavailableError(RouteError):
    """路由存在但没有可用 Provider。"""


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    base_url: str
    model: str
    api_key_env: str = ""
    api_key: str = field(default="", repr=False)
    requires_key: bool = True
    supports_json_mode: bool = True
    enabled: bool = True

    @property
    def available(self) -> bool:
        if not self.enabled or not self.base_url or not self.model:
            return False
        return (not self.requires_key) or bool(self.api_key)


@dataclass(frozen=True)
class RouteSpec:
    id: str
    label: str
    description: str
    default: tuple[str, ...]
    tasks: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class ResolvedRoute:
    id: str
    label: str
    description: str
    default_profiles: tuple[ProviderProfile, ...]
    task_profiles: Mapping[str, tuple[ProviderProfile, ...]]
    available: bool
    reason: str
    models: Mapping[str, list[str]]

    def profiles_for(self, task: str) -> tuple[ProviderProfile, ...]:
        return self.task_profiles.get(task, ())

    def models_by_task(self) -> dict[str, list[str]]:
        return {task: list(self.models.get(task, ())) for task in TASK_KEYS}


@dataclass(frozen=True)
class ChatRuntime:
    mode: str
    reason: str
    default_route: ResolvedRoute | None
    routes: tuple[ResolvedRoute, ...]


class ProfileRegistry:
    def __init__(
        self,
        providers: Mapping[str, ProviderProfile],
        route_specs: Mapping[str, RouteSpec],
        default_route_id: str | None,
        *,
        legacy: bool = False,
        warnings: list[str] | None = None,
    ) -> None:
        self.providers = dict(providers)
        self.route_specs = dict(route_specs)
        self.default_route_id = default_route_id
        self.legacy = legacy
        self.warnings = list(warnings or [])
        self._resolved = {
            route_id: self._resolve_spec(spec)
            for route_id, spec in self.route_specs.items()
        }
        self.effective_default_route_id = self._pick_default()

    def _task_chain(self, spec: RouteSpec, task: str) -> list[ProviderProfile]:
        chain: list[ProviderProfile] = []
        seen: set[str] = set()
        for provider_id in list(spec.tasks.get(task, ())) + list(spec.default):
            if provider_id in seen:
                continue
            seen.add(provider_id)
            profile = self.providers.get(provider_id)
            if profile is not None:
                chain.append(profile)
        return chain

    def _resolve_spec(self, spec: RouteSpec) -> ResolvedRoute:
        default_profiles = tuple(
            profile
            for provider_id in spec.default
            if (profile := self.providers.get(provider_id)) is not None
        )
        default_available = [profile for profile in default_profiles if profile.available]
        available = bool(default_available)

        task_profiles: dict[str, tuple[ProviderProfile, ...]] = {}
        models: dict[str, list[str]] = {}
        for task in TASK_KEYS:
            chain = self._task_chain(spec, task)
            if available:
                effective = [
                    profile for profile in chain if profile.available
                ][:MAX_PROFILES_PER_TASK]
            else:
                effective = []
            task_profiles[task] = tuple(effective)
            models[task] = [profile.model for profile in effective]

        reason = ""
        if not available:
            missing = sorted(
                {
                    profile.api_key_env
                    for profile in default_profiles
                    if profile.requires_key and not profile.api_key
                }
            )
            disabled = [profile.id for profile in default_profiles if not profile.enabled]
            invalid = [
                profile.id
                for profile in default_profiles
                if profile.enabled and (not profile.base_url or not profile.model)
            ]
            details = []
            if missing:
                details.append("缺少 " + "、".join(missing))
            if disabled:
                details.append("未启用 " + "、".join(disabled))
            if invalid:
                details.append("配置不完整 " + "、".join(invalid))
            reason = "默认回退链没有可用 Provider"
            if details:
                reason += "：" + "；".join(details)
            # 不可用时展示配置里“想做”的模型，方便 UI 说明。
            models = {
                task: [
                    profile.model
                    for profile in self._task_chain(spec, task)[:MAX_PROFILES_PER_TASK]
                ]
                for task in TASK_KEYS
            }

        return ResolvedRoute(
            id=spec.id,
            label=spec.label,
            description=spec.description,
            default_profiles=default_profiles,
            task_profiles=task_profiles,
            available=available,
            reason=reason,
            models=models,
        )

    def _pick_default(self) -> str | None:
        configured = self.default_route_id
        if (
            configured
            and configured in self._resolved
            and self._resolved[configured].available
        ):
            return configured
        for route_id, route in self._resolved.items():
            if route.available:
                return route_id
        if configured and configured in self._resolved:
            return configured
        return next(iter(self._resolved), None)

    def resolve(
        self, route_id: str | None = None, require_available: bool = False
    ) -> ResolvedRoute:
        chosen = route_id or self.effective_default_route_id
        if not chosen or chosen not in self._resolved:
            raise RouteNotFound(chosen or "")
        route = self._resolved[chosen]
        if require_available and not route.available:
            raise RouteUnavailableError(route.reason or f"路由 {chosen} 不可用")
        return route

    def routes(self) -> list[ResolvedRoute]:
        return list(self._resolved.values())

    def available_routes(self) -> list[ResolvedRoute]:
        return [route for route in self._resolved.values() if route.available]


def _legacy_registry(config: Settings, warnings: list[str] | None = None) -> ProfileRegistry:
    legacy = ProviderProfile(
        id="legacy-dashscope",
        label=f"百炼 {config.llm_model}",
        base_url=config.base_url,
        model=config.llm_model,
        api_key_env="DASHSCOPE_API_KEY",
        api_key=config.api_key,
    )
    spec = RouteSpec(
        id="legacy",
        label="默认（百炼）",
        description="未找到 llm_profiles.yaml，使用旧版单模型配置",
        default=(legacy.id,),
        tasks={},
    )
    return ProfileRegistry(
        {legacy.id: legacy},
        {spec.id: spec},
        spec.id,
        legacy=True,
        warnings=warnings,
    )


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是对象")
    return data


def load_registry(
    config: Settings = settings, path: str | Path | None = None
) -> ProfileRegistry:
    warnings: list[str] = []
    profile_path = Path(path or config.llm_profiles_path).expanduser()
    if not profile_path.exists():
        return _legacy_registry(config, [f"未找到 {profile_path}，使用 legacy 单模型路由"])

    try:
        data = _read_yaml(profile_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 LLM profiles 失败，使用 legacy 路由：%s", exc)
        return _legacy_registry(config, [f"LLM profiles 读取失败：{exc}"])

    providers: dict[str, ProviderProfile] = {}
    raw_providers = data.get("providers") or {}
    if not isinstance(raw_providers, dict):
        warnings.append("providers 必须是对象")
        raw_providers = {}

    for provider_id, raw in raw_providers.items():
        if not isinstance(raw, dict):
            warnings.append(f"provider {provider_id} 配置格式错误，已跳过")
            continue
        base_url = str(raw.get("base_url") or "").strip()
        base_url_env = str(raw.get("base_url_env") or "").strip()
        if base_url_env:
            override = os.getenv(base_url_env, "").strip()
            if override:
                base_url = override
        model = str(raw.get("model") or "").strip()
        model_env = str(raw.get("model_env") or "").strip()
        if model_env:
            override = os.getenv(model_env, "").strip()
            if override:
                model = override
        api_key_env = str(raw.get("api_key_env") or "").strip()
        api_key = os.getenv(api_key_env, "").strip() if api_key_env else ""
        providers[str(provider_id)] = ProviderProfile(
            id=str(provider_id),
            label=str(raw.get("label") or provider_id),
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            api_key=api_key,
            requires_key=bool(raw.get("requires_key", True)),
            supports_json_mode=bool(raw.get("supports_json_mode", True)),
            enabled=bool(raw.get("enabled", True)),
        )

    route_specs: dict[str, RouteSpec] = {}
    raw_routes = data.get("routes") or {}
    if not isinstance(raw_routes, dict):
        warnings.append("routes 必须是对象")
        raw_routes = {}

    for route_id, raw in raw_routes.items():
        if not isinstance(raw, dict):
            warnings.append(f"route {route_id} 配置格式错误，已跳过")
            continue
        default = raw.get("default")
        if not isinstance(default, list) or not default:
            warnings.append(f"route {route_id} 缺少 default 回退链，已跳过")
            continue
        default_ids = tuple(str(item) for item in default if str(item) in providers)
        if not default_ids:
            warnings.append(f"route {route_id} 的 default 没有已知 provider，已跳过")
            continue
        tasks: dict[str, tuple[str, ...]] = {}
        raw_tasks = raw.get("tasks") or {}
        if isinstance(raw_tasks, dict):
            for task, chain in raw_tasks.items():
                if task not in TASK_KEYS:
                    warnings.append(f"route {route_id} 含未知 task {task}，已忽略")
                    continue
                if not isinstance(chain, list):
                    warnings.append(f"route {route_id} 的 task {task} 不是列表，已忽略")
                    continue
                tasks[task] = tuple(
                    str(item) for item in chain if str(item) in providers
                )
        route_specs[str(route_id)] = RouteSpec(
            id=str(route_id),
            label=str(raw.get("label") or route_id),
            description=str(raw.get("description") or ""),
            default=default_ids,
            tasks=tasks,
        )

    if not providers or not route_specs:
        warnings.append("LLM profiles 没有有效的 provider/route，使用 legacy 路由")
        return _legacy_registry(config, warnings)

    configured_default = (
        config.llm_route
        or str(data.get("default_route") or "").strip()
        or next(iter(route_specs))
    )
    for warning in warnings:
        logger.warning("%s", warning)
    return ProfileRegistry(providers, route_specs, configured_default, warnings=warnings)


_registry_cache: dict[str, ProfileRegistry] = {}
_registry_lock = threading.Lock()


def get_registry(config: Settings = settings) -> ProfileRegistry:
    key = str(Path(config.llm_profiles_path).expanduser())
    with _registry_lock:
        registry = _registry_cache.get(key)
        if registry is None:
            registry = load_registry(config)
            _registry_cache[key] = registry
        return registry


def reset_registry() -> None:
    with _registry_lock:
        _registry_cache.clear()


def chat_runtime(config: Settings = settings) -> ChatRuntime:
    registry = get_registry(config)
    routes = tuple(registry.routes())
    if not config.live_requested:
        return ChatRuntime(
            mode="mock",
            reason="当前为 mock 模式：不发起模型请求，仅规则引擎 + 模板建议",
            default_route=registry.resolve(None),
            routes=routes,
        )
    available = registry.available_routes()
    if not available:
        return ChatRuntime(
            mode="mock",
            reason=(
                "LLM_MODE=live 但没有可用模型路由；"
                "请配置任一 Provider 的 API Key，或在 llm_profiles.yaml 启用本地模型"
            ),
            default_route=registry.resolve(None),
            routes=routes,
        )
    default_route = registry.resolve(None)
    return ChatRuntime(
        mode="live",
        reason=f"live 模式：路由「{default_route.label}」；模型失败时按配置自动回退",
        default_route=default_route,
        routes=routes,
    )


def resolve_route(
    route_id: str | None = None,
    config: Settings = settings,
    require_available: bool | None = None,
) -> ResolvedRoute:
    if require_available is None:
        require_available = chat_runtime(config).mode == "live"
    return get_registry(config).resolve(route_id, require_available=require_available)
