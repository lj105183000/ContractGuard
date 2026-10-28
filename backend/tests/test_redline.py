import pytest

from app.graph.nodes import redline
from app.llm.trace import TraceCollector
from app.schemas import (
    Category,
    Clause,
    ContractMeta,
    Finding,
    RedlineItem,
    RedlineOutput,
    ReviewResult,
    Severity,
    Source,
)
from app.services.summary import build_summary

SUGGESTION = "第一条 标的\n采购设备两套，并包含安装调试服务。"


class _FakeLLM:
    def __init__(self, output=None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error

    async def chat_json(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.output


def _report() -> ReviewResult:
    text = "第一条 标的\n采购设备一套。"
    clause = Clause(
        id="cl_001",
        index=0,
        article="第一条",
        text=text,
        start=0,
        end=len(text),
    )
    finding = Finding(
        id="f1",
        clause_id="cl_001",
        clause_article="第一条",
        category=Category.PRICE,
        severity=Severity.HIGH,
        source=Source.RULE,
        title="标的约定不清",
        recommendation="明确设备型号与数量。",
    )
    return ReviewResult(
        contract=ContractMeta(filename="contract.txt", title="测试合同"),
        summary=build_summary([finding]),
        clauses=[clause],
        findings=[finding],
        full_text=text,
    )


def _state(report: ReviewResult) -> dict:
    return {
        "job_id": "redline-test",
        "report": report,
        "clauses": report.clauses,
        "trace": TraceCollector(),
        "llm_route": None,
    }


async def test_redline_marks_non_empty_llm_suggestion(monkeypatch):
    report = _report()
    output = RedlineOutput(
        redlines=[
            RedlineItem(
                finding_id="f1",
                suggested_text=SUGGESTION,
                fallback="保留安装服务。",
                negotiation_note="优先争取免费安装。",
            )
        ]
    )
    monkeypatch.setattr(
        redline, "get_llm", lambda *_args, **_kwargs: _FakeLLM(output)
    )

    await redline.redline_node(_state(report))

    finding = report.findings[0]
    assert finding.suggested_text == SUGGESTION
    assert finding.suggested_text_source == "llm"


@pytest.mark.parametrize(
    "fake",
    [
        _FakeLLM(RedlineOutput()),
        _FakeLLM(error=RuntimeError("model down")),
    ],
)
async def test_redline_marks_fallback_as_template(monkeypatch, fake):
    report = _report()
    monkeypatch.setattr(redline, "get_llm", lambda *_args, **_kwargs: fake)

    await redline.redline_node(_state(report))

    finding = report.findings[0]
    assert finding.suggested_text
    assert finding.suggested_text_source == "template"


def test_legacy_finding_defaults_to_template_source():
    finding = Finding(
        id="legacy",
        category=Category.PRICE,
        severity=Severity.LOW,
        source=Source.RULE,
        title="旧任务",
    )
    assert finding.suggested_text_source == "template"
