"""LLM 调用封装。

- live：按路由选择 OpenAI 兼容 Provider；单 Provider 失败时按顺序回退；
- mock：不发网络请求，返回空的结构化结果；
- JSON 模式 + Pydantic 校验 + 尝试次数限制 + 调用 trace。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Protocol, Sequence, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel

from app.config import Settings, settings
from app.llm.profiles import (
    MAX_PROFILES_PER_TASK,
    ProviderProfile,
    RouteError,
    chat_runtime,
    get_registry,
)
from app.llm.trace import TraceCollector
from app.schemas import LLMCallTrace

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS_PER_PROFILE = 2
RETRY_BACKOFF_S = 1.0


class LLMError(RuntimeError):
    """模型调用或结构化输出失败。"""


class LLMConfigError(LLMError):
    """没有可用的模型路由/Provider。"""


class _ProfileFailure(LLMError):
    def __init__(self, profile: ProviderProfile, error: Exception) -> None:
        super().__init__(f"{profile.id}/{profile.model}: {error}")
        self.profile = profile
        self.error = error


class BaseLLM(Protocol):
    async def chat_json(
        self,
        name: str,
        system: str,
        user: str,
        schema_model: type[T],
        temperature: float = 0.1,
    ) -> T: ...


def extract_json(text: str) -> object:
    """从模型输出中提取 JSON，兼容 markdown 代码块和前后说明文字。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("模型返回为空")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        raise ValueError("模型输出中没有 JSON 对象")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        raise ValueError("模型输出的 JSON 不完整")
    return json.loads(text[start : end + 1])


def _looks_like_format_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        keyword in message
        for keyword in ("response_format", "json_object", "json mode", "unsupported parameter")
    )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(
        exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    ):
        return True
    status = getattr(exc, "status_code", None)
    return status == 429 or (isinstance(status, int) and status >= 500)


class LiveLLM:
    def __init__(
        self,
        trace: TraceCollector,
        task: str,
        profiles: Sequence[ProviderProfile],
        config: Settings = settings,
    ) -> None:
        self.trace = trace
        self.task = task
        self.profiles = list(profiles)[:MAX_PROFILES_PER_TASK]
        self.config = config
        if not self.profiles:
            raise LLMConfigError(f"任务 {task} 没有可用的模型 profile")
        self._clients: dict[str, AsyncOpenAI] = {}
        self._json_mode = {
            profile.id: profile.supports_json_mode for profile in self.profiles
        }

    def _client(self, profile: ProviderProfile) -> AsyncOpenAI:
        client = self._clients.get(profile.id)
        if client is None:
            client = AsyncOpenAI(
                api_key=profile.api_key or "local",
                base_url=profile.base_url,
                timeout=self.config.llm_timeout_s,
                max_retries=0,
            )
            self._clients[profile.id] = client
        return client

    async def _call(
        self,
        profile: ProviderProfile,
        messages: list[dict[str, str]],
        temperature: float,
    ):  # type: ignore[no-untyped-def]
        kwargs = {
            "model": profile.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self._json_mode.get(profile.id, False):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return await self._client(profile).chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if self._json_mode.get(profile.id, False) and _looks_like_format_error(exc):
                # 该 provider 不支持 response_format，当前 LiveLLM 实例内记住并重试一次。
                self._json_mode[profile.id] = False
                kwargs.pop("response_format", None)
                return await self._client(profile).chat.completions.create(**kwargs)
            raise

    async def _chat_with_profile(
        self,
        name: str,
        profile: ProviderProfile,
        base_messages: list[dict[str, str]],
        schema_model: type[T],
        temperature: float,
    ) -> T:
        messages = list(base_messages)
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS_PER_PROFILE):
            started = time.perf_counter()
            try:
                response = await self._call(profile, messages, temperature)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.trace.add(
                    LLMCallTrace(
                        name=name,
                        model=profile.model,
                        profile=profile.id,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        success=False,
                        error=str(exc)[:500],
                    )
                )
                if attempt == 0 and _is_retryable(exc):
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                raise _ProfileFailure(profile, exc) from exc

            latency = int((time.perf_counter() - started) * 1000)
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(usage, "completion_tokens", 0) or 0
            content = response.choices[0].message.content

            try:
                data = extract_json(content or "")
                parsed = schema_model.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.trace.add(
                    LLMCallTrace(
                        name=name,
                        model=profile.model,
                        profile=profile.id,
                        latency_ms=latency,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        success=False,
                        error=str(exc)[:500],
                    )
                )
                messages.append({"role": "assistant", "content": content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"上一次输出无法通过 schema 校验：{exc}。"
                            "请只输出合法 JSON，不要 markdown 代码块，不要解释文字，"
                            "字段名和枚举值严格参考系统提示。"
                        ),
                    }
                )
                if attempt == 0:
                    continue
                raise _ProfileFailure(profile, exc) from exc

            self.trace.add(
                LLMCallTrace(
                    name=name,
                    model=profile.model,
                    profile=profile.id,
                    latency_ms=latency,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    success=True,
                )
            )
            return parsed

        raise _ProfileFailure(profile, last_error or LLMError("未知错误"))

    async def chat_json(
        self,
        name: str,
        system: str,
        user: str,
        schema_model: type[T],
        temperature: float = 0.1,
    ) -> T:
        base_messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        failures: list[str] = []
        try:
            async with asyncio.timeout(self.config.llm_deadline_s):
                for profile in self.profiles:
                    try:
                        return await self._chat_with_profile(
                            name, profile, base_messages, schema_model, temperature
                        )
                    except _ProfileFailure as exc:
                        failures.append(str(exc))
                        logger.warning(
                            "LLM profile %s 失败，尝试下一个（task=%s, name=%s）：%s",
                            profile.id,
                            self.task,
                            name,
                            exc.error,
                        )
        except TimeoutError as exc:
            failures.append(f"超过 {self.config.llm_deadline_s}s 总时限")
            raise LLMError(
                f"{name} 调用超时（task={self.task}）：" + "；".join(failures)
            ) from exc

        raise LLMError(
            f"{name} 调用失败（task={self.task}，已尝试 {len(self.profiles)} 个 profile）："
            + "；".join(failures)
        )


