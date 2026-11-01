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
    kappa_scope: str = "union_scope_optimistic"
    universe_size: int = 0
    jaccard: float | None = None
    positive_agreement: float | None = None
    precision_a: float | None = None
    recall_a: float | None = None
    precision_b: float | None = None
    recall_b: float | None = None

    def model_dump(self) -> dict:
        return {
            "total_items": self.total_items,
            "both": self.both,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "cohen_kappa": self.cohen_kappa,
            "kappa_scope": self.kappa_scope,
            "universe_size": self.universe_size,
            "positive_rate": self.positive_rate,
            "jaccard": self.jaccard,
            "positive_agreement": self.positive_agreement,
            "precision_a": self.precision_a,
            "recall_a": self.recall_a,
            "precision_b": self.precision_b,
            "recall_b": self.recall_b,
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


def cohen_kappa_on_universe(
    a_keys: set[tuple], b_keys: set[tuple], universe: set[tuple]
) -> float | None:
    """Cohen's kappa on a fixed candidate universe (includes true negatives)."""
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


def _positive_metrics(keys_a: set[tuple], keys_b: set[tuple]) -> dict:
    both = len(keys_a & keys_b)
    union = len(keys_a | keys_b)
    return {
        "jaccard": round(both / union, 4) if union else None,
        "positive_agreement": (
            round(2 * both / (len(keys_a) + len(keys_b)), 4)
            if keys_a or keys_b
            else None
        ),
        "precision_a": round(both / len(keys_a), 4) if keys_a else None,
        "recall_a": round(both / len(keys_b), 4) if keys_b else None,
        "precision_b": round(both / len(keys_b), 4) if keys_b else None,
        "recall_b": round(both / len(keys_a), 4) if keys_a else None,
    }


def compare_labels(
    labels_a: list[EvalLabel],
    labels_b: list[EvalLabel],
    universe_keys: set[tuple] | None = None,
) -> Agreement:
    """Compare two annotators.

    Without ``universe_keys`` the kappa is computed only over the union of
    positives. That is optimistic because neither annotator can miss a risk
    that both failed to notice. Pass a fixed universe (all candidate
    clause/category/rule keys) for a reportable kappa.
    """
    keys_a = {label_key(label) for label in labels_a}
    keys_b = {label_key(label) for label in labels_b}
    both = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    union = keys_a | keys_b
    disagreements = [
        {"key": list(key), "side": "a_only"} for key in sorted(only_a)
    ] + [{"key": list(key), "side": "b_only"} for key in sorted(only_b)]
    positive = _positive_metrics(keys_a, keys_b)
    if universe_keys is None:
        universe = set(union)
        kappa = cohen_kappa(keys_a, keys_b)
        kappa_scope = "union_scope_optimistic"
    else:
        # A provided universe may omit a positive key by mistake; include all
        # observed positives so no label is silently dropped, and let the
        # reported universe_size show the effective denominator.
        universe = set(universe_keys) | union
        kappa = cohen_kappa_on_universe(keys_a, keys_b, universe)
        kappa_scope = "fixed_universe"
    return Agreement(
        total_items=len(universe),
        both=len(both),
        only_a=len(only_a),
        only_b=len(only_b),
        cohen_kappa=kappa,
        positive_rate=round(len(both) / len(union), 4) if union else 0.0,
        disagreements=disagreements,
        kappa_scope=kappa_scope,
        universe_size=len(universe),
        **positive,
    )


def label_counts(labels: list[EvalLabel]) -> dict[str, int]:
    return dict(Counter(label.annotator for label in labels))
