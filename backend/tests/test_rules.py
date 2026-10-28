from pathlib import Path

from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import parse_contract
from app.parsing.facts import extract_heuristic_facts
from app.rules.engine import (
    RuleContext,
    evaluate_condition,
    evaluate_rules,
    load_playbook,
)
from app.schemas import ContractFacts

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "data" / "samples"


def _sample_rules():
    parsed = parse_contract(SAMPLES / "sample_01_数控设备采购合同.docx")
    clauses = split_clauses(parsed)
    facts = extract_heuristic_facts(parsed.text, clauses)
    rules = load_playbook(BACKEND / "data" / "playbook.yaml")
    return rules, clauses, facts, parsed.text


def test_rule_hits_on_sample_01():
    rules, clauses, facts, text = _sample_rules()
    result = evaluate_rules(rules, clauses, facts, text)
    ids = {finding.rule_id for finding in result.findings}
    assert {
        "PAY-001",
        "ACC-002",
        "ACC-003",
        "LIA-001",
        "LIA-002",
        "DIS-001",
        "COM-001",
    }.issubset(ids)
    assert "LIA-004" not in ids
    assert "PAY-002" not in ids
    assert "DEL-001" not in ids
    assert all(finding.evidence_quote or finding.rule_id for finding in result.findings)


def test_missing_fact_is_unknown_not_false():
    rules, clauses, facts, text = _sample_rules()
    ctx = RuleContext(clauses=clauses, facts=facts, full_text=text)
    condition = {"type": "fact", "field": "payment.retention_ratio", "op": ">", "value": 0}
    assert evaluate_condition(condition, ctx).matched is None
    condition = {"type": "fact", "field": "contract_value", "op": "is_null"}
    assert evaluate_condition(condition, ctx).matched is False
    condition = {
        "type": "all",
        "conditions": [
            {"type": "fact", "field": "payment.retention_ratio", "op": ">", "value": 0},
            {"type": "fact", "field": "payment.prepayment_ratio", "op": ">=", "value": 0.3},
        ],
    }
    assert evaluate_condition(condition, ctx).matched is None


def test_empty_facts_skip_unknown_but_keep_absence_rules():
    rules, _clauses, _facts, _text = _sample_rules()
    from app.schemas import Clause, ClauseType

    only_subject = "第一条 合同标的\n采购设备一批。"
    clauses = [
        Clause(
            id="cl_001",
            index=0,
            article="第一条",
            text=only_subject,
            start=0,
            end=len(only_subject),
            clause_type=ClauseType.SUBJECT,
        )
    ]
    result = evaluate_rules(rules, clauses, ContractFacts(), only_subject)
    ids = {finding.rule_id for finding in result.findings}
    # 缺失条款类规则不依赖抽取字段，应当仍然工作
    assert "ACC-001" in ids
    assert "DIS-003" in ids
    # 依赖具体事实的规则不应误报
    assert "PAY-001" not in ids
    assert "COM-001" not in ids
