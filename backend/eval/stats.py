"""Small-sample evaluation statistics.

Bootstrap intervals are computed by resampling **contracts** (not individual
findings) so that repeated risks inside one contract are not treated as
independent evidence. Retrieval intervals resample queries.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Any


def prf_counts(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; values need not be sorted."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _interval(
    values: Sequence[float],
    point: float,
    *,
    iterations: int,
    seed: int,
    confidence: float,
) -> dict[str, Any]:
    alpha = (1.0 - confidence) / 2.0
    return {
        "point": round(point, 4),
        "low": round(percentile(values, alpha), 4),
        "high": round(percentile(values, 1.0 - alpha), 4),
        "iterations": iterations,
        "seed": seed,
        "confidence": confidence,
    }


def bootstrap_prf(
    rows: Sequence[Mapping[str, int]],
    *,
    iterations: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, dict[str, Any]]:
    """Bootstrap precision/recall/F1 by resampling contracts with replacement."""
    clean = [
        {
            "tp": int(row.get("tp", 0)),
            "fp": int(row.get("fp", 0)),
            "fn": int(row.get("fn", 0)),
        }
        for row in rows
    ]
    if not clean:
        empty = {"point": 0.0, "low": 0.0, "high": 0.0, "iterations": iterations,
                 "seed": seed, "confidence": confidence}
        return {metric: dict(empty) for metric in ("precision", "recall", "f1")}

    point = prf_counts(
        sum(row["tp"] for row in clean),
        sum(row["fp"] for row in clean),
        sum(row["fn"] for row in clean),
    )
    rng = random.Random(seed)
    samples = {metric: [] for metric in point}
    for _ in range(iterations):
        draw = [clean[rng.randrange(len(clean))] for _ in clean]
        value = prf_counts(
            sum(row["tp"] for row in draw),
            sum(row["fp"] for row in draw),
            sum(row["fn"] for row in draw),
        )
        for metric in samples:
            samples[metric].append(value[metric])
    return {
        metric: _interval(
            samples[metric],
            point[metric],
            iterations=iterations,
            seed=seed,
            confidence=confidence,
        )
        for metric in point
    }


def bootstrap_metric(
    values: Sequence[float],
    *,
    iterations: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Bootstrap a per-query metric (Hit/Recall/MRR/nDCG)."""
    clean = [float(value) for value in values]
    if not clean:
        return {"point": 0.0, "low": 0.0, "high": 0.0, "iterations": iterations,
                "seed": seed, "confidence": confidence}
    point = sum(clean) / len(clean)
    rng = random.Random(seed)
    draws = [
        sum(clean[rng.randrange(len(clean))] for _ in clean) / len(clean)
        for _ in range(iterations)
    ]
    return _interval(
        draws,
        point,
        iterations=iterations,
        seed=seed,
        confidence=confidence,
    )


def ndcg_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int = 10) -> float:
    """Binary-relevance nDCG@k."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    dcg = 0.0
    for index, doc_id in enumerate(ranked[:k]):
        if doc_id in relevant_set:
            dcg += 1.0 / math.log2(index + 2)
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg else 0.0
