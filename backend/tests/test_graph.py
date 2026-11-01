from app.graph.build import build_graph
from app.graph.nodes import agents as agent_nodes
from app.llm.trace import TraceCollector


async def test_graph_mock_end_to_end(contract_text, tmp_path):
    path = tmp_path / "contract.txt"
    path.write_text(contract_text, encoding="utf-8")
    trace = TraceCollector()
    state = {
        "job_id": "test-graph",
        "filename": "contract.txt",
        "source_path": str(path),
        "trace": trace,
    }
    result = await build_graph().ainvoke(state, config={"recursion_limit": 30})
    report = result["report"]
    assert report is not None
    assert report.mode == "mock"
    assert report.summary.total >= 5
    ids = {finding.rule_id for finding in report.findings}
    assert "PAY-001" in ids
    assert "ACC-002" in ids
    assert "DIS-001" in ids
    assert "PAY-002" not in ids
    assert report.summary.high >= 3
    assert report.summary.evidence_valid_rate == 1.0
    assert report.legal_basis
    assert all(finding.suggested_text for finding in report.findings)
    assert all(finding.id for finding in report.findings)
    assert all(
        finding.suggested_text_source == "template" for finding in report.findings
    )


async def test_graph_can_run_with_rag_disabled_for_ablation(
    contract_text, tmp_path
):
    path = tmp_path / "contract.txt"
    path.write_text(contract_text, encoding="utf-8")
    state = {
        "job_id": "test-no-rag",
        "filename": "contract.txt",
        "source_path": str(path),
        "disable_rag": True,
        "trace": TraceCollector(),
    }
    result = await build_graph().ainvoke(state, config={"recursion_limit": 30})
    report = result["report"]
    assert report is not None
    assert report.summary.total >= 1
    assert any(finding.rule_id for finding in report.findings)


async def test_agent_failure_is_visible_in_warnings(
    contract_text, tmp_path, monkeypatch
):
    path = tmp_path / "contract.txt"
    path.write_text(contract_text, encoding="utf-8")
    original = agent_nodes.get_llm

    def failing_get_llm(trace, task, route=None):
        if task == "agent_commercial":
            raise RuntimeError("simulated provider failure")
        return original(trace, task, route)

    monkeypatch.setattr(agent_nodes, "get_llm", failing_get_llm)
    state = {
        "job_id": "test-agent-failure",
        "filename": "contract.txt",
        "source_path": str(path),
        "trace": TraceCollector(),
    }
    result = await build_graph().ainvoke(state, config={"recursion_limit": 30})
    report = result["report"]
    assert report is not None
    assert any("商务条款审查员 调用失败" in item for item in report.warnings)
    # 规则分支仍然覆盖全文，结果可导出（除非有其他阻断）。
    assert report.summary.total >= 1
    assert any(finding.rule_id for finding in report.findings)
