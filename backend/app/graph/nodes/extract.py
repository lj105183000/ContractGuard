"""条款抽取节点：确定性切分 + LLM 结构化抽取 + 启发式兜底。"""

from __future__ import annotations

import time

from app.graph.errors import PipelineError
from app.graph.progress import emit
from app.graph.state import ReviewState
from app.llm.client import get_llm
from app.llm.prompts import (
    EXTRACTION_SYSTEM,
    MAX_EXTRACTION_CLAUSES,
    build_extraction_prompt,
)
from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import ParsedContract
from app.parsing.facts import extract_heuristic_facts, merge_facts
from app.schemas import ClauseExtraction, ClauseType, EventStatus


async def extract_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "extract", "抽取条款与事实", EventStatus.STARTED, progress=15)

    text = state["contract_text"]
    parsed = ParsedContract(
        text=text,
        source_type=state.get("source_type", "txt"),
        page_count=state.get("page_count", 0),
        page_offsets=state.get("page_offsets", []),
    )
    clauses = split_clauses(parsed)
    if not clauses:
        raise PipelineError("无法从合同中切分出条款，请检查文档内容。")

    warnings: list[str] = []
    heuristic_facts = extract_heuristic_facts(text, clauses)
    prompt_clauses = clauses[:MAX_EXTRACTION_CLAUSES]
    if len(clauses) > len(prompt_clauses):
        warnings.append(
            f"文档条款较多（{len(clauses)} 条），LLM 抽取仅处理前 {len(prompt_clauses)} 条；"
            "规则与启发式事实仍覆盖全文。"
        )
    extraction: ClauseExtraction | None = None
    try:
        llm = get_llm(state["trace"], "extract", state.get("llm_route"))
        extraction = await llm.chat_json(
            "clause_extraction",
            EXTRACTION_SYSTEM,
            build_extraction_prompt(text, prompt_clauses),
            ClauseExtraction,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LLM 条款抽取失败，已使用启发式事实兜底：{exc}")

    if extraction is not None:
        extracted_by_id = {item.clause_id: item for item in extraction.clauses}
        for clause in clauses:
            item = extracted_by_id.get(clause.id)
            if not item:
                continue
            if item.clause_type != ClauseType.OTHER or clause.clause_type == ClauseType.OTHER:
                clause.clause_type = item.clause_type
            if item.summary:
                clause.summary = item.summary
        facts = merge_facts(extraction.facts, heuristic_facts)
    else:
        facts = heuristic_facts

    duration = int((time.perf_counter() - started) * 1000)
    emit(
        job_id,
        "extract",
        "抽取条款与事实",
        EventStatus.COMPLETED,
        message=f"{len(clauses)} 条条款；金额={'已识别' if facts.contract_value else '未识别'}；"
        f"付款={'已识别' if facts.payment.prepayment_ratio is not None or facts.payment.payment_days else '未识别'}",
        progress=28,
        duration_ms=duration,
    )
    return {"clauses": clauses, "facts": facts, "warnings": warnings}
