"""解析节点：PDF/DOCX/TXT/图片 -> 文本 + 页码（扫描件走 OCR）。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.graph.errors import PipelineError
from app.graph.progress import emit
from app.graph.state import ReviewState
from app.parsing.contract_parser import ContractParseError, parse_contract
from app.schemas import EventStatus


async def parse_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "parse", "解析合同", EventStatus.STARTED, progress=5)

    source_path = Path(state["source_path"])

    def on_ocr(done: int, total: int) -> None:
        emit(
            job_id,
            "parse",
            "解析合同",
            EventStatus.STARTED,
            message=f"扫描件 OCR：{done}/{total} 页",
            progress=5 + int(7 * done / max(total, 1)),
        )

    try:
        parsed = await asyncio.to_thread(
            parse_contract,
            source_path,
            state.get("filename") or source_path.name,
            on_ocr,
        )
    except ContractParseError as exc:
        emit(
            job_id,
            "parse",
            "解析合同",
            EventStatus.FAILED,
            message=str(exc),
            progress=5,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise PipelineError(str(exc)) from exc

    duration = int((time.perf_counter() - started) * 1000)
    page_text = f"{parsed.page_count} 页" if parsed.page_count else "无分页信息"
    ocr_note = " · OCR" if parsed.used_ocr else ""
    emit(
        job_id,
        "parse",
        "解析合同",
        EventStatus.COMPLETED,
        message=(
            f"{parsed.source_type.upper()} · {page_text} · "
            f"{len(parsed.text)} 字符{ocr_note}"
        ),
        progress=12,
        duration_ms=duration,
    )

    warnings: list[str] = []
    if parsed.used_ocr:
        confidence = (
            f"，平均置信度 {parsed.ocr_confidence:.0%}"
            if parsed.ocr_confidence is not None
            else ""
        )
        failed = (
            f"，{parsed.ocr_failed_pages} 页识别失败"
            if parsed.ocr_failed_pages
            else ""
        )
        warnings.append(
            f"扫描件 OCR：{parsed.ocr_pages} 页由 OCR 识别{confidence}{failed}；"
            "可能存在错字，请对照原件核对。"
        )

    return {
        "contract_text": parsed.text,
        "source_type": parsed.source_type,
        "page_count": parsed.page_count,
        "page_offsets": parsed.page_offsets,
        "title": parsed.title,
        "warnings": warnings,
    }
