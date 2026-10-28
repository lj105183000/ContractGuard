import json
from pathlib import Path

import yaml

from app.schemas import ConfidenceTier, FindingStatus, Source
from eval.run_eval import (
    SAMPLES_DIR,
    load_labels,
    rules_run,
    score,
)

BACKEND = Path(__file__).resolve().parents[1]
BADCASES = yaml.safe_load(
    (BACKEND / "eval" / "badcases.yaml").read_text(encoding="utf-8")
)
SAMPLES = yaml.safe_load(
    (SAMPLES_DIR / "samples.yaml").read_text(encoding="utf-8")
)


def test_rules_badcase_counts_and_known_false_negatives():
    expected = BADCASES["rules"]["expected"]
    unmatched_label_ids = set()
    rule_findings = []
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for sample in SAMPLES:
        findings, _duration = rules_run(sample, load_labels(sample["id"]))
        result = score(load_labels(sample["id"]), findings)
        for key in totals:
            totals[key] += result[key]
        unmatched_label_ids.update(
            label["id"] for label in result["unmatched_labels"]
        )
        rule_findings.extend(
            finding for finding in findings if finding.source == Source.RULE
        )

    assert totals == expected
    assert unmatched_label_ids == {
        item["label_id"] for item in BADCASES["rules"]["known_false_negatives"]
    }
    assert rule_findings
    assert all(
        finding.status == FindingStatus.PENDING for finding in rule_findings
    )
    assert all(
        finding.confidence_tier == ConfidenceTier.CONFIRMED
        for finding in rule_findings
    )


def test_full_badcases_are_disclosed_in_saved_snapshot():
    payload = json.loads(
        (BACKEND / "eval" / "results_full.json").read_text(encoding="utf-8")
    )
    unmatched = [
        finding
        for sample in payload["samples"]
        for finding in sample["score"]["unmatched_findings"]
    ]
    titles = {finding["title"] for finding in unmatched}
    known_titles = set(BADCASES["full"]["known_false_positive_titles"])

    assert payload["overall"]["fp"] == len(unmatched)
    assert known_titles <= titles
    assert payload["overall"]["by_confidence"]["confirmed"]["tp"] > 0
    assert payload["overall"]["by_confidence"]["advisory"]["fp"] > 0
