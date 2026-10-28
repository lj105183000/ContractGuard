"""集中管理路径、环境变量和运行模式。

所有模块都从这里读取配置，避免散落的 os.getenv。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
IS_FROZEN = bool(getattr(sys, "frozen", False))
# PyInstaller onedir 下 __file__ 位于 _internal，.env 应相对于 exe 所在目录。
APP_ROOT = Path(sys.executable).resolve().parent if IS_FROZEN else BACKEND_DIR
load_dotenv(APP_ROOT / ".env")

PROJECT_DIR = BACKEND_DIR.parent
# 桌面端打包后安装目录只读，Electron 会用 CONTRACTGUARD_DATA_DIR 指向用户数据目录。
DATA_DIR = Path(
    os.getenv("CONTRACTGUARD_DATA_DIR", str(APP_ROOT / "data"))
).expanduser()
LEGAL_DIR = DATA_DIR / "legal"
SAMPLES_DIR = DATA_DIR / "samples"
INDEX_CACHE_DIR = Path(
    os.getenv("INDEX_CACHE_DIR", str(DATA_DIR / "index_cache"))
).expanduser()
FEEDBACK_DIR = DATA_DIR / "feedback"
UPLOAD_DIR = Path(
    os.getenv("CONTRACTGUARD_UPLOAD_DIR", str(DATA_DIR / "uploads"))
).expanduser()
RUNS_DIR = DATA_DIR / "runs"
# 开发模式下 APP_ROOT 是 backend/，与原来的 PROJECT_DIR/frontend/dist 一致；
# 打包后 Electron 用 CONTRACTGUARD_FRONTEND_DIST 指向 resources/frontend/dist。
FRONTEND_DIST = Path(
    os.getenv(
        "CONTRACTGUARD_FRONTEND_DIST",
        str(APP_ROOT.parent / "frontend" / "dist"),
    )
).expanduser()

LEGAL_TERMS_PATH = DATA_DIR / "legal_terms.txt"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """运行配置。

    live 模式需要 DASHSCOPE_API_KEY；没有 Key 时自动降级为 mock，
    并在 /api/meta 中把原因告诉前端，避免静默失败。
    """

    llm_mode: str = os.getenv("LLM_MODE", "mock").strip().lower()
    llm_route: str = os.getenv("LLM_ROUTE", "").strip()
    llm_profiles_path: str = os.getenv(
        "CONTRACTGUARD_LLM_PROFILES", str(DATA_DIR / "llm_profiles.yaml")
    ).strip()
    llm_timeout_s: int = max(5, _env_int("LLM_TIMEOUT_S", 90))
    llm_deadline_s: int = max(30, _env_int("LLM_DEADLINE_S", 180))
    api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).strip()
    llm_model: str = os.getenv("LLM_MODEL", "qwen-plus").strip()
    embedding_api_key: str = os.getenv(
        "EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")
    ).strip()
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL",
        os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    ).strip()
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v3").strip()
    embedding_dim: int = _env_int("EMBEDDING_DIM", 1024)
    rerank_enabled: bool = _env_bool("RERANK_ENABLED", False)
    rerank_api_style: str = os.getenv("RERANK_API_STYLE", "native").strip().lower()
    rerank_instruct: str = os.getenv("RERANK_INSTRUCT", "").strip()
    rerank_model: str = os.getenv("RERANK_MODEL", "gte-rerank-v2").strip()
    rerank_base_url: str = os.getenv(
        "RERANK_BASE_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
    ).strip()
    rerank_api_key: str = os.getenv(
        "RERANK_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")
    ).strip()
    rerank_top_n: int = max(1, _env_int("RERANK_TOP_N", 20))
    rerank_timeout_s: int = max(5, _env_int("RERANK_TIMEOUT_S", 30))
    cors_origin: str = os.getenv("CORS_ORIGIN", "http://localhost:5173").strip()
    max_upload_mb: int = max(1, _env_int("MAX_UPLOAD_MB", 50))
    max_pdf_pages: int = max(1, _env_int("PDF_MAX_PAGES", 200))
    max_docx_uncompressed_mb: int = max(
        1, _env_int("DOCX_MAX_UNCOMPRESSED_MB", 200)
    )
    max_active_jobs: int = max(1, _env_int("MAX_ACTIVE_JOBS", 20))
    rate_limit_per_minute: int = max(0, _env_int("RATE_LIMIT_PER_MINUTE", 0))
    # 可选单租户 Bearer Token；为空表示不启用鉴权（本地 demo 默认）。
    api_token: str = os.getenv("CONTRACTGUARD_API_TOKEN", "").strip()
    ocr_max_pages: int = max(1, _env_int("OCR_MAX_PAGES", 50))
    ocr_render_scale: float = min(
        4.0, max(0.5, _env_float("OCR_RENDER_SCALE", 2.0))
    )
    ocr_min_score: float = min(1.0, max(0.0, _env_float("OCR_MIN_SCORE", 0.5)))
    ocr_min_page_chars: int = max(1, _env_int("OCR_MIN_PAGE_CHARS", 20))
    ocr_max_render_side: int = max(500, _env_int("OCR_MAX_RENDER_SIDE", 3000))
    sync_jobs: bool = _env_bool("SYNC_JOBS", False)
    save_traces: bool = _env_bool("SAVE_TRACES", False)
    job_db_path: str = os.getenv(
        "CONTRACTGUARD_DB_PATH", str(DATA_DIR / "jobs.sqlite3")
    ).strip()
    job_retention_days: int = max(1, _env_int("JOB_RETENTION_DAYS", 7))
    hitl_enabled: bool = _env_bool("HITL_ENABLED", True)
    checkpoint_db_path: str = os.getenv(
        "CONTRACTGUARD_CHECKPOINT_DB_PATH",
        str(DATA_DIR / "checkpoints.sqlite3"),
    ).strip()
    hitl_resume_on_startup: bool = _env_bool("HITL_RESUME_ON_STARTUP", True)
    worker_enabled: bool = _env_bool("WORKER_ENABLED", True)
    worker_concurrency: int = max(1, _env_int("WORKER_CONCURRENCY", 2))
    worker_poll_interval_s: float = max(
        0.2, _env_float("WORKER_POLL_INTERVAL_S", 1.0)
    )
    lease_seconds: int = max(10, _env_int("LEASE_SECONDS", 60))
    # 心跳至少要给租约留出两次机会，避免配置错误导致租约在两次心跳间过期。
    heartbeat_seconds: int = min(
        max(2, _env_int("HEARTBEAT_SECONDS", 15)),
        max(2, lease_seconds // 2),
    )
    encryption_enabled: bool = _env_bool("ENCRYPTION_ENABLED", False)
    master_key: str = os.getenv("CONTRACTGUARD_MASTER_KEY", "").strip()

    @property
    def live_requested(self) -> bool:
        return self.llm_mode == "live"

    @property
    def effective_mode(self) -> str:
        if self.live_requested and self.api_key:
            return "live"
        return "mock"

    @property
    def mode_reason(self) -> str:
        if self.live_requested and not self.api_key:
            return "LLM_MODE=live 但未配置 DASHSCOPE_API_KEY，已降级为 mock"
        if self.llm_mode == "mock":
            return "当前为 mock 模式：不发外部请求，仅规则引擎 + 模板建议"
        return "live 模式：调用百炼 qwen-plus 与 text-embedding-v3"

    @property
    def embedding_live(self) -> bool:
        return self.live_requested and bool(self.embedding_api_key)

    @property
    def is_live(self) -> bool:
        """兼容旧调用；现在只表示 embedding 是否可用，聊天模式由 profiles 决定。"""
        return self.embedding_live


settings = Settings()

DATA_DIRS = [
    LEGAL_DIR,
    SAMPLES_DIR,
    INDEX_CACHE_DIR,
    FEEDBACK_DIR,
    UPLOAD_DIR,
    RUNS_DIR,
]


def ensure_dirs() -> None:
    """确保运行时数据目录存在。"""
    for path in DATA_DIRS:
        path.mkdir(parents=True, exist_ok=True)


DISCLAIMER = (
    "本报告由 AI 辅助生成，仅用于合同风险初筛，不构成法律意见。"
    "所有风险结论，尤其是高风险项，必须由具备法律知识的人员复核后再使用。"
)
