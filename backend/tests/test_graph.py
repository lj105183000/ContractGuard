from app.graph.build import build_graph
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
