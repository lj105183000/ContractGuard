from eval.run_quality_eval import summarize, validate


def _record(**overrides):
    data = {
        "contract_id": "real_01",
        "finding_id": "f1",
        "reviewer": "legal_a",
        "legal_relevance": 4,
        "evidence_support": 5,
        "suggestion_usability": 3,
        "hallucinated": False,
        "notes": "",
    }
    data.update(overrides)
    return data


def test_quality_validation_and_summary():
    records = [
        _record(),
        _record(
            finding_id="f2",
            legal_relevance=2,
            evidence_support=1,
            suggestion_usability=1,
            hallucinated=True,
        ),
    ]
    assert validate(records) == []
    summary = summarize(records)
    assert summary["reviews"] == 2
    assert summary["unique_findings"] == 2
    assert summary["metrics"]["legal_relevance"]["mean"] == 3.0
    assert summary["hallucination_rate"] == 0.5
    assert summary["suggestion_usable_rate"] == 0.0
    assert summary["suggestion_unusable_rate"] == 0.5
    assert summary["low_legal_relevance_rate"] == 0.5


def test_quality_validation_rejects_bad_scores():
    assert validate([_record(legal_relevance=0)])
    assert validate([_record(evidence_support=6)])
    assert validate([_record(hallucinated="no")])
    assert validate([_record(reviewer="")])
    assert validate([_record(), _record()])
