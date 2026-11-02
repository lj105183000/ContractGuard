"""FastAPI 入口。"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.body_limit import BodyLimitMiddleware
from app.api.routes import router
from app.config import FRONTEND_DIST, ensure_dirs, settings
from app.crypto import AesGcmCipher
from app.graph.build import build_graph
from app.graph.runtime import clear_runtime, init_runtime
from app.llm.profiles import chat_runtime, get_registry
from app.observability import record_request
from app.rag.corpus import load_kb
from app.rag.retriever import get_retriever
from app.services.jobs import delete_checkpoint, resume_review, store
from app.worker import WorkerPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("contractguard")

# Checkpoint 会序列化 ReviewState 中的 Pydantic 模型和 TraceCollector。
# 显式 allowlist，避免 LangGraph 的 unregistered type 告警，也为未来
# LANGGRAPH_STRICT_MSGPACK 做好准备。
CHECKPOINT_ALLOWED_TYPES = [
    ("app.schemas", name)
    for name in (
        "Category",
        "Clause",
        "ClauseType",
        "ConfidenceTier",
        "ContractFacts",
        "ContractMeta",
        "DeliveryFacts",
        "AcceptanceFacts",
        "WarrantyFacts",
        "LiabilityFacts",
        "TerminationFacts",
        "DisputeFacts",
        "ComplianceFacts",
        "PaymentFacts",
        "Finding",
        "FindingStatus",
        "LLMCallTrace",
        "LegalArticle",
        "PlaybookRule",
        "ReviewResult",
        "ReviewSummary",
        "Severity",
        "Source",
        "UsageSummary",
    )
] + [("app.llm.trace", "TraceCollector")]


async def _checkpoint_janitor() -> None:
    """定期执行保留期清理并删除过期任务的 checkpoint。"""
    while True:
        try:
            store.cleanup_expired()
        except Exception:  # noqa: BLE001
            logger.exception("保留期清理失败")
        for job_id in store.pop_checkpoint_cleanup():
            await delete_checkpoint(job_id)
        await asyncio.sleep(60)


async def _warmup_embeddings(retriever) -> None:  # type: ignore[no-untyped-def]
    """后台预热向量索引；超时/失败只降级 BM25，不阻塞应用启动。"""
    try:
        async with asyncio.timeout(settings.embedding_warmup_timeout_s):
            await retriever.warmup()
    except TimeoutError:
        retriever.last_warning = (
            f"向量索引预热超过 {settings.embedding_warmup_timeout_s}s，"
            "已先以降级模式启动（BM25）"
        )
        logger.warning(retriever.last_warning)
    except Exception:  # noqa: BLE001
        logger.exception("向量索引预热失败，已降级为 BM25")
    else:
        if retriever.last_warning:
            logger.warning(retriever.last_warning)
        else:
            logger.info("向量检索就绪：%s", retriever.retrieval_mode)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    if settings.cors_origin.strip() == "*":
        raise RuntimeError(
            "CORS_ORIGIN=* 不安全：请配置明确的来源，或保持默认 localhost。"
        )
    if "*" in settings.allowed_host_list:
        raise RuntimeError(
            "CONTRACTGUARD_ALLOWED_HOSTS 不能使用通配符；请列出明确的 Host。"
        )
    if settings.require_auth and not settings.api_token:
        raise RuntimeError(
            "CONTRACTGUARD_REQUIRE_AUTH=1 但未设置 CONTRACTGUARD_API_TOKEN。"
        )
    if settings.has_non_local_hosts and not settings.api_token:
        raise RuntimeError(
            "CONTRACTGUARD_ALLOWED_HOSTS 含非本机地址时必须设置 "
            "CONTRACTGUARD_API_TOKEN（避免内网无鉴权暴露）。"
        )
    ensure_dirs()
    store.recover_incomplete()
    kb = load_kb()
    logger.info(
        "知识库加载完成：%s 条法条，%s 条规则，%s 份示例合同",
        len(kb.legal_articles),
        len(kb.rules),
        len(kb.samples),
    )
    registry = get_registry()
    runtime = chat_runtime(settings)
    logger.info("聊天模式：%s（%s）", runtime.mode, runtime.reason)
    logger.info(
        "模型路由：%s 个；默认路由：%s",
        len(registry.routes()),
        registry.effective_default_route_id or "无",
    )
    for warning in registry.warnings:
        logger.warning("LLM profiles：%s", warning)
    logger.info(
        "Embedding：%s（%s）",
        settings.embedding_model,
        "live" if settings.embedding_live else "未配置/BM25",
    )
    if not settings.encryption_enabled:
        logger.warning(
            "ENCRYPTION_ENABLED=0：job state/checkpoint 之外的合同数据"
            "（上传源文件、feedback、run trace、桌面历史）为明文；"
            "真实合同请开启加密或使用磁盘/卷加密。"
        )
    retriever = get_retriever()
    warmup_task: asyncio.Task | None = None
    if settings.embedding_live:
        warmup_task = asyncio.create_task(
            _warmup_embeddings(retriever), name="contractguard-embedding-warmup"
        )

    worker: WorkerPool | None = None
    janitor: asyncio.Task | None = None
    startup_tasks: set[asyncio.Task] = set()
    try:
        async with AsyncExitStack() as stack:
            if settings.hitl_enabled:
                checkpoint = Path(settings.checkpoint_db_path).expanduser()
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                conn = await stack.enter_async_context(
                    aiosqlite.connect(str(checkpoint))
                )
                await conn.execute("PRAGMA busy_timeout=30000")
                serde = JsonPlusSerializer(
                    allowed_msgpack_modules=CHECKPOINT_ALLOWED_TYPES
                )
                if settings.encryption_enabled:
                    serde = EncryptedSerializer(AesGcmCipher(), serde)
                saver = AsyncSqliteSaver(conn, serde=serde)
                init_runtime(
                    saver,
                    build_graph(checkpointer=saver, enable_hitl=True),
                )
                logger.info(
                    "HITL 已启用：checkpoint=%s（加密：%s）",
                    checkpoint,
                    "开" if settings.encryption_enabled else "关",
                )
                if settings.hitl_resume_on_startup:
                    for job in store.awaiting_resume_jobs():
                        logger.info("恢复等待人工复核的任务：%s", job.job_id)
                        if settings.worker_enabled and not settings.sync_jobs:
                            store.request_resume(job.job_id)
                        else:
                            task = asyncio.create_task(
                                resume_review(job.job_id)
                            )
                            startup_tasks.add(task)
                            task.add_done_callback(startup_tasks.discard)
            janitor = asyncio.create_task(
                _checkpoint_janitor(), name="contractguard-janitor"
            )
            if settings.worker_enabled and not settings.sync_jobs:
                worker = WorkerPool()
                await worker.start()
            yield
    finally:
        if warmup_task is not None:
            warmup_task.cancel()
            await asyncio.gather(warmup_task, return_exceptions=True)
        if worker is not None:
            await worker.stop()
        if janitor is not None:
            janitor.cancel()
            await asyncio.gather(janitor, return_exceptions=True)
            for job_id in store.pop_checkpoint_cleanup():
                await delete_checkpoint(job_id)
        if startup_tasks:
            for task in startup_tasks:
                task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)
        clear_runtime()
        store.close()


app = FastAPI(
    title="ContractGuard 合同哨兵",
    description="中文货物采购合同 AI 初审：多 Agent + RAG + 规则引擎 + 人工复核",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex)[:64]
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        record_request(request.method, status_code, duration_ms)
        if request.url.path.startswith("/api/"):
            logger.info(
                "request %s %s -> %s %sms rid=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                request_id,
            )


# 必须在路由解析 multipart 之前限制请求体；放在 token guard 之内，
# 这样未带 Token 的超大请求会先得到 401，而不是先落盘。
app.add_middleware(
    BodyLimitMiddleware,
    max_multipart_bytes=settings.max_upload_mb * 1024 * 1024 + 2 * 1024 * 1024,
    max_json_bytes=settings.max_json_body_mb * 1024 * 1024,
)


@app.middleware("http")
async def api_token_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    token = settings.api_token
    path = request.url.path
    if token and path.startswith("/api/") and path != "/api/health":
        supplied = request.headers.get("X-API-Token", "")
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(
            supplied.encode("utf-8"), token.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "需要有效的 API Token。"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


cors_origins: set[str] = set()
if settings.cors_origin and settings.cors_origin != "*":
    cors_origins.add(settings.cors_origin)
if not settings.api_token:
    cors_origins.update(
        {"http://localhost:5173", "http://127.0.0.1:5173"}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 合同全文进入 ReviewResult 后，轮询响应可能达到数十 KB，压缩后再返回。
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 只接受明确配置的 Host，降低 DNS rebinding 读取本机 API 的风险。
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_host_list,
    www_redirect=False,
)

app.include_router(router)

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "message": "ContractGuard 后端已启动。前端尚未构建，请运行 frontend/npm run dev。",
                "docs": "/docs",
                "health": "/api/health",
            }
        )
