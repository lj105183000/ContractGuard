"""对已启动的 ContractGuard 服务做端到端冒烟（无需 API Key）。

用于 Docker/部署环境，不启动进程内应用：
    uv run python scripts/smoke_http.py --base-url http://127.0.0.1:8000

脚本会等待服务就绪，上传一份内置合同，等待审查完成（或人工复核暂停后
自动采纳已核实高风险），最后导出 Markdown 报告。仅使用运行依赖 httpx。
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

CONTRACT = """设备采购合同

甲方：星海智能装备有限公司
乙方：远航机电有限公司

第一条 合同标的
甲方向乙方采购数控设备一套，具体规格见附件一。

第二条 合同价款
合同总价为人民币100万元（含税）。

第三条 付款方式
合同生效后支付预付款50%，验收合格后30日内支付尾款。

第四条 交付
乙方应于合同生效后30日内交付至甲方仓库。

第五条 验收
甲方应在收货后5日内完成验收，逾期未提出异议视为验收合格。

第六条 违约责任
乙方逾期交付的，按日万分之三支付违约金。

第七条 争议解决
因本合同发生争议，由乙方所在地人民法院管辖。
"""

TERMINAL = {"completed", "completed_with_warnings", "failed", "cancelled"}


def _wait_for_health(client: httpx.Client, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                payload = response.json()
                print(f"health: {payload.get('status')}")
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SystemExit(f"服务在 {timeout_s:.0f}s 内未就绪：{last_error}")


def _wait_for_job(
    client: httpx.Client,
    job_id: str,
    timeout_s: float,
    stop_on_awaiting_review: bool = True,
) -> dict:
    deadline = time.monotonic() + timeout_s
    job: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/reviews/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in TERMINAL:
            return job
        if stop_on_awaiting_review and job["status"] == "awaiting_review":
            return job
        time.sleep(1)
    status = job["status"] if job else "unknown"
    raise SystemExit(f"任务 {job_id} 在 {timeout_s:.0f}s 内未结束（status={status}）")


def _accept_confirmed_high(client: httpx.Client, job: dict) -> int:
    findings = (job.get("result") or {}).get("findings") or []
    accepted = 0
    for finding in findings:
        if finding.get("severity") != "high":
            continue
        if finding.get("status") != "pending":
            continue
        response = client.post(
            f"/api/reviews/{job['job_id']}/findings/{finding['id']}/decision",
            json={"decision": "accepted", "note": "HTTP 冒烟测试采纳"},
        )
        if response.status_code not in (200, 409):
            raise SystemExit(
                f"复核失败 {finding['id']}: HTTP {response.status_code} {response.text}"
            )
        accepted += 1
    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="服务地址，默认 http://127.0.0.1:8000",
    )
    parser.add_argument("--ready-timeout-s", type=float, default=90)
    parser.add_argument("--job-timeout-s", type=float, default=180)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        _wait_for_health(client, args.ready_timeout_s)

        response = client.post(
            "/api/reviews",
            files={
                "file": (
                    "smoke_contract.txt",
                    CONTRACT.encode("utf-8"),
                    "text/plain",
                )
            },
        )
        response.raise_for_status()
        job_id = response.json()["job_id"]
        print(f"job: {job_id}")

        job = _wait_for_job(client, job_id, args.job_timeout_s)
        print(f"status: {job['status']}")
        if job["status"] == "awaiting_review":
            accepted = _accept_confirmed_high(client, job)
            print(f"review: accepted {accepted} high finding(s)")
            job = _wait_for_job(
                client,
                job_id,
                args.job_timeout_s,
                stop_on_awaiting_review=False,
            )
            print(f"status: {job['status']}")
        if job["status"] not in {"completed", "completed_with_warnings"}:
            detail = job.get("error") or job.get("summary") or ""
            raise SystemExit(f"任务未成功完成：{job['status']} {detail}")

        findings = (job.get("result") or {}).get("findings") or []
        if not findings:
            raise SystemExit("审查完成但没有返回任何风险项")
        print(f"findings: {len(findings)}")

        report = client.get(f"/api/reviews/{job_id}/report?format=md")
        if report.status_code != 200 or not report.content:
            raise SystemExit(
                f"报告导出失败：HTTP {report.status_code} {report.text[:200]}"
            )
        print(f"report: {len(report.content)} bytes")

    print("HTTP smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
