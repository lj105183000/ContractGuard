"""任务存储与审查执行器。

任务快照持久化到 SQLite（单表 JSON 快照），单进程内用内存缓存加速。
图内人工复核由 AsyncSqliteSaver checkpoint 支持：暂停在 awaiting_review 的任务
可跨重启恢复决策；进程崩溃时正在执行的 queued/running 任务仍标记为中断。
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.types import Command

from app.config import FEEDBACK_DIR, RUNS_DIR, settings
from app.crypto import (
    CryptoError,
    assert_encryption_ready,
    decrypt_state,
    encrypt_state,
)
from app.graph import runtime
from app.graph.build import build_graph
from app.graph.errors import PipelineError
from app.graph.progress import subscribe, unsubscribe
from app.llm.profiles import chat_runtime, resolve_route
from app.llm.trace import TraceCollector
from app.rag.retriever import get_retriever
from app.schemas import (
    ConfidenceTier,
    Decision,
    EventStatus,
    Finding,
    FindingReviewEvent,
    FindingStatus,
    JobEvent,
    JobState,
    JobStatus,
    JobSummary,
    ReviewDecisionResponse,
    ReviewResult,
    ReviewSummary,
    Severity,
)
from app.services.summary import blocking_high_findings, build_summary

logger = logging.getLogger(__name__)

# 内存热缓存与 SQLite 快照共用；add_event 最多每 2 秒落盘一次，避免
# 每个进度事件都重写包含 full_text 的大 JSON。
EVENT_PERSIST_INTERVAL_S = 2.0


class JobNotFound(KeyError):
    pass


class FindingNotFound(KeyError):
    pass


class LeaseLost(RuntimeError):
    """当前 worker 已不再持有任务租约。"""


class StaleRevision(RuntimeError):
    """任务快照在读取后被其他进程修改。"""


class JobResuming(RuntimeError):
    """任务已被 worker 领取并正在恢复，不能再写入人工决策。"""


class JobNotCancellable(RuntimeError):
    """任务已经结束，不能取消。"""


class JobNotRetryable(RuntimeError):
    """只有 failed/cancelled 且源文件仍在的任务可以重试。"""


class SourceMissing(JobNotRetryable):
    """源文件已被清理，无法重试。"""


def _apply_decision(finding: Finding, decision: Decision, note: str) -> None:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    finding.status = (
        FindingStatus.ACCEPTED
        if decision == Decision.ACCEPTED
        else FindingStatus.REJECTED
    )
    finding.review_note = note
    finding.reviewed_at = now
    finding.review_history.append(
        FindingReviewEvent(decision=decision, note=note, at=now)
    )


@dataclass
class Claim:
    job: JobState
    token: str
    kind: str


class JobStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.RLock()
        self._last_persist: dict[str, float] = {}
        self._resume_locks: dict[str, asyncio.Lock] = {}
        self._checkpoint_cleanup: set[str] = set()
        self._db_path = Path(db_path or settings.job_db_path).expanduser()
        self._conn: sqlite3.Connection | None = None
        assert_encryption_ready()
        self._connection()
        self.recover_incomplete()

    def resume_lock(self, job_id: str) -> asyncio.Lock:
        with self._lock:
            lock = self._resume_locks.get(job_id)
            if lock is None:
                lock = asyncio.Lock()
                self._resume_locks[job_id] = lock
            return lock

    # ------------------------------------------------------------------
    # SQLite 基础设施
    # ------------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                llm_route TEXT,
                batch_id TEXT,
                task_kind TEXT NOT NULL DEFAULT 'review',
                ready INTEGER NOT NULL DEFAULT 1,
                available_at REAL NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_token TEXT,
                lease_expires_at REAL,
                attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 1,
                resume_requested INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                summary_json TEXT,
                error TEXT,
                state_json TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        required_columns = {
            "error": "TEXT",
            "task_kind": "TEXT NOT NULL DEFAULT 'review'",
            "ready": "INTEGER NOT NULL DEFAULT 1",
            "available_at": "REAL NOT NULL DEFAULT 0",
            "lease_owner": "TEXT",
            "lease_token": "TEXT",
            "lease_expires_at": "REAL",
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 1",
            "resume_requested": "INTEGER NOT NULL DEFAULT 0",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "revision": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in required_columns.items():
            if name not in columns:
                try:
                    self._conn.execute(
                        f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_claim "
            "ON jobs(status, available_at, lease_expires_at)"
        )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        self._resume_locks.clear()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def _persist(self, job: JobState, lease_token: str | None = None) -> None:
        summary_json = (
            job.result.summary.model_dump_json() if job.result is not None else None
        )
        state_json = encrypt_state(job.job_id, job.model_dump_json())
        conn = self._connection()
        if lease_token is not None:
            cursor = conn.execute(
                """
                UPDATE jobs SET
                    filename = ?, status = ?, progress = ?, llm_route = ?,
                    batch_id = ?, updated_at = ?, summary_json = ?, error = ?,
                    state_json = ?, revision = revision + 1
                WHERE job_id = ? AND lease_token = ?
                """,
                (
                    job.filename,
                    job.status.value,
                    job.progress,
                    job.llm_route,
                    job.batch_id,
                    job.updated_at,
                    summary_json,
                    job.error,
                    state_json,
                    job.job_id,
                    lease_token,
                ),
            )
            if cursor.rowcount == 0:
                raise LeaseLost(job.job_id)
            job.revision += 1
        else:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, filename, status, progress, llm_route, batch_id,
                    created_at, updated_at, summary_json, error, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    filename = excluded.filename,
                    status = excluded.status,
                    progress = excluded.progress,
                    llm_route = excluded.llm_route,
                    batch_id = excluded.batch_id,
                    updated_at = excluded.updated_at,
                    summary_json = excluded.summary_json,
                    error = excluded.error,
                    state_json = excluded.state_json
                """,
                (
                    job.job_id,
                    job.filename,
                    job.status.value,
                    job.progress,
                    job.llm_route,
                    job.batch_id,
                    job.created_at,
                    job.updated_at,
                    summary_json,
                    job.error,
                    state_json,
                ),
            )
        self._last_persist[job.job_id] = time.monotonic()

    def _persist_cas(self, job: JobState, expected_revision: int) -> None:
        summary_json = (
            job.result.summary.model_dump_json() if job.result is not None else None
        )
        state_json = encrypt_state(job.job_id, job.model_dump_json())
        cursor = self._connection().execute(
            """
            UPDATE jobs SET
                filename = ?, status = ?, progress = ?, llm_route = ?,
                batch_id = ?, updated_at = ?, summary_json = ?, error = ?,
                state_json = ?, revision = revision + 1
            WHERE job_id = ? AND revision = ? AND status <> ?
            """,
            (
                job.filename,
                job.status.value,
                job.progress,
                job.llm_route,
                job.batch_id,
                job.updated_at,
                summary_json,
                job.error,
                state_json,
                job.job_id,
                expected_revision,
                JobStatus.RESUMING.value,
            ),
        )
        if cursor.rowcount == 0:
            raise StaleRevision(job.job_id)
        job.revision += 1
        self._last_persist[job.job_id] = time.monotonic()

    def _persist_reaped(
        self,
        job: JobState,
        *,
        expected_status: str,
        expected_revision: int,
        require_expired_lease: bool,
    ) -> bool:
        """回收路径的条件写入：读取后状态/租约变化则放弃。"""
        summary_json = (
            job.result.summary.model_dump_json() if job.result is not None else None
        )
        state_json = encrypt_state(job.job_id, job.model_dump_json())
        where = "job_id = ? AND status = ? AND revision = ?"
        params: list[object] = [
            job.filename,
            job.status.value,
            job.progress,
            job.llm_route,
            job.batch_id,
            job.updated_at,
            summary_json,
            job.error,
            state_json,
            int(job.cancel_requested),
            job.job_id,
            expected_status,
            expected_revision,
        ]
        if require_expired_lease:
            where += (
                " AND (lease_token IS NULL OR lease_expires_at IS NULL"
                " OR lease_expires_at < ?)"
            )
            params.append(time.time())
        else:
            where += " AND ready = 0"
        cursor = self._connection().execute(
            f"""
            UPDATE jobs SET
                filename = ?, status = ?, progress = ?, llm_route = ?,
                batch_id = ?, updated_at = ?, summary_json = ?, error = ?,
                state_json = ?, cancel_requested = ?, revision = revision + 1
            WHERE {where}
            """,
            params,
        )
        if cursor.rowcount != 1:
            return False
        job.revision = expected_revision + 1
        self._last_persist[job.job_id] = time.monotonic()
        return True

    def _load(self, job_id: str) -> JobState | None:
        row = self._connection().execute(
            """
            SELECT state_json, revision, filename, status, progress, llm_route,
                   batch_id, updated_at, error, cancel_requested
            FROM jobs WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            plaintext = decrypt_state(job_id, row["state_json"])
            job = JobState.model_validate_json(plaintext)
            # claim/mark_ready/request_resume 只更新列，不重写大 JSON，
            # 因此以列上的状态为准，避免轮询或 decide 看到旧状态。
            job.revision = row["revision"]
            job.filename = row["filename"]
            job.status = JobStatus(row["status"])
            job.progress = row["progress"]
            job.llm_route = row["llm_route"]
            job.batch_id = row["batch_id"]
            job.updated_at = row["updated_at"]
            job.error = row["error"]
            job.cancel_requested = bool(row["cancel_requested"])
            return job
        except CryptoError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取任务快照失败 %s：%s", job_id, exc)
            return None

    def _cleanup(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.job_retention_days
        )
        rows = self._connection().execute(
            """
            SELECT job_id, updated_at, state_json FROM jobs
            WHERE status IN (
                'completed', 'completed_with_warnings', 'failed', 'cancelled',
                'awaiting_review', 'resuming'
            )
            AND (lease_token IS NULL OR lease_expires_at < ?)
            """,
            (time.time(),),
        ).fetchall()
        for row in rows:
            try:
                updated = datetime.fromisoformat(row["updated_at"])
            except ValueError:
                continue
            if updated < cutoff:
                job_id = row["job_id"]
                try:
                    job = JobState.model_validate_json(
                        decrypt_state(job_id, row["state_json"])
                    )
                    self._delete_source(job)
                except Exception:  # noqa: BLE001
                    pass
                self._jobs.pop(job_id, None)
                self._last_persist.pop(job_id, None)
                self._resume_locks.pop(job_id, None)
                self._connection().execute(
                    "DELETE FROM jobs WHERE job_id = ?", (job_id,)
                )
                self._queue_checkpoint_cleanup(job_id)

    def _queue_checkpoint_cleanup(self, job_id: str) -> None:
        self._checkpoint_cleanup.add(job_id)

    def pop_checkpoint_cleanup(self) -> list[str]:
        """取出已过期任务 ID，由调用方异步删除对应 checkpoint。"""
        with self._lock:
            pending = list(self._checkpoint_cleanup)
            self._checkpoint_cleanup.clear()
            return pending

    def cleanup_expired(self) -> None:
        """执行保留期清理，供后台 janitor 定时调用。"""
        with self._lock:
            self._cleanup()

    def active_count(self) -> int:
        with self._lock:
            row = self._connection().execute(
                "SELECT COUNT(*) AS count FROM jobs "
                "WHERE status IN ('queued', 'running', 'resuming', 'awaiting_review')"
            ).fetchone()
            return int(row["count"])

    def health(self) -> dict[str, object]:
        """轻量 readiness 信息；DB 不可用时抛异常由调用方返回 503。"""
        with self._lock:
            self._connection().execute("SELECT 1").fetchone()
            rows = self._connection().execute(
                "SELECT status, COUNT(*) AS count FROM jobs "
                "WHERE status IN ('queued', 'running', 'resuming') "
                "GROUP BY status"
            ).fetchall()
            counts = {row["status"]: row["count"] for row in rows}
            return {
                "db": "ok",
                "queued": counts.get("queued", 0),
                "running": counts.get("running", 0),
                "resuming": counts.get("resuming", 0),
            }

    def _delete_source(self, job: JobState) -> None:
        if not job.cleanup_source or not job.source_path:
            return
        try:
            path = Path(job.source_path)
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass

    def cleanup_source(
        self, job_id: str, lease_token: str | None = None
    ) -> None:
        with self._lock:
            if lease_token is not None:
                row = self._connection().execute(
                    "SELECT 1 FROM jobs WHERE job_id = ? AND lease_token = ?",
                    (job_id, lease_token),
                ).fetchone()
                if row is None:
                    return
            job = self._jobs.get(job_id)
            if job is None:
                job = self._load(job_id)
            if job is not None:
                self._delete_source(job)

    # ------------------------------------------------------------------
    # 任务 API
    # ------------------------------------------------------------------

    def create(
        self,
        filename: str,
        llm_route: str | None = None,
        batch_id: str | None = None,
        source_path: str | None = None,
        cleanup_source: bool = True,
    ) -> JobState:
        with self._lock:
            self._cleanup()
            job_id = uuid.uuid4().hex[:12]
            job = JobState(
                job_id=job_id,
                filename=filename,
                llm_route=llm_route,
                batch_id=batch_id,
                source_path=source_path,
                cleanup_source=cleanup_source,
            )
            self._jobs[job_id] = job
            self._persist(job)
            # 新任务默认不可领取；上传/示例保存完成后调用 mark_ready。
            self._connection().execute(
                "UPDATE jobs SET ready = 0 WHERE job_id = ?", (job_id,)
            )
            return job

    def mark_ready(self, job_id: str) -> bool:
        with self._lock:
            updated = self._connection().execute(
                """
                UPDATE jobs SET ready = 1, status = ?, task_kind = 'review',
                    available_at = 0, updated_at = ?, revision = revision + 1
                WHERE job_id = ? AND status = ?
                """,
                (JobStatus.QUEUED.value, self._now(), job_id, JobStatus.QUEUED.value),
            ).rowcount
            if updated:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.status = JobStatus.QUEUED
                    job.updated_at = self._now()
            return bool(updated)

    def request_resume(self, job_id: str) -> bool:
        with self._lock:
            updated = self._connection().execute(
                """
                UPDATE jobs SET resume_requested = 1, task_kind = 'resume',
                    max_attempts = 3, available_at = 0, updated_at = ?,
                    revision = revision + 1
                WHERE job_id = ? AND status = ?
                """,
                (
                    self._now(),
                    job_id,
                    JobStatus.AWAITING_REVIEW.value,
                ),
            ).rowcount
            return bool(updated)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._connection().execute(
                "SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return bool(row and row["cancel_requested"])

    def request_cancel(self, job_id: str) -> JobState:
        """取消排队/待复核任务；运行中任务只置取消标记，由 worker 处理。"""
        with self._lock:
            job = self._load(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job.status in {
                JobStatus.COMPLETED,
                JobStatus.COMPLETED_WITH_WARNINGS,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                raise JobNotCancellable(job_id)
            now = self._now()
            if job.status in {JobStatus.QUEUED, JobStatus.AWAITING_REVIEW}:
                previous_status = job.status
                job.status = JobStatus.CANCELLED
                job.error = "用户取消。"
                job.result = None
                job.warnings = []
                job.interrupt_id = None
                job.pending_review_ids = []
                job.progress = 0
                job.cancel_requested = False
                job.updated_at = now
                state_json = encrypt_state(job.job_id, job.model_dump_json())
                updated = self._connection().execute(
                    """
                    UPDATE jobs SET status = ?, error = ?, cancel_requested = 0,
                        progress = 0, summary_json = NULL, state_json = ?,
                        updated_at = ?, revision = revision + 1,
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL
                    WHERE job_id = ? AND revision = ? AND status = ?
                    """,
                    (
                        JobStatus.CANCELLED.value,
                        job.error,
                        state_json,
                        now,
                        job_id,
                        job.revision,
                        previous_status.value,
                    ),
                ).rowcount
                if not updated:
                    raise StaleRevision(job_id)
                job.revision += 1
                self._jobs[job_id] = job
                self._delete_source(job)
                self._queue_checkpoint_cleanup(job_id)
                return job

            updated = self._connection().execute(
                """
                UPDATE jobs SET cancel_requested = 1, updated_at = ?,
                    revision = revision + 1
                WHERE job_id = ? AND revision = ?
                  AND status IN ('running', 'resuming')
                """,
                (now, job_id, job.revision),
            ).rowcount
            if not updated:
                raise StaleRevision(job_id)
            job.cancel_requested = True
            job.updated_at = now
            job.revision += 1
            self._jobs[job_id] = job
            return job

    def set_cancelled(
        self, job_id: str, lease_token: str | None = None
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id) or self._load(job_id)
            if job is None:
                return
            job.status = JobStatus.CANCELLED
            job.error = "用户取消。"
            job.result = None
            job.warnings = []
            job.interrupt_id = None
            job.pending_review_ids = []
            job.progress = 0
            job.cancel_requested = False
            job.updated_at = self._now()
            self._persist(job, lease_token)
            self._connection().execute(
                "UPDATE jobs SET cancel_requested = 0 WHERE job_id = ?",
                (job_id,),
            )
            self._delete_source(job)
            self._queue_checkpoint_cleanup(job_id)

    def retry(self, job_id: str) -> JobState:
        """把 failed/cancelled 任务重置为 queued；源文件必须仍存在。"""
        with self._lock:
            job = self._load(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
                raise JobNotRetryable(job_id)
            if not job.source_path or not Path(job.source_path).exists():
                raise SourceMissing(job_id)
            job.status = JobStatus.QUEUED
            job.progress = 0
            job.error = None
            job.result = None
            job.warnings = []
            job.events = []
            job.interrupt_id = None
            job.pending_review_ids = []
            job.cancel_requested = False
            job.updated_at = self._now()
            state_json = encrypt_state(job.job_id, job.model_dump_json())
            updated = self._connection().execute(
                """
                UPDATE jobs SET status = ?, progress = 0, error = NULL,
                    summary_json = NULL, state_json = ?, updated_at = ?,
                    ready = 1, task_kind = 'review', attempt = 0,
                    max_attempts = 1, available_at = 0, cancel_requested = 0,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, revision = revision + 1
                WHERE job_id = ? AND revision = ?
                  AND status IN ('failed', 'cancelled')
                """,
                (JobStatus.QUEUED.value, state_json, job.updated_at, job_id, job.revision),
            ).rowcount
            if not updated:
                raise StaleRevision(job_id)
            job.revision += 1
            self._jobs[job_id] = job
            return job

    def claim(self, worker_id: str, lease_seconds: int) -> Claim | None:
        with self._lock:
            token = uuid.uuid4().hex
            now_iso = self._now()
            expires = time.time() + lease_seconds
            row = self._connection().execute(
                """
                UPDATE jobs SET
                    status = CASE
                        WHEN status = ? THEN ?
                        ELSE ?
                    END,
                    task_kind = CASE
                        WHEN status = ? THEN 'resume'
                        ELSE 'review'
                    END,
                    lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                    attempt = attempt + 1, resume_requested = 0,
                    cancel_requested = 0,
                    updated_at = ?, revision = revision + 1
                WHERE job_id = (
                    SELECT job_id FROM jobs
                    WHERE ready = 1 AND cancel_requested = 0 AND (
                        (status = ? AND task_kind = 'review')
                        OR (status = ? AND resume_requested = 1)
                    )
                    ORDER BY available_at ASC, created_at ASC
                    LIMIT 1
                )
                RETURNING job_id, state_json, task_kind, revision
                """,
                (
                    JobStatus.AWAITING_REVIEW.value,
                    JobStatus.RESUMING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.AWAITING_REVIEW.value,
                    worker_id,
                    token,
                    expires,
                    now_iso,
                    JobStatus.QUEUED.value,
                    JobStatus.AWAITING_REVIEW.value,
                ),
            ).fetchone()
            if row is None:
                return None
            try:
                job = JobState.model_validate_json(
                    decrypt_state(row["job_id"], row["state_json"])
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("领取任务时读取快照失败 %s：%s", row["job_id"], exc)
                self._connection().execute(
                    "UPDATE jobs SET status = ?, error = ?, lease_owner = NULL, "
                    "lease_token = NULL, lease_expires_at = NULL WHERE job_id = ?",
                    (
                        JobStatus.FAILED.value,
                        f"任务快照读取失败：{exc}",
                        row["job_id"],
                    ),
                )
                return None
            job.status = (
                JobStatus.RESUMING
                if row["task_kind"] == "resume"
                else JobStatus.RUNNING
            )
            job.revision = row["revision"]
            job.updated_at = now_iso
            self._jobs[job.job_id] = job
            return Claim(job=job, token=token, kind=row["task_kind"])

    def heartbeat(self, job_id: str, token: str) -> bool:
        """续租并返回是否收到取消请求。"""
        with self._lock:
            row = self._connection().execute(
                """
                UPDATE jobs SET lease_expires_at = ?, updated_at = ?,
                    revision = revision + 1
                WHERE job_id = ? AND lease_token = ?
                RETURNING cancel_requested
                """,
                (
                    time.time() + settings.lease_seconds,
                    self._now(),
                    job_id,
                    token,
                ),
            ).fetchone()
            if row is None:
                raise LeaseLost(job_id)
            return bool(row["cancel_requested"])

    def release(self, job_id: str, token: str) -> None:
        with self._lock:
            self._connection().execute(
                """
                UPDATE jobs SET lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL
                WHERE job_id = ? AND lease_token = ?
                """,
                (job_id, token),
            )

    def get(self, job_id: str, fresh: bool = False) -> JobState:
        with self._lock:
            job = None if fresh else self._jobs.get(job_id)
            if job is None:
                job = self._load(job_id)
                if job is None:
                    raise JobNotFound(job_id)
                self._jobs[job_id] = job
            return job

    def set_source(
        self, job_id: str, source_path: str, cleanup_source: bool = True
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.source_path = source_path
            job.cleanup_source = cleanup_source
            job.updated_at = self._now()
            self._persist(job)

    def discard(self, job_id: str) -> None:
        """移除尚未调度的任务（上传被拒、空文件等）。"""
        with self._lock:
            self._jobs.pop(job_id, None)
            self._last_persist.pop(job_id, None)
            self._connection().execute(
                "DELETE FROM jobs WHERE job_id = ?", (job_id,)
            )

    def add_event(
        self, job_id: str, event: JobEvent, lease_token: str | None = None
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.events.append(event)
            job.progress = max(job.progress, event.progress)
            job.updated_at = self._now()
            last = self._last_persist.get(job_id, 0.0)
            if time.monotonic() - last >= EVENT_PERSIST_INTERVAL_S:
                try:
                    self._persist(job, lease_token)
                except LeaseLost:
                    logger.warning("任务 %s 已失去租约，停止写进度", job_id)

    def set_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.RUNNING
                job.updated_at = self._now()
                self._persist(job)

    def set_awaiting_review(
        self,
        job_id: str,
        report: ReviewResult,
        warnings: list[str],
        interrupt_id: str | None,
        pending_ids: list[str],
        lease_token: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.result = report
            job.warnings = list(dict.fromkeys(warnings))
            job.status = JobStatus.AWAITING_REVIEW
            job.progress = 100
            job.interrupt_id = interrupt_id
            job.pending_review_ids = list(dict.fromkeys(pending_ids))
            job.updated_at = self._now()
            self._persist(job, lease_token)
            # 进入人工复核后重新计数，保证 resuming 有完整的 3 次尝试。
            self._connection().execute(
                "UPDATE jobs SET attempt = 0 WHERE job_id = ?", (job_id,)
            )

    def begin_resume(self, job_id: str) -> bool:
        """在进程内原子地把 awaiting_review 切到 resuming。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.AWAITING_REVIEW:
                return False
            job.status = JobStatus.RESUMING
            job.updated_at = self._now()
            self._persist(job)
            return True

    def set_result(
        self,
        job_id: str,
        report: ReviewResult,
        warnings: list[str],
        lease_token: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.result = report
            job.warnings = list(dict.fromkeys(warnings))
            job.status = (
                JobStatus.COMPLETED_WITH_WARNINGS
                if job.warnings
                else JobStatus.COMPLETED
            )
            job.progress = 100
            job.interrupt_id = None
            job.pending_review_ids = []
            job.updated_at = self._now()
            self._persist(job, lease_token)

    def set_failed(
        self, job_id: str, message: str, lease_token: str | None = None
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.error = message
            job.interrupt_id = None
            job.pending_review_ids = []
            job.updated_at = self._now()
            self._persist(job, lease_token)
            self._delete_source(job)
            # 失败任务的 checkpoint 也应由 janitor 兜底删除。
            self._queue_checkpoint_cleanup(job_id)

    def decide(
        self, job_id: str, finding_id: str, decision: Decision, note: str = ""
    ) -> ReviewDecisionResponse:
        with self._lock:
            for _attempt in range(3):
                job = self._load(job_id)
                if job is None or job.result is None:
                    raise JobNotFound(job_id)
                if job.status == JobStatus.RESUMING:
                    raise JobResuming("复核正在完成，请稍候。")
                finding = next(
                    (item for item in job.result.findings if item.id == finding_id),
                    None,
                )
                if finding is None:
                    raise FindingNotFound(finding_id)
                _apply_decision(finding, decision, note)
                job.result.summary = build_summary(job.result.findings)
                job.updated_at = self._now()
                try:
                    self._persist_cas(job, job.revision)
                except StaleRevision:
                    continue
                self._jobs[job_id] = job
                summary = job.result.summary
                updated = finding.model_copy(deep=True)
                break
            else:
                raise StaleRevision(job_id)

        self._append_feedback(job_id, updated, decision, note)
        return ReviewDecisionResponse(finding=updated, summary=summary)

    def update_suggestion(
        self, job_id: str, finding_id: str, suggested_text: str
    ) -> Finding:
        text = suggested_text.strip()
        if not text:
            raise FindingNotFound(finding_id)
        with self._lock:
            for _attempt in range(3):
                job = self._load(job_id)
                if job is None or job.result is None:
                    raise JobNotFound(job_id)
                if job.status == JobStatus.RESUMING:
                    raise JobResuming("复核正在完成，请稍候。")
                finding = next(
                    (item for item in job.result.findings if item.id == finding_id),
                    None,
                )
                if finding is None:
                    raise FindingNotFound(finding_id)
                finding.suggested_text = text
                finding.suggested_text_source = "human"
                job.updated_at = self._now()
                try:
                    self._persist_cas(job, job.revision)
                except StaleRevision:
                    continue
                self._jobs[job_id] = job
                return finding.model_copy(deep=True)
            raise StaleRevision(job_id)

    def decide_many(
        self,
        job_id: str,
        finding_ids: list[str] | None,
        decision: Decision,
        note: str = "",
    ) -> tuple[int, ReviewSummary]:
        """批量处理一个 job 内所有待处理的已核实高风险；返回处理条数与新 summary。"""
        with self._lock:
            for _attempt in range(3):
                job = self._load(job_id)
                if job is None or job.result is None:
                    raise JobNotFound(job_id)
                if job.status == JobStatus.RESUMING:
                    raise JobResuming("复核正在完成，请稍候。")
                targets = set(finding_ids) if finding_ids else None
                changed: list[Finding] = []
                for finding in job.result.findings:
                    if targets is not None and finding.id not in targets:
                        continue
                    if finding.status in {
                        FindingStatus.ACCEPTED,
                        FindingStatus.REJECTED,
                    }:
                        continue
                    if (
                        finding.severity != Severity.HIGH
                        or finding.confidence_tier != ConfidenceTier.CONFIRMED
                    ):
                        continue
                    _apply_decision(finding, decision, note)
                    changed.append(finding.model_copy(deep=True))
                if changed:
                    job.result.summary = build_summary(job.result.findings)
                    job.updated_at = self._now()
                    try:
                        self._persist_cas(job, job.revision)
                    except StaleRevision:
                        continue
                self._jobs[job_id] = job
                summary = job.result.summary
                break
            else:
                raise StaleRevision(job_id)

        for finding in changed:
            self._append_feedback(job_id, finding, decision, note)
        return len(changed), summary

    def list_jobs(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        batch_id: str | None = None,
        order: str = "updated_at",
    ) -> list[JobSummary]:
        with self._lock:
            self._cleanup()
            limit = max(1, min(int(limit), 200))
            offset = max(0, int(offset))
            query = (
                "SELECT job_id, filename, status, progress, llm_route, batch_id, "
                "created_at, updated_at, error, summary_json FROM jobs"
            )
            clauses: list[str] = []
            params: list[object] = []
            if status:
                clauses.append("status = ?")
                params.append(status)
            if batch_id:
                clauses.append("batch_id = ?")
                params.append(batch_id)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            order_column = "created_at" if order == "created_at" else "updated_at"
            query += f" ORDER BY {order_column} DESC, job_id ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._connection().execute(query, params).fetchall()
            summaries: list[JobSummary] = []
            for row in rows:
                summary = None
                if row["summary_json"]:
                    try:
                        summary = ReviewSummary.model_validate_json(
                            row["summary_json"]
                        )
                    except Exception:  # noqa: BLE001
                        summary = None
                try:
                    job_status = JobStatus(row["status"])
                except ValueError:
                    job_status = JobStatus.QUEUED
                summaries.append(
                    JobSummary(
                        job_id=row["job_id"],
                        filename=row["filename"],
                        status=job_status,
                        progress=row["progress"],
                        llm_route=row["llm_route"],
                        batch_id=row["batch_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        error=row["error"],
                        summary=summary,
                    )
                )
            return summaries

    def reset(self) -> None:
        """清空内存与数据库，仅供测试和冒烟脚本使用。"""
        with self._lock:
            self._jobs.clear()
            self._last_persist.clear()
            self._resume_locks.clear()
            self._checkpoint_cleanup.clear()
            self._connection().execute("DELETE FROM jobs")

    def _clear_lease(self, job_id: str) -> None:
        self._connection().execute(
            "UPDATE jobs SET lease_owner = NULL, lease_token = NULL, "
            "lease_expires_at = NULL WHERE job_id = ?",
            (job_id,),
        )

    def reap_expired_leases(self) -> None:
        """回收过期/遗留的 running/resuming 任务。

        - 有 lease 且未过期：跳过；
        - running 过期/无 lease：标记 failed（不自动重放 LLM）；
        - resuming 过期/无 lease：attempt < max_attempts 时退回 awaiting_review
          并 request_resume，等待重新领取；否则 failed；
        - ready=0 且排队超过 1 小时的 queued（上传中途崩溃）：标记 failed。
        """
        with self._lock:
            now = time.time()
            queue_cutoff = (
                datetime.now(timezone.utc).astimezone() - timedelta(hours=1)
            ).isoformat(timespec="seconds")
            rows = self._connection().execute(
                """
                SELECT job_id, state_json, status, attempt, max_attempts,
                       lease_token, lease_expires_at, revision, cancel_requested
                FROM jobs
                WHERE status IN ('running', 'resuming')
                   OR (status = 'queued' AND ready = 0 AND updated_at < ?)
                """,
                (queue_cutoff,),
            ).fetchall()
            for row in rows:
                try:
                    job = JobState.model_validate_json(
                        decrypt_state(row["job_id"], row["state_json"])
                    )
                except CryptoError as exc:
                    logger.error(
                        "跳过无法解密的任务 %s：%s", row["job_id"], exc
                    )
                    continue
                except Exception:  # noqa: BLE001
                    continue
                job.revision = row["revision"]

                if row["status"] == JobStatus.QUEUED.value:
                    job.status = JobStatus.FAILED
                    job.error = "任务排队超时，请重新上传。"
                    job.updated_at = self._now()
                    if not self._persist_reaped(
                        job,
                        expected_status=row["status"],
                        expected_revision=row["revision"],
                        require_expired_lease=False,
                    ):
                        continue
                    self._jobs[job.job_id] = job
                    self._delete_source(job)
                    continue

                expired = (
                    row["lease_token"] is None
                    or row["lease_expires_at"] is None
                    or row["lease_expires_at"] < now
                )
                if not expired:
                    continue
                if row["cancel_requested"]:
                    job.status = JobStatus.CANCELLED
                    job.error = "用户取消。"
                    job.result = None
                    job.warnings = []
                    job.interrupt_id = None
                    job.pending_review_ids = []
                    job.progress = 0
                    job.cancel_requested = False
                    job.updated_at = self._now()
                    if not self._persist_reaped(
                        job,
                        expected_status=row["status"],
                        expected_revision=row["revision"],
                        require_expired_lease=True,
                    ):
                        continue
                    self._jobs[job.job_id] = job
                    self._clear_lease(job.job_id)
                    self._delete_source(job)
                    self._queue_checkpoint_cleanup(job.job_id)
                    continue
                # DB 的 status 才是领取时的权威值；state_json 可能还停留在
                # 领取前的快照（claim 只更新列，不重写大 JSON）。
                retry_resume = (
                    row["status"] == JobStatus.RESUMING.value
                    and job.result is not None
                    and row["attempt"] < row["max_attempts"]
                )
                if retry_resume:
                    job.status = JobStatus.AWAITING_REVIEW
                    job.error = None
                else:
                    job.status = JobStatus.FAILED
                    job.error = "worker 租约过期，任务中断，请重新审查。"
                job.updated_at = self._now()
                if not self._persist_reaped(
                    job,
                    expected_status=row["status"],
                    expected_revision=row["revision"],
                    require_expired_lease=True,
                ):
                    # 任务在 SELECT 后被领取/续租/决策；下一轮再看。
                    continue
                self._jobs[job.job_id] = job
                self._clear_lease(job.job_id)
                if retry_resume:
                    if job.result is not None:
                        self.request_resume(job.job_id)
                else:
                    self._delete_source(job)
                    self._queue_checkpoint_cleanup(job.job_id)

    def recover_incomplete(self) -> None:
        """兼容旧调用：启动时回收过期租约。"""
        self.reap_expired_leases()

    def awaiting_resume_jobs(self) -> list[JobState]:
        """已暂停且所有阻断项都已决策的任务；启动后可自动收尾。"""
        with self._lock:
            rows = self._connection().execute(
                "SELECT job_id FROM jobs WHERE status = ?",
                (JobStatus.AWAITING_REVIEW.value,),
            ).fetchall()
            candidates: list[JobState] = []
            for row in rows:
                job = self.get(row["job_id"])
                if job.result is not None and not blocking_high_findings(
                    job.result.findings
                ):
                    candidates.append(job)
            return candidates

    def pending_high_risk(self, job_id: str) -> list[Finding]:
        """所有未处理的高风险，保留给旧调用方。"""
        job = self.get(job_id, fresh=True)
        if job.result is None:
            return []
        return [
            finding
            for finding in job.result.findings
            if finding.severity == Severity.HIGH
            and finding.status in {FindingStatus.PENDING, FindingStatus.NEEDS_HUMAN}
        ]

    def blocking_high_risk(self, job_id: str) -> list[Finding]:
        """只有已核实（规则引擎命中）的高风险才阻断导出。"""
        job = self.get(job_id, fresh=True)
        if job.result is None:
            return []
        return blocking_high_findings(job.result.findings)

    def advisory_high_risk(self, job_id: str) -> list[Finding]:
        return [
            finding
            for finding in self.pending_high_risk(job_id)
            if finding.confidence_tier == ConfidenceTier.ADVISORY
        ]

    def _append_feedback(
        self, job_id: str, finding: Finding, decision: Decision, note: str
    ) -> None:
        try:
            FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
            record = {
                "job_id": job_id,
                "finding_id": finding.id,
                "rule_id": finding.rule_id,
                "category": finding.category.value,
                "severity": finding.severity.value,
                "decision": decision.value,
                "note": note,
                "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            }
            with (FEEDBACK_DIR / "feedback.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入人工反馈失败：%s", exc)


store = JobStore()
atexit.register(store.close)


def _remove_source(source_path: Path) -> None:
    try:
        source_path.unlink(missing_ok=True)
        source_path.parent.rmdir()
    except OSError:
        pass


def _extract_interrupts(result) -> list:  # type: ignore[no-untyped-def]
    interrupts = []
    if isinstance(result, dict):
        interrupts = list(result.get("__interrupt__") or [])
    else:
        interrupts = list(getattr(result, "interrupts", None) or [])
    return interrupts


def _result_report(result):  # type: ignore[no-untyped-def]
    if isinstance(result, dict):
        return result.get("report")
    value = getattr(result, "value", None)
    if isinstance(value, dict):
        return value.get("report")
    return None


async def delete_checkpoint(job_id: str) -> None:
    """删除任务的 checkpoint（含合同全文），失败只记日志。"""
    if not runtime.hitl_active():
        return
    try:
        await runtime.get_checkpointer().adelete_thread(job_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除 checkpoint 失败 %s：%s", job_id, exc)


def _result_trace(result):  # type: ignore[no-untyped-def]
    if isinstance(result, dict):
        return result.get("trace")
    value = getattr(result, "value", None)
    if isinstance(value, dict):
        return value.get("trace")
    return None


async def run_review(
    job_id: str,
    source_path: Path | None = None,
    cleanup: bool | None = None,
    llm_route: str | None = None,
    lease_token: str | None = None,
    job: JobState | None = None,
) -> None:
    """执行一次完整审查。由 worker、FastAPI BackgroundTasks 或测试内联调用。"""
    job = job or store.get(job_id)
    if lease_token is None:
        store.set_running(job_id)
    if source_path is None:
        if not job.source_path:
            store.set_failed(job_id, "任务缺少源文件。", lease_token=lease_token)
            return
        source_path = Path(job.source_path)
    if cleanup is None:
        cleanup = job.cleanup_source
    trace = TraceCollector()
    started = time.perf_counter()
    paused = False
    subscribe(
        job_id,
        lambda event: store.add_event(job_id, event, lease_token=lease_token),
    )

    runtime_settings = chat_runtime(settings)
    route = None
    route_warnings: list[str] = []
    requested_route = job.llm_route or llm_route
    try:
        route = resolve_route(
            requested_route,
            require_available=runtime_settings.mode == "live",
        )
    except Exception as exc:  # noqa: BLE001
        try:
            route = resolve_route(
                None, require_available=runtime_settings.mode == "live"
            )
            route_warnings.append(
                f"请求的模型路由不可用，已回退到默认路由：{exc}"
            )
        except Exception:  # noqa: BLE001
            route = runtime_settings.default_route

    state = {
        "job_id": job_id,
        "filename": job.filename,
        "source_path": str(source_path),
        "llm_route": route.id if route else requested_route,
        "llm_mode": runtime_settings.mode,
        "trace": trace,
    }
    hitl = runtime.hitl_active()
    try:
        if hitl:
            graph = runtime.get_graph()
            config = {"configurable": {"thread_id": job_id}, "recursion_limit": 30}
        else:
            graph = build_graph()
            config = {"recursion_limit": 30}
        if hitl:
            result = await graph.ainvoke(
                state, config=config, durability="sync"
            )
        else:
            result = await graph.ainvoke(state, config=config)
        if store.is_cancel_requested(job_id):
            store.set_cancelled(job_id, lease_token=lease_token)
            return
        interrupts = _extract_interrupts(result)
        report = _result_report(result)
        if report is None and interrupts:
            snapshot = await graph.aget_state(config)
            if snapshot.values:
                report = snapshot.values.get("report")
        if report is None:
            raise PipelineError("审查流程没有生成报告。")

        duration_ms = int((time.perf_counter() - started) * 1000)
        retriever = get_retriever()
        if route_warnings:
            report.warnings = list(dict.fromkeys(report.warnings + route_warnings))
        if retriever.last_warning:
            report.warnings = list(
                dict.fromkeys(report.warnings + [retriever.last_warning])
            )
        report.mode = runtime_settings.mode
        effective_trace = _result_trace(result) or trace
        report.usage = effective_trace.usage(
            duration_ms=duration_ms,
            mode=runtime_settings.mode,
            embedding_mode=retriever.retrieval_mode,
            rerank_mode=retriever.rerank_mode,
            llm_route=route.id if route else requested_route,
            llm_route_label=route.label if route else None,
        )

        if interrupts:
            pending = blocking_high_findings(report.findings)
            store.add_event(
                job_id,
                JobEvent(
                    node="human_review",
                    label="等待人工复核",
                    status=EventStatus.COMPLETED,
                    message=f"{len(pending)} 条已核实高风险待复核",
                    progress=100,
                ),
                lease_token=lease_token,
            )
            store.set_awaiting_review(
                job_id,
                report,
                report.warnings,
                interrupts[0].id,
                [finding.id for finding in pending],
                lease_token=lease_token,
            )
            paused = True
            return

        store.add_event(
            job_id,
            JobEvent(
                node="complete",
                label="审查完成",
                status=EventStatus.COMPLETED,
                message=f"{len(report.findings)} 条风险；用时 {duration_ms / 1000:.1f}s",
                progress=100,
            ),
            lease_token=lease_token,
        )
        store.set_result(
            job_id, report, report.warnings, lease_token=lease_token
        )
        if hitl:
            await delete_checkpoint(job_id)
        if settings.save_traces:
            _save_trace(job_id, report)
    except LeaseLost:
        logger.warning("任务 %s 已失去租约，停止执行", job_id)
    except PipelineError as exc:
        store.add_event(
            job_id,
            JobEvent(
                node="failed",
                label="审查失败",
                status=EventStatus.FAILED,
                message=str(exc),
                progress=0,
            ),
            lease_token=lease_token,
        )
        store.set_failed(job_id, str(exc), lease_token=lease_token)
        if hitl:
            await delete_checkpoint(job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("审查任务异常：%s", exc)
        store.add_event(
            job_id,
            JobEvent(
                node="failed",
                label="审查失败",
                status=EventStatus.FAILED,
                message="服务内部错误，请查看后端日志或重试。",
                progress=0,
            ),
            lease_token=lease_token,
        )
        store.set_failed(
            job_id, f"服务内部错误：{exc}", lease_token=lease_token
        )
        if hitl:
            await delete_checkpoint(job_id)
    finally:
        unsubscribe(job_id)
        if cleanup and not paused:
            if lease_token is None:
                _remove_source(source_path)
            else:
                store.cleanup_source(job_id, lease_token=lease_token)


async def resume_review(
    job_id: str,
    lease_token: str | None = None,
    job: JobState | None = None,
) -> None:
    """应用已保存的人工决策，从 checkpoint 继续图执行。"""
    lock = store.resume_lock(job_id)
    async with lock:
        try:
            job = job or store.get(job_id, fresh=True)
        except JobNotFound:
            return
        if lease_token is None:
            if job.status != JobStatus.AWAITING_REVIEW:
                return
            if store.blocking_high_risk(job_id):
                return
            if not store.begin_resume(job_id):
                return
        elif job.status != JobStatus.RESUMING:
            return
        if not runtime.hitl_active():
            store.set_failed(
                job_id,
                "服务未启用 HITL，无法恢复人工复核。",
                lease_token=lease_token,
            )
            return

        graph = runtime.get_graph()
        config = {"configurable": {"thread_id": job_id}, "recursion_limit": 30}
        try:
            snapshot = await graph.aget_state(config)
            if not snapshot.interrupts:
                if job.result is None or store.blocking_high_risk(job_id):
                    raise PipelineError("没有可恢复的人工复核断点。")
                store.set_result(
                    job_id,
                    job.result,
                    job.warnings,
                    lease_token=lease_token,
                )
                store.cleanup_source(job_id, lease_token=lease_token)
                return

            decisions = {
                finding.id: {
                    "decision": finding.status.value,
                    "note": finding.review_note,
                }
                for finding in (job.result.findings if job.result else [])
                if finding.status
                in {FindingStatus.ACCEPTED, FindingStatus.REJECTED}
            }
            if not decisions:
                raise PipelineError("没有可应用的人工复核决策。")

            store.add_event(
                job_id,
                JobEvent(
                    node="resume",
                    label="应用人工复核",
                    status=EventStatus.STARTED,
                    progress=96,
                ),
                lease_token=lease_token,
            )
            command = Command(
                resume={snapshot.interrupts[0].id: {"decisions": decisions}}
            )
            result = await graph.ainvoke(
                command, config=config, durability="sync"
            )
            if store.is_cancel_requested(job_id):
                store.set_cancelled(job_id, lease_token=lease_token)
                return
            interrupts = _extract_interrupts(result)
            report = _result_report(result)
            if report is None:
                snapshot = await graph.aget_state(config)
                report = snapshot.values.get("report") if snapshot.values else None
                if not interrupts:
                    interrupts = list(snapshot.interrupts)
            if report is None:
                raise PipelineError("人工复核恢复后没有生成报告。")

            if interrupts:
                pending = blocking_high_findings(report.findings)
                store.add_event(
                    job_id,
                    JobEvent(
                        node="human_review",
                        label="仍需人工复核",
                        status=EventStatus.COMPLETED,
                        message=f"{len(pending)} 条已核实高风险待复核",
                        progress=100,
                    ),
                    lease_token=lease_token,
                )
                store.set_awaiting_review(
                    job_id,
                    report,
                    report.warnings,
                    interrupts[0].id,
                    [finding.id for finding in pending],
                    lease_token=lease_token,
                )
                return

            if job.result is not None:
                report.usage = job.result.usage
                report.mode = job.result.mode
                report.warnings = list(
                    dict.fromkeys(job.result.warnings + report.warnings)
                )
            store.add_event(
                job_id,
                JobEvent(
                    node="resume",
                    label="复核完成",
                    status=EventStatus.COMPLETED,
                    message="人工复核结果已应用",
                    progress=100,
                ),
                lease_token=lease_token,
            )
            if job.result is not None:
                prior = {finding.id: finding for finding in job.result.findings}
                for finding in report.findings:
                    previous = prior.get(finding.id)
                    if previous is not None:
                        finding.review_history = previous.review_history
                        finding.reviewed_at = previous.reviewed_at
            store.set_result(
                job_id, report, report.warnings, lease_token=lease_token
            )
            store.cleanup_source(job_id, lease_token=lease_token)
            await delete_checkpoint(job_id)
            if settings.save_traces:
                _save_trace(job_id, report)
        except LeaseLost:
            logger.warning("任务 %s 已失去租约，停止恢复", job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("恢复人工复核失败：%s", exc)
            store.set_failed(
                job_id,
                f"恢复人工复核失败：{exc}",
                lease_token=lease_token,
            )
            await delete_checkpoint(job_id)


def _save_trace(job_id: str, report: ReviewResult) -> None:  # type: ignore[no-untyped-def]
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{job_id}.json"
        path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存 trace 失败：%s", exc)
