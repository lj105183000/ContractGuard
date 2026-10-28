"""用本地假 OpenAI 服务验证 live 管线，不需要真实 API Key。

它会启动一个只监听 127.0.0.1 的 HTTP 服务，模拟：
- POST /v1/chat/completions（JSON 模式）
- POST /v1/embeddings（8 维测试向量）

然后通过 FastAPI TestClient 走一遍上传 → LangGraph → 报告，验证：
- AsyncOpenAI 客户端调用格式
- JSON 输出 + Pydantic 校验
- 向量索引 + 混合检索
- 3 个 Agent / 修改建议 / 门禁 / JobStore

用法（backend 目录）：
    PYTHONUTF8=1 uv run python scripts/test_live_offline.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
甲方应在收货后5日内完成验收，逾期未提出异议的，视为验收合格。

第六条 违约责任
乙方逾期交付的，按日万分之三支付违约金。

第七条 争议解决
因本合同发生争议，由乙方所在地人民法院管辖。
"""


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        body = json.dumps(
            {"error": {"message": message, "type": "invalid_request_error"}}
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path.endswith("/embeddings"):
            request = self._read_json()
            inputs = request.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            data = []
            for index, text in enumerate(inputs):
                # 确定性、可归一化的 8 维向量
                seed = sum(ord(char) for char in str(text))
                data.append(
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [
                            ((seed + index * 17 + dim * 11) % 97) / 97.0
                            for dim in range(8)
                        ],
                    }
                )
            self._send(
                {
                    "object": "list",
                    "data": data,
                    "model": request.get("model", "text-embedding-v3"),
                    "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
                }
            )
            return

        if self.path.endswith("/chat/completions"):
            request = self._read_json()
            if request.get("model") == "broken-model":
                self._send_error(401, "invalid api key for broken-model")
                return
            content = "\n".join(
                message.get("content", "") for message in request.get("messages", [])
            )
            if "请完成两件事" in content:
                payload = {
                    "clauses": [],
                    "facts": {
                        "payment": {
                            "prepayment_ratio": 0.5,
                            "payment_days": 30,
                            "payment_after_acceptance": True,
                        },
                        "acceptance": {"present": True, "period_days": 5},
                        "dispute": {"clause_present": True},
                    },
                }
            elif "你的角色：" in content:
                clause_ids = re.findall(r'"clause_id": "(cl_[0-9]+)"', content)
                clause_id = clause_ids[0] if clause_ids else "cl_001"
                payload = {
                    "findings": [
                        {
                            "category": "payment",
                            "rule_hint": None,
                            "severity": "high",
                            "clause_id": clause_id,
                            "title": "测试模型风险",
                            "analysis": "这是本地假模型返回的测试风险，用于验证 live 管线。",
                            "evidence_quote": "合同",
                            "legal_basis_ids": ["CC-577"],
                            "confidence": 0.9,
                        }
                    ]
                }
            elif "请为下面每条风险生成修改建议" in content:
                finding_ids = re.findall(r'"finding_id": "([^"]+)"', content)
                payload = {
                    "redlines": [
                        {
                            "finding_id": finding_id,
                            "suggested_text": "测试修改建议：请以双方最终确认的条款为准。",
                            "fallback": "测试退让方案。",
                            "negotiation_note": "本地假模型，仅用于管线验证。",
                        }
                        for finding_id in finding_ids
                    ]
                }
            else:
                payload = {"findings": []}
            self._send(
                {
                    "id": "chatcmpl-local-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": request.get("model", "qwen-plus"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(payload, ensure_ascii=False),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                }
            )
            return

        self.send_error(404)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    fake_cache_dir = tempfile.mkdtemp(prefix="contractguard_fake_cache_")
    fake_profiles_dir = tempfile.mkdtemp(prefix="contractguard_profiles_")
    profiles_path = Path(fake_profiles_dir) / "llm_profiles.yaml"
    profiles_path.write_text(
        f"""
version: 1
default_route: local-test
providers:
  broken:
    label: Broken primary
    base_url: http://127.0.0.1:{port}/v1
    model: broken-model
    api_key_env: DASHSCOPE_API_KEY
  fake:
    label: Fake backup
    base_url: http://127.0.0.1:{port}/v1
    model: fake-model
    api_key_env: DASHSCOPE_API_KEY
routes:
  local-test:
    label: Local Test
    default: [broken, fake]
""",
        encoding="utf-8",
    )
    os.environ["CONTRACTGUARD_LLM_PROFILES"] = str(profiles_path)
    os.environ["LLM_MODE"] = "live"
    os.environ["LLM_TIMEOUT_S"] = "5"
    os.environ["LLM_DEADLINE_S"] = "30"
    os.environ["DASHSCOPE_API_KEY"] = "local-test-key"
    os.environ["DASHSCOPE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["EMBEDDING_DIM"] = "8"
    os.environ["SYNC_JOBS"] = "1"
    os.environ["HITL_ENABLED"] = "0"
    os.environ["WORKER_ENABLED"] = "0"
    os.environ["INDEX_CACHE_DIR"] = fake_cache_dir

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/reviews",
                files={
                    "file": (
                        "live_offline_contract.txt",
                        CONTRACT.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            job = client.get(f"/api/reviews/{job_id}").json()
            print("status:", job["status"])
            assert job["status"] in {"completed", "completed_with_warnings"}, job
            result = job["result"]
            print("mode:", result["mode"])
            print("usage:", json.dumps(result["usage"], ensure_ascii=False)[:500])
            print("findings:", len(result["findings"]))
            assert result["mode"] == "live"
            assert result["usage"]["llm_calls"] > 0
            assert result["usage"]["embedding_mode"] == "hybrid"
            assert result["usage"]["llm_route"] == "local-test"
            assert result["usage"]["llm_route_label"] == "Local Test"
            assert "fake-model" in result["usage"]["llm_models"]
            traces = result["usage"]["traces"]
            assert any(
                trace["profile"] == "broken" and not trace["success"]
                for trace in traces
            )
            assert any(
                trace["profile"] == "fake" and trace["success"]
                for trace in traces
            )
            assert any(
                finding["source"] in {"llm", "rule+llm"}
                for finding in result["findings"]
            )
            assert any(
                finding["title"] == "测试模型风险" for finding in result["findings"]
            )
            print("multi-LLM fallback 验证通过。")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(fake_cache_dir, ignore_errors=True)
        shutil.rmtree(fake_profiles_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
