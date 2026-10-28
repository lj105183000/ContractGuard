from eval.run_eval import matches, score

from app.schemas import (
    Category,
    ConfidenceTier,
    EvalLabel,
    Finding,
    Severity,
    Source,
)


def _finding(finding_id="f1", **overrides):
    data = {
        "id": finding_id,
        "clause_article": "第三条",
        "category": Category.PAYMENT,
        "severity": Severity.HIGH,
        "source": Source.RULE,
        "title": "预付款比例过高",
        "rule_id": "PAY-001",
    }
    data.update(overrides)
    return Finding(**data)


def _label(label_id="L1", **overrides):
    data = {
        "id": label_id,
        "clause_article": "第三条",
        "category": "payment",
        "severity": "high",
        "rule_id": "PAY-001",
    }
    data.update(overrides)
    return EvalLabel(**data)


def test_rule_label_matches_by_rule_and_clause():
    assert matches(_label(), _finding())


def test_rule_label_rejects_wrong_clause():
    assert not matches(_label(), _finding(clause_article="第四条"))


def test_semantic_label_matches_category_clause_and_keyword():
    label = _label(
        label_id="L2",
        clause_article="第六条",
        category="liability",
        rule_id=None,
        keywords=["赔偿上限"],
    )
    finding = _finding(
        clause_article="第六条",
        category=Category.LIABILITY,
        rule_id=None,
        rule_hint=None,
        title="未约定赔偿上限",
    )
    assert matches(label, finding)


def test_score_matches_each_label_once():
    label = _label()
    findings = [_finding("f1"), _finding("f2")]
    data = score([label], findings)
    assert data["tp"] == 1
    assert data["fp"] == 1
    assert data["fn"] == 0


def test_score_breaks_down_source_and_tier():
    label = _label()
    rule_hit = _finding(
        "f1",
        confidence_tier=ConfidenceTier.CONFIRMED,
        legal_basis_ids=["CC-509"],
    )
    llm_fp = _finding(
        "f2",
        source=Source.LLM,
        rule_id=None,
        rule_hint="PAY-002",
        title="模型误报",
        confidence_tier=ConfidenceTier.ADVISORY,
        evidence_quote="原文",
        evidence_start=0,
        evidence_end=2,
        legal_basis_ids=["CC-510"],
    )
    data = score([label], [rule_hit, llm_fp])
    assert data["by_source"]["rule"] == {"tp": 1, "fp": 0, "precision": 1.0}
    assert data["by_source"]["llm"] == {"tp": 0, "fp": 1, "precision": 0.0}
    assert data["by_confidence"]["confirmed"]["tp"] == 1
    assert data["by_confidence"]["advisory"]["fp"] == 1
    assert data["fp_attribution"]["total"] == 1
    assert data["fp_attribution"]["by_source"]["llm"] == 1
    assert data["fp_attribution"]["with_rule_hint"] == 1
    assert data["fp_attribution"]["evidence_located"] == 1


def test_evidence_metric_excludes_rule_without_quote():
    finding = _finding(rule_id="ACC-001", evidence_quote="")
    data = score([], [finding])
    assert data["evidence_checked"] == 0
    assert data["evidence_valid_rate"] is None


def test_quote_anchor_matches_finding_without_article():
    label = _label(
        label_id="L3",
        clause_article="",
        rule_id="IP-001",
        quote="知识产权归甲方所有",
    )
    finding = _finding(
        clause_id="cl_9",
        clause_article="6459",
        rule_id="IP-001",
        clause_text="双方约定知识产权归甲方所有，乙方不得主张。",
    )
    assert matches(label, finding)


def test_clause_id_anchor_matches():
    label = _label(label_id="L4", clause_article="", rule_id="PAY-001", clause_id="cl_3")
    finding = _finding(clause_id="cl_3")
    assert matches(label, finding)


def test_quote_anchor_rejects_wrong_quote():
    label = _label(
        label_id="L5",
        clause_article="",
        rule_id="IP-001",
        quote="不存在的原文",
    )
    finding = _finding(clause_id="cl_9", clause_article="6459", rule_id="IP-001")
    assert not matches(label, finding)
