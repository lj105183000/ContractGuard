"""HTTP API：上传、轮询、人工复核、导出。"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.limits import check_rate_limit
from app.config import DISCLAIMER, UPLOAD_DIR, settings
from app.llm.profiles import (
    ResolvedRoute,
    RouteError,
    chat_runtime,
    get_registry,
    resolve_route,
)
from app.observability import metrics_snapshot
from app.parsing.contract_parser import ALLOWED_EXTENSIONS
from app.rag.corpus import find_sample, load_kb, sample_path
from app.rag.retriever import get_retriever
from app.reporting.batch_report import (
    build_batch_docx,
    build_batch_json,
    build_batch_markdown,
    safe_batch_report_filename,
)
from app.reporting.report import (
    build_docx,
    build_json,
    build_markdown,
    safe_report_filename,
)
from app.reporting.revised_contract import (
    RevisedContractError,
    build_revised_contract,
    build_revised_docx,
    build_revised_markdown,
    safe_revised_filename,
)
from app.schemas import (
    BatchDecisionItem,
    BatchDecisionRequest,
    BatchDecisionResponse,
    BatchState,
    Finding,
    JobState,
    JobStatus,
    JobSummary,
    LLMRouteInfo,
    MetaResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    SampleInfo,
    SuggestionUpdateRequest,
)
from app.services.batches import load_batch, load_batch_jobs
from app.worker import worker_state
from app.crypto import CryptoError
from app.services.jobs import (
    FindingNotFound,
    JobNotCancellable,
    JobNotFound,
    JobNotRetryable,
    JobResuming,
    SourceMissing,
    StaleRevision,
    delete_checkpoint,
    resume_review,
    run_review,
    store,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_filename(filename: str) -> str:
    name = Path(filename or "contract.txt").name
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return name or "contract.txt"


def _validate_upload_signature(path: Path, suffix: str) -> None:
    """按文件头拒绝伪装成 PDF/DOCX/图片的上传。"""
    with path.open("rb") as handle:
        head = handle.read(16)
    signatures = {
        ".pdf": (b"%PDF-",),
        ".docx": (b"PK\x03\x04",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
    }
    expected = signatures.get(suffix)
    if expected and not any(head.startswith(prefix) for prefix in expected):
        raise HTTPException(
            status_code=400,
            detail=f"文件内容与扩展名 {suffix} 不匹配，请检查文件是否损坏。",
        )


def _validate_batch_id(batch_id: str | None) -> str | None:
    if batch_id is None:
        return None
    value = batch_id.strip()
    if not value:
        return None
    if not BATCH_ID_RE.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail="batch_id 只能包含字母、数字、下划线和短横线，长度 1–64。",
        )
    return value


async def _schedule_resume(
    job_id: str, background_tasks: BackgroundTasks
) -> None:
    if settings.sync_jobs:
        await resume_review(job_id)
    elif settings.worker_enabled:
        store.request_resume(job_id)
    else:
        background_tasks.add_task(resume_review, job_id)


def _resolve_requested_route(llm_route: str | None) -> ResolvedRoute:
    runtime = chat_runtime(settings)
    try:
        route = resolve_route(
            llm_route, config=settings, require_available=False
        )
    except RouteError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"模型路由不存在：{llm_route or '(默认)'}",
        ) from exc
    if runtime.mode == "live" and not route.available:
        raise HTTPException(
            status_code=400,
            detail=f"模型路由不可用：{route.reason or route.id}",
        )
    return route


@router.get("/health")
async def health() -> dict:
    runtime = chat_runtime(settings)
    try:
        store_health = store.health()
    except Exception as exc:  # noqa: BLE001
        logger.exception("健康检查失败：%s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "mode": runtime.mode,
                "version": "0.1.0",
                "checks": {"db": "error"},
            },
        )
    checkpoint_ok = True
    if settings.hitl_enabled:
        checkpoint_ok = Path(settings.checkpoint_db_path).expanduser().exists()
    return {
        "status": "ok",
        "mode": runtime.mode,
        "version": "0.1.0",
        "checks": {**store_health, "checkpoint": checkpoint_ok},
        "worker": worker_state(),
    }


@router.get("/metrics")
async def metrics() -> dict:
    try:
        queue = store.health()
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取队列指标失败：%s", exc)
        queue = {"db": "error"}
    return {
        "requests": metrics_snapshot(),
        "queue": queue,
        "worker": worker_state(),
    }


@router.get("/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    kb = load_kb()
    retriever = get_retriever()
    runtime = chat_runtime(settings)
    default_route = runtime.default_route
    models = default_route.models_by_task() if default_route else {}
    llm_model = (
        next(iter(models.get("agent_commercial", [])), "")
        or next(iter(models.get("extract", [])), "")
        or settings.llm_model
    )
    route_infos = [
        LLMRouteInfo(
            id=route.id,
            label=route.label,
            description=route.description,
            available=route.available,
            reason=route.reason,
            models=route.models_by_task(),
        )
        for route in runtime.routes
    ]
    return MetaResponse(
        mode=runtime.mode,
        mode_reason=runtime.reason,
        llm_model=llm_model,
        llm_route=default_route.id if default_route else None,
        llm_routes=route_infos,
        embedding_model=settings.embedding_model,
        legal_count=len(kb.legal_articles),
        rule_count=len(kb.rules),
        sample_count=len(kb.samples),
        max_upload_mb=settings.max_upload_mb,
        ocr_max_pages=settings.ocr_max_pages,
        embedding_mode=retriever.retrieval_mode,
        rerank_mode=retriever.rerank_mode,
        rerank_model=settings.rerank_model if retriever.rerank_mode != "off" else None,
        disclaimer=DISCLAIMER,
    )


@router.get("/samples", response_model=list[SampleInfo])
async def samples() -> list[SampleInfo]:
    return load_kb().samples


@router.post("/reviews", status_code=202)
async def create_review(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    llm_route: str | None = Query(default=None, max_length=64),
    batch_id: str | None = Query(default=None, max_length=64),
) -> JSONResponse:
    check_rate_limit(request)
    if store.active_count() >= settings.max_active_jobs:
        raise HTTPException(
            status_code=429,
            detail=f"当前活跃任务已达上限 {settings.max_active_jobs}，请等待任务完成。",
        )
    filename = _safe_filename(file.filename or "contract.txt")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型 {suffix or '(无扩展名)'}；"
                "请上传 PDF、DOCX、TXT 或 JPG/PNG 图片。"
            ),
        )
    route = _resolve_requested_route(llm_route)
    batch_id = _validate_batch_id(batch_id)

    job = store.create(filename, route.id, batch_id=batch_id)
    job_dir = UPLOAD_DIR / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / filename
    store.set_source(job.job_id, str(target))
    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {settings.max_upload_mb}MB 限制。",
                    )
                handle.write(chunk)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        store.discard(job.job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(job_dir, ignore_errors=True)
        store.discard(job.job_id)
        raise HTTPException(status_code=500, detail=f"文件保存失败：{exc}") from exc
    finally:
        await file.close()

    if size == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        store.discard(job.job_id)
        raise HTTPException(status_code=400, detail="上传文件为空。")

    try:
        _validate_upload_signature(target, suffix)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        store.discard(job.job_id)
        raise

    if settings.sync_jobs:
        await run_review(job.job_id, target, cleanup=True, llm_route=route.id)
    elif settings.worker_enabled:
        if not store.mark_ready(job.job_id):
            shutil.rmtree(job_dir, ignore_errors=True)
            store.discard(job.job_id)
            raise HTTPException(
                status_code=409,
                detail="任务已过期或被回收，请重新上传。",
            )
    else:
        background_tasks.add_task(
            run_review, job.job_id, target, True, route.id
        )
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@router.post("/reviews/samples/{sample_id}", status_code=202)
async def create_sample_review(
    sample_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    llm_route: str | None = Query(default=None, max_length=64),
    batch_id: str | None = Query(default=None, max_length=64),
) -> JSONResponse:
    check_rate_limit(request)
    if store.active_count() >= settings.max_active_jobs:
        raise HTTPException(
            status_code=429,
            detail=f"当前活跃任务已达上限 {settings.max_active_jobs}，请等待任务完成。",
        )
    sample = find_sample(sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail="示例合同不存在。")
    path = sample_path(sample)
    if not path.exists():
        raise HTTPException(status_code=404, detail="示例合同文件缺失，请先运行数据生成脚本。")
    route = _resolve_requested_route(llm_route)
    batch_id = _validate_batch_id(batch_id)
    job = store.create(
        sample.filename,
        route.id,
        batch_id=batch_id,
        source_path=str(path),
        cleanup_source=False,
    )
    if settings.sync_jobs:
        await run_review(job.job_id, path, cleanup=False, llm_route=route.id)
    elif settings.worker_enabled:
        if not store.mark_ready(job.job_id):
            store.discard(job.job_id)
            raise HTTPException(
                status_code=409,
                detail="任务已过期或被回收，请重新运行示例。",
            )
    else:
        background_tasks.add_task(run_review, job.job_id, path, False, route.id)
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@router.get("/reviews", response_model=list[JobSummary])
async def list_reviews(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    batch_id: str | None = Query(default=None, max_length=64),
) -> list[JobSummary]:
    return store.list_jobs(
        status=status,
        limit=limit,
        offset=offset,
        batch_id=_validate_batch_id(batch_id),
    )


@router.get("/reviews/{job_id}", response_model=JobState)
async def get_review(job_id: str) -> JobState:
    try:
        return store.get(job_id, fresh=True)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
    except CryptoError as exc:
        raise HTTPException(
            status_code=500,
            detail="任务数据无法解密，请检查 CONTRACTGUARD_MASTER_KEY。",
        ) from exc


@router.get("/batches/{batch_id}", response_model=BatchState)
async def get_batch(batch_id: str) -> BatchState:
    _validate_batch_id(batch_id)
    batch = load_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在或已过期。")
    return batch


@router.post(
    "/batches/{batch_id}/decisions",
    response_model=BatchDecisionResponse,
)
async def decide_batch(
    batch_id: str,
    payload: BatchDecisionRequest,
    background_tasks: BackgroundTasks,
) -> BatchDecisionResponse:
    _validate_batch_id(batch_id)
    batch = load_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在或已过期。")

    items: list[BatchDecisionItem] = []
    total_decided = total_skipped = 0
    for summary in batch.items:
        try:
            job = store.get(summary.job_id, fresh=True)
            if job.status != JobStatus.AWAITING_REVIEW:
                items.append(
                    BatchDecisionItem(
                        job_id=summary.job_id,
                        filename=summary.filename,
                        status=job.status,
                    )
                )
                continue
            decided, _summary = store.decide_many(
                summary.job_id,
                payload.finding_ids,
                payload.decision,
                payload.note,
            )
            if not store.blocking_high_risk(summary.job_id):
                await _schedule_resume(summary.job_id, background_tasks)
            current = store.get(summary.job_id, fresh=True)
            skipped = (
                max(0, len(payload.finding_ids) - decided)
                if payload.finding_ids
                else 0
            )
            total_decided += decided
            total_skipped += skipped
            items.append(
                BatchDecisionItem(
                    job_id=summary.job_id,
                    filename=summary.filename,
                    status=current.status,
                    decided=decided,
                    skipped=skipped,
                )
            )
        except Exception as exc:  # noqa: BLE001
            items.append(
                BatchDecisionItem(
                    job_id=summary.job_id,
                    filename=summary.filename,
                    status=summary.status,
                    error=str(exc),
                )
            )
    return BatchDecisionResponse(
        batch_id=batch_id,
        decided=total_decided,
        skipped=total_skipped,
        items=items,
    )


@router.get("/batches/{batch_id}/report/preview")
async def preview_batch_report(batch_id: str) -> dict:
    _validate_batch_id(batch_id)
    batch = load_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在或已过期。")
    jobs = load_batch_jobs(batch_id)
    return {
        "batch_id": batch_id,
        "markdown": build_batch_markdown(batch, jobs),
        "stats": batch.stats,
        "export_allowed": batch.export_allowed,
        "blocked_items": batch.blocked_items,
    }


@router.get("/batches/{batch_id}/report")
async def download_batch_report(batch_id: str, format: str = "md") -> Response:
    if format not in {"md", "json", "docx"}:
        raise HTTPException(status_code=400, detail="format 只能是 md、json 或 docx。")
    _validate_batch_id(batch_id)
    batch = load_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在或已过期。")
    if batch.blocked_items:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"批次还有 {len(batch.blocked_items)} 份合同未完成复核，暂不能导出。",
                "blocked_items": [
                    item.model_dump(mode="json") for item in batch.blocked_items
                ],
            },
        )
    jobs = load_batch_jobs(batch_id)
    if format == "md":
        content = build_batch_markdown(batch, jobs).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
    elif format == "json":
        content = json.dumps(
            build_batch_json(batch, jobs), ensure_ascii=False, indent=2
        ).encode("utf-8")
        media_type = "application/json; charset=utf-8"
    else:
        content = await run_in_threadpool(build_batch_docx, batch, jobs)
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    filename = safe_batch_report_filename(batch_id, format)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.post(
    "/reviews/{job_id}/findings/{finding_id}/decision",
    response_model=ReviewDecisionResponse,
)
async def decide_finding(
    job_id: str,
    finding_id: str,
    payload: ReviewDecisionRequest,
    background_tasks: BackgroundTasks,
) -> ReviewDecisionResponse:
    try:
        job = store.get(job_id, fresh=True)
        if job.status == JobStatus.RESUMING:
            raise HTTPException(status_code=409, detail="复核正在完成，请稍候。")
        response = store.decide(job_id, finding_id, payload.decision, payload.note)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或尚未完成。") from exc
    except FindingNotFound as exc:
        raise HTTPException(status_code=404, detail="风险项不存在。") from exc
    except JobResuming as exc:
        raise HTTPException(status_code=409, detail="复核正在完成，请稍候。") from exc
    except StaleRevision as exc:
        raise HTTPException(
            status_code=409, detail="任务正在被其他请求修改，请重试。"
        ) from exc
    except CryptoError as exc:
        raise HTTPException(
            status_code=500,
            detail="任务数据无法解密，请检查 CONTRACTGUARD_MASTER_KEY。",
        ) from exc

    if job.status == JobStatus.AWAITING_REVIEW and not store.blocking_high_risk(job_id):
        await _schedule_resume(job_id, background_tasks)
    return response


@router.patch(
    "/reviews/{job_id}/findings/{finding_id}/suggestion",
    response_model=Finding,
)
async def update_finding_suggestion(
    job_id: str, finding_id: str, payload: SuggestionUpdateRequest
) -> Finding:
    try:
        return store.update_suggestion(
            job_id, finding_id, payload.suggested_text
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或尚未完成。") from exc
    except FindingNotFound as exc:
        raise HTTPException(status_code=404, detail="风险项不存在。") from exc
    except (JobResuming, StaleRevision) as exc:
        raise HTTPException(
            status_code=409, detail="任务正在被其他请求修改，请重试。"
        ) from exc


@router.post("/reviews/{job_id}/resume", status_code=202)
async def resume_suspended_review(
    job_id: str, background_tasks: BackgroundTasks
) -> dict:
    try:
        job = store.get(job_id, fresh=True)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
    if job.status == JobStatus.RESUMING:
        return {"status": "resuming"}
    if job.status != JobStatus.AWAITING_REVIEW:
        raise HTTPException(status_code=409, detail="任务不在等待人工复核状态。")
    pending = store.blocking_high_risk(job_id)
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"还有 {len(pending)} 条已核实高风险未复核。",
        )
    await _schedule_resume(job_id, background_tasks)
    return {"status": "resuming"}


def _pending_high_response(pending: list[Finding]) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"还有 {len(pending)} 条已核实高风险未复核，请先处理。",
            "pending_findings": [
                {
                    "id": finding.id,
                    "title": finding.title,
                    "clause_article": finding.clause_article,
                    "confidence_tier": (
                        finding.confidence_tier.value
                        if finding.confidence_tier
                        else None
                    ),
                }
                for finding in pending
            ],
        },
    )


@router.post("/reviews/{job_id}/cancel")
async def cancel_review(job_id: str) -> dict:
    try:
        job = store.request_cancel(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
    except (JobNotCancellable, StaleRevision) as exc:
        raise HTTPException(
            status_code=409, detail="任务已结束或被其他请求修改，无法取消。"
        ) from exc
    return {
        "job_id": job_id,
        "status": "cancelling" if job.cancel_requested else "cancelled",
    }


@router.post("/reviews/{job_id}/retry", status_code=202)
async def retry_review(
    job_id: str, background_tasks: BackgroundTasks
) -> dict:
    try:
        job = store.retry(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
    except SourceMissing as exc:
        raise HTTPException(
            status_code=409,
            detail="源文件已清理，无法重试；请重新上传合同。",
        ) from exc
    except (JobNotRetryable, StaleRevision) as exc:
        raise HTTPException(
            status_code=409, detail="只有失败或已取消的任务可以重试。"
        ) from exc

    source_path = Path(job.source_path)  # type: ignore[arg-type]
    await delete_checkpoint(job.job_id)
    if settings.sync_jobs:
        await run_review(
            job.job_id,
            source_path,
            job.cleanup_source,
            job.llm_route,
        )
    elif not settings.worker_enabled:
        background_tasks.add_task(
            run_review,
            job.job_id,
            source_path,
            job.cleanup_source,
            job.llm_route,
        )
    return {"job_id": job.job_id, "status": job.status.value}


@router.get("/reviews/{job_id}/report")
async def download_report(job_id: str, format: str = "md") -> Response:
    if format not in {"md", "json", "docx"}:
        raise HTTPException(status_code=400, detail="format 只能是 md、json 或 docx。")
    try:
        job = store.get(job_id, fresh=True)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc

    if job.result is None:
        raise HTTPException(status_code=409, detail="审查尚未完成，暂时不能导出。")
    if job.status not in {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
    }:
        raise HTTPException(status_code=409, detail="人工复核尚未完成，请稍候再导出。")

    pending = store.blocking_high_risk(job_id)
    if pending:
        return _pending_high_response(pending)

    report = job.result
    if format == "md":
        content = build_markdown(report).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        filename = safe_report_filename(report, "md")
    elif format == "json":
        content = build_json(report).encode("utf-8")
        media_type = "application/json; charset=utf-8"
        filename = safe_report_filename(report, "json")
    else:
        content = await run_in_threadpool(build_docx, report)
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        filename = safe_report_filename(report, "docx")

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/reviews/{job_id}/revised-contract")
async def download_revised_contract(
    job_id: str, format: str = "json"
) -> Response:
    if format not in {"md", "json", "docx"}:
        raise HTTPException(status_code=400, detail="format 只能是 md、json 或 docx。")
    try:
        job = store.get(job_id, fresh=True)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc

    if job.result is None:
        raise HTTPException(status_code=409, detail="审查尚未完成，暂时不能生成修订稿。")
    if job.status not in {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
    }:
        raise HTTPException(status_code=409, detail="人工复核尚未完成，请稍候再生成修订稿。")

    pending = store.blocking_high_risk(job_id)
    if pending:
        return _pending_high_response(pending)

    try:
        revised = build_revised_contract(job.result, job_id=job_id)
    except RevisedContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if format == "json":
        content = revised.model_dump_json(indent=2).encode("utf-8")
        media_type = "application/json; charset=utf-8"
    elif format == "md":
        content = build_revised_markdown(revised).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
    else:
        content = await run_in_threadpool(build_revised_docx, revised)
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    filename = safe_revised_filename(job.result, format)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/reviews/{job_id}/report/preview")
async def preview_report(job_id: str) -> dict:
    """报告在线预览。

    预览是只读草稿：不因高风险未复核而拒绝，方便用户在复核过程中查看
    报告结构；真正的下载仍由 blocking high 门禁控制。
    """
    try:
        job = store.get(job_id, fresh=True)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
    if job.result is None:
        raise HTTPException(status_code=409, detail="审查尚未完成，暂时不能预览。")

    pending = store.blocking_high_risk(job_id)
    advisory = store.advisory_high_risk(job_id)
    return {
        "markdown": build_markdown(job.result),
        "pending_high": len(pending),
        "advisory_high": len(advisory),
        "export_allowed": not pending,
        "pending_findings": [
            {
                "id": finding.id,
                "title": finding.title,
                "clause_article": finding.clause_article,
            }
            for finding in pending
        ],
    }
