"""Inter-annotator agreement helpers for contract risk labels.

Labels are compared on an anchor key rather than on the annotator's own IDs:
(clause_id or clause_article, category, rule_id). This lets two annotators use
different label IDs while still measuring whether they flagged the same risk.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.schemas import EvalLabel


def label_key(label: EvalLabel) -> tuple[str, str, str]:
    return (
        label.clause_id or label.clause_article or "",
        label.category.value,
        label.rule_id or "",
    )


@dataclass
class Agreement:
    total_items: int
    both: int
    only_a: int
    only_b: int
    cohen_kappa: float | None
    positive_rate: float
    disagreements: list[dict]

    def model_dump(self) -> dict:
        return {
            "total_items": self.total_items,
            "both": self.both,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "cohen_kappa": self.cohen_kappa,
            "positive_rate": self.positive_rate,
            "disagreements": self.disagreements,
        }


def cohen_kappa(a_keys: set[tuple], b_keys: set[tuple]) -> float | None:
    universe = a_keys | b_keys
    total = len(universe)
    if total == 0:
        return None
    both = len(a_keys & b_keys)
    a_only = len(a_keys - b_keys)
    b_only = len(b_keys - a_keys)
    neither = total - both - a_only - b_only
    observed = (both + neither) / total
    a_positive = len(a_keys) / total
    b_positive = len(b_keys) / total
    expected = a_positive * b_positive + (1 - a_positive) * (1 - b_positive)
    if expected == 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def compare_labels(
    labels_a: list[EvalLabel], labels_b: list[EvalLabel]
) -> Agreement:
    keys_a = {label_key(label) for label in labels_a}
    keys_b = {label_key(label) for label in labels_b}
    both = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    disagreements = [
        {"key": list(key), "side": "a_only"} for key in sorted(only_a)
    ] + [{"key": list(key), "side": "b_only"} for key in sorted(only_b)]
    return Agreement(
        total_items=len(keys_a | keys_b),
        both=len(both),
        only_a=len(only_a),
        only_b=len(only_b),
        cohen_kappa=cohen_kappa(keys_a, keys_b),
        positive_rate=round(len(both) / len(keys_a | keys_b), 4)
        if keys_a | keys_b
        else 0.0,
        disagreements=disagreements,
    )


def label_counts(labels: list[EvalLabel]) -> dict[str, int]:
    return dict(Counter(label.annotator for label in labels))
