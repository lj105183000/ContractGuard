import json
from pathlib import Path

from app.graph.evidence import find_quote_span
from app.llm.client import extract_json
from app.llm.prompts import MAX_EXTRACTION_CLAUSES, build_extraction_prompt
from app.reporting.report import safe_report_filename
from app.schemas import Clause, ContractMeta, ReviewResult, ReviewSummary

BACKEND = Path(__file__).resolve().parents[1]


def test_find_quote_span_exact_and_whitespace_tolerant():
    text = "甲方应在收货后 3 日内完成验收，逾期视为验收合格。"
    assert find_quote_span(text, "3 日内完成验收") is not None
    span = find_quote_span(text, "3日内完成验收")
    assert span is not None
    assert text[span[0] : span[1]].replace(" ", "") == "3日内完成验收"
    assert find_quote_span(text, "不存在的句子") is None


def test_extract_json_from_markdown_fence():
    payload = extract_json('```json\n{"findings": []}\n```')
    assert payload == {"findings": []}
    payload = extract_json('说明文字 {"ok": true} 结尾')
    assert payload == {"ok": True}


def test_safe_report_filename():
    report = ReviewResult(
        contract=ContractMeta(filename="a/b:c.docx", title='设备/采购:"合同"'),
        summary=ReviewSummary(),
        clauses=[],
        findings=[],
    )
    name = safe_report_filename(report, "docx")
    assert name.endswith(".docx")
    assert "/" not in name
    assert ":" not in name
    assert '"' not in name


def test_rules_report_is_valid_json():
    path = BACKEND / "eval" / "results_rules.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "rules"
    assert data["overall"]["tp"] > 0


def test_extraction_prompt_caps_clauses():
    clauses = [
        Clause(
            id=f"cl_{index:03d}",
            index=index,
            article=f"第{index + 1}条",
            text="测试条款",
            start=0,
            end=4,
        )
        for index in range(MAX_EXTRACTION_CLAUSES + 5)
    ]
    prompt = build_extraction_prompt("合同正文", clauses)
    assert f'"clause_id": "cl_{MAX_EXTRACTION_CLAUSES - 1:03d}"' in prompt
    assert f'"clause_id": "cl_{MAX_EXTRACTION_CLAUSES:03d}"' not in prompt