class MockLLM:
    """离线演示：返回空结果，不伪造 LLM 风险。"""

    def __init__(self, trace: TraceCollector) -> None:
        self.trace = trace

    async def chat_json(
        self,
        name: str,
        system: str,
        user: str,
        schema_model: type[T],
        temperature: float = 0.1,
    ) -> T:
        self.trace.add(
            LLMCallTrace(
                name=name,
                model="mock",
                latency_ms=0,
                mock=True,
                success=True,
            )
        )
        return schema_model()


def _resolve_profiles(
    task: str, route_id: str | None, config: Settings
) -> tuple[list[ProviderProfile], str, str]:
    registry = get_registry(config)
    try:
        route = registry.resolve(route_id)
    except RouteError:
        route = registry.resolve(None)
    if not route.available:
        try:
            route = registry.resolve(None)
        except RouteError:
            pass
    profiles = list(route.profiles_for(task))
    if not profiles:
        # 防御：路由可用时 default 链一定有一个可用 profile。
        profiles = [
            profile
            for profile in route.default_profiles
            if profile.available
        ][:MAX_PROFILES_PER_TASK]
    if not profiles:
        raise LLMConfigError(f"路由 {route.id} 没有可用于任务 {task} 的模型")
    return profiles, route.id, route.label


def get_llm(
    trace: TraceCollector,
    task: str = "default",
    route_id: str | None = None,
    config: Settings = settings,
) -> BaseLLM:
    runtime = chat_runtime(config)
    if runtime.mode != "live":
        return MockLLM(trace)
    try:
        profiles, resolved_id, route_label = _resolve_profiles(task, route_id, config)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMConfigError(f"无法解析模型路由：{exc}") from exc
    logger.debug(
        "LLM route=%s task=%s profiles=%s label=%s",
        resolved_id,
        task,
        [profile.id for profile in profiles],
        route_label,
    )
    return LiveLLM(trace, task, profiles, config)
