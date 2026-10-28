"""端到端冒烟脚本（不依赖 API Key）。

在 backend 目录运行：
    PYTHONUTF8=1 uv run python scripts/smoke_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["LLM_MODE"] = "mock"
os.environ["SYNC_JOBS"] = "1"
os.environ["HITL_ENABLED"] = "0"
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.jobs import store  # noqa: E402

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


def main() -> None:
    store.reset()  # 仅用于本地冒烟
    with TestClient(app) as client:
        health = client.get("/api/health")
        print("health", health.status_code, health.json())

        response = client.post(
            "/api/reviews",
            files={"file": ("smoke_contract.txt", CONTRACT.encode("utf-8"), "text/plain")},
        )
        print("upload", response.status_code, response.json())
        job_id = response.json()["job_id"]

        job_response = client.get(f"/api/reviews/{job_id}")
        job = job_response.json()
        print("job", job["status"], "findings", len(job["result"]["findings"]))
        for finding in job["result"]["findings"]:
            print(
                " ",
                finding["id"],
                finding["severity"],
                finding["title"],
                finding["clause_article"],
                finding["status"],
            )

        blocked = client.get(f"/api/reviews/{job_id}/report?format=md")
        print("export before review", blocked.status_code, blocked.json().get("detail"))

        for finding in job["result"]["findings"]:
            if finding["severity"] == "high":
                decision = client.post(
                    f"/api/reviews/{job_id}/findings/{finding['id']}/decision",
                    json={"decision": "accepted", "note": "冒烟测试采纳"},
                )
                assert decision.status_code == 200, decision.text

        md = client.get(f"/api/reviews/{job_id}/report?format=md")
        print("markdown", md.status_code, len(md.content), md.headers.get("content-type"))
        Path("data/uploads/smoke_report.md").write_bytes(md.content)

        docx = client.get(f"/api/reviews/{job_id}/report?format=docx")
        print("docx", docx.status_code, len(docx.content), docx.headers.get("content-type"))
        Path("data/uploads/smoke_report.docx").write_bytes(docx.content)

        data = client.get(f"/api/reviews/{job_id}/report?format=json")
        print("json", data.status_code, len(data.content))

        revised = client.get(
            f"/api/reviews/{job_id}/revised-contract?format=json"
        )
        print(
            "revised json",
            revised.status_code,
            len(revised.content),
            revised.json().get("annotated_count"),
        )
        assert revised.status_code == 200, revised.text
        assert "【修订建议】" in revised.json()["text"]

        revised_docx = client.get(
            f"/api/reviews/{job_id}/revised-contract?format=docx"
        )
        print(
            "revised docx",
            revised_docx.status_code,
            len(revised_docx.content),
            revised_docx.headers.get("content-type"),
        )
        assert revised_docx.status_code == 200, revised_docx.text
        assert revised_docx.content[:2] == b"PK"
        Path("data/uploads/smoke_revised.docx").write_bytes(revised_docx.content)


if __name__ == "__main__":
    main()
