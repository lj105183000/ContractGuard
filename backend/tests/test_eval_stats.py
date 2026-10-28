import pytest

from eval.stats import (
    bootstrap_metric,
    bootstrap_prf,
    ndcg_at_k,
    percentile,
    prf_counts,
)


def test_prf_counts_handles_zero_denominators():
    assert prf_counts(0, 0, 0) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert prf_counts(1, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert prf_counts(1, 1, 1)["f1"] == pytest.approx(0.5)


def test_percentile_interpolates_and_handles_empty():
    assert percentile([], 0.5) == 0.0
    assert percentile([4.0], 0.95) == 4.0
    assert percentile([0.0, 10.0], 0.5) == 5.0
    assert percentile([0.0, 10.0], 0.25) == 2.5


def test_bootstrap_prf_is_deterministic_and_contains_point():
    rows = [
        {"tp": 2, "fp": 1, "fn": 0},
        {"tp": 1, "fp": 0, "fn": 1},
    ]
    first = bootstrap_prf(rows, iterations=500, seed=7)
    second = bootstrap_prf(rows, iterations=500, seed=7)
    assert first == second
    assert first["precision"]["low"] <= first["precision"]["point"] <= first["precision"]["high"]
    assert first["recall"]["low"] <= first["recall"]["point"] <= first["recall"]["high"]


def test_bootstrap_prf_single_contract_is_degenerate():
    result = bootstrap_prf([{"tp": 2, "fp": 3, "fn": 2}], iterations=100)
    for metric in ("precision", "recall", "f1"):
        assert result[metric]["low"] == result[metric]["point"]
        assert result[metric]["high"] == result[metric]["point"]


def test_bootstrap_metric_average():
    result = bootstrap_metric([1.0, 0.0, 0.5], iterations=200, seed=1)
    assert result["point"] == pytest.approx(0.5)
    assert result["low"] <= result["point"] <= result["high"]


def test_ndcg_at_k_binary_relevance():
    assert ndcg_at_k(["a", "b"], ["a"], 10) == 1.0
    assert ndcg_at_k(["b", "a"], ["a"], 10) == pytest.approx(0.6309, abs=1e-3)
    assert ndcg_at_k(["x"], [], 10) == 0.0
    assert ndcg_at_k(["x", "y"], ["a", "b"], 10) == 0.0
