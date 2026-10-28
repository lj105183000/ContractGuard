"""规则引擎节点（确定性，与三个 LLM Agent 并行）。"""

from __future__ import annotations

import time

from app.graph.progress import emit
from app.graph.state import ReviewState
from app.rag.corpus import load_kb
from app.rules.engine import evaluate_rules
from app.schemas import EventStatus


def rule_engine_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "rule_engine", "规则引擎", EventStatus.STARTED, progress=32)
    kb = load_kb()
    try:
        result = evaluate_rules(
            kb.rules,
            state.get("clauses", []),
            state["facts"],
            state.get("contract_text", ""),
        )
    except Exception as exc:  # noqa: BLE001
        message = f"规则引擎执行失败，已跳过：{exc}"
        emit(
            job_id,
            "rule_engine",
            "规则引擎",
            EventStatus.FAILED,
            message=message,
            progress=70,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {"warnings": [message]}

    skipped = sum(1 for item in result.evaluations if item.result == "skipped")
    emit(
        job_id,
        "rule_engine",
        "规则引擎",
        EventStatus.COMPLETED,
        message=f"命中 {len(result.findings)} 条；跳过 {skipped} 条（字段未知）",
        progress=70,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"findings": result.findings, "warnings": result.warnings}
