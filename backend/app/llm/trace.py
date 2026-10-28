"""收集每次 LLM 调用的耗时与 token。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import LLMCallTrace, UsageSummary


@dataclass
class TraceCollector:
    traces: list[LLMCallTrace] = field(default_factory=list)

    def add(self, trace: LLMCallTrace) -> None:
        self.traces.append(trace)

    def usage(
        self,
        duration_ms: int,
        mode: str,
        embedding_mode: str,
        rerank_mode: str = "off",
        llm_route: str | None = None,
        llm_route_label: str | None = None,
    ) -> UsageSummary:
        real = [trace for trace in self.traces if not trace.mock]
        models = list(
            dict.fromkeys(
                trace.model
                for trace in self.traces
                if trace.success and not trace.mock
            )
        )
        return UsageSummary(
            llm_calls=len(real),
            input_tokens=sum(trace.input_tokens for trace in real),
            output_tokens=sum(trace.output_tokens for trace in real),
            duration_ms=duration_ms,
            mode=mode,
            embedding_mode=embedding_mode,
            rerank_mode=rerank_mode,
            llm_route=llm_route,
            llm_route_label=llm_route_label,
            llm_models=models,
            traces=list(self.traces),
        )
