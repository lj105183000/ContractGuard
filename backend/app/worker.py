"""SQLite 租约 worker：多进程/多 worker 时由 DB CAS 保证每个任务只有一个 owner。"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from pathlib import Path

from app.config import settings
from app.services.jobs import Claim, LeaseLost, resume_review, run_review, store

logger = logging.getLogger(__name__)

WORKER_STATE: dict[str, object] = {
    "status": "disabled",
    "worker_id": None,
    "started_at": None,
    "last_poll_at": None,
    "active_jobs": 0,
}


def worker_state() -> dict[str, object]:
    return dict(WORKER_STATE)


class WorkerPool:
    def __init__(self) -> None:
        self.worker_id = (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        WORKER_STATE.update(
            {
                "status": "running",
                "worker_id": self.worker_id,
                "started_at": time.time(),
                "last_poll_at": None,
                "active_jobs": 0,
            }
        )
        self._loop_task = asyncio.create_task(
            self._loop(), name="contractguard-worker"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper(), name="contractguard-reaper"
        )
        logger.info(
            "worker 已启动：id=%s concurrency=%s lease=%ss",
            self.worker_id,
            settings.worker_concurrency,
            settings.lease_seconds,
        )

    async def stop(self) -> None:
        WORKER_STATE["status"] = "stopped"
        self._stop.set()
        running = [task for task in (self._loop_task, self._reaper_task) if task]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        if self._tasks:
            _done, pending = await asyncio.wait(self._tasks, timeout=10)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info("worker 已停止：id=%s", self.worker_id)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _loop(self) -> None:
        semaphore = asyncio.Semaphore(settings.worker_concurrency)
        while not self._stop.is_set():
            WORKER_STATE["last_poll_at"] = time.time()
            await semaphore.acquire()
            if self._stop.is_set():
                semaphore.release()
                break
            try:
                claim = store.claim(self.worker_id, settings.lease_seconds)
            except Exception:  # noqa: BLE001
                logger.exception("领取任务失败，稍后重试")
                semaphore.release()
                await self._wait(max(1.0, settings.worker_poll_interval_s))
                continue
            if claim is None:
                semaphore.release()
                await self._wait(settings.worker_poll_interval_s)
                continue
            task = asyncio.create_task(
                self._execute(claim), name=f"contractguard-job-{claim.job.job_id}"
            )
            self._tasks.add(task)

            def _done(finished: asyncio.Task) -> None:
                self._tasks.discard(finished)
                semaphore.release()

            task.add_done_callback(_done)

    async def _heartbeat(
        self, claim: Claim, execution: asyncio.Task
    ) -> None:
        while True:
            await asyncio.sleep(settings.heartbeat_seconds)
            try:
                cancelled = store.heartbeat(claim.job.job_id, claim.token)
                if cancelled:
                    logger.warning("任务 %s 收到取消请求", claim.job.job_id)
                    try:
                        store.set_cancelled(
                            claim.job.job_id, lease_token=claim.token
                        )
                    except LeaseLost:
                        pass
                    execution.cancel()
                    return
            except LeaseLost:
                logger.warning(
                    "任务 %s 租约丢失，取消执行", claim.job.job_id
                )
                execution.cancel()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # SQLite 短暂锁冲突等：继续重试，租约真正丢失时上面的
                # heartbeat 会返回 0 行并触发取消。
                logger.exception("任务 %s 心跳失败", claim.job.job_id)

    async def _execute(self, claim: Claim) -> None:
        job_id = claim.job.job_id
        execution = asyncio.current_task()
        assert execution is not None
        WORKER_STATE["active_jobs"] = int(WORKER_STATE["active_jobs"]) + 1
        heartbeat = asyncio.create_task(
            self._heartbeat(claim, execution),
            name=f"contractguard-heartbeat-{job_id}",
        )
        try:
            if claim.kind == "resume":
                await resume_review(
                    job_id, lease_token=claim.token, job=claim.job
                )
            else:
                source_path = (
                    Path(claim.job.source_path)
                    if claim.job.source_path
                    else None
                )
                await run_review(
                    job_id,
                    source_path=source_path,
                    cleanup=claim.job.cleanup_source,
                    lease_token=claim.token,
                    job=claim.job,
                )
        except asyncio.CancelledError:
            logger.warning("任务 %s 执行被取消", job_id)
            raise
        except LeaseLost:
            logger.warning("任务 %s 租约丢失，worker 放弃执行", job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("worker 执行任务失败 %s：%s", job_id, exc)
            try:
                store.set_failed(
                    job_id,
                    f"worker 执行失败：{exc}",
                    lease_token=claim.token,
                )
            except LeaseLost:
                pass
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            WORKER_STATE["active_jobs"] = max(
                0, int(WORKER_STATE["active_jobs"]) - 1
            )
            try:
                store.release(job_id, claim.token)
            except Exception:  # noqa: BLE001
                logger.exception("释放任务租约失败 %s", job_id)

    async def _reaper(self) -> None:
        while not self._stop.is_set():
            try:
                store.reap_expired_leases()
            except Exception:  # noqa: BLE001
                logger.exception("回收过期租约失败")
            await self._wait(10)
