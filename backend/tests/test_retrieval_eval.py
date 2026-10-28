from eval.run_retrieval_eval import (
    aggregate_queries,
    average_metrics,
    compute_metrics,
)


def test_hit_recall_mrr():
    metrics = compute_metrics(["CC-1", "CC-2", "CC-3"], ["CC-2", "CC-9"], 2)
    assert metrics == {
        "hit": 1.0,
        "recall": 0.5,
        "mrr": 0.5,
        "ndcg": 0.3869,
    }


def test_no_hit():
    metrics = compute_metrics(["CC-1", "CC-3"], ["CC-2"], 2)
    assert metrics == {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}


def test_average_metrics():
    averaged = average_metrics(
        [
            {"hit": 1.0, "recall": 1.0, "mrr": 1.0, "ndcg": 1.0},
            {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0},
        ]
    )
    assert averaged == {"hit": 0.5, "recall": 0.5, "mrr": 0.5, "ndcg": 0.5}


def test_aggregate_queries_adds_ndcg_and_bootstrap_ci():
    per_query = [
        {
            "source": "synthetic_clause",
            "metrics@5": {"hit": 1.0, "recall": 1.0, "mrr": 1.0, "ndcg": 1.0},
            "metrics@10": {"hit": 1.0, "recall": 1.0, "mrr": 1.0, "ndcg": 1.0},
        },
        {
            "source": "synthetic_clause",
            "metrics@5": {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0},
            "metrics@10": {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0},
        },
    ]
    overall, ci, by_source = aggregate_queries(per_query)
    assert overall["ndcg@10"] == 0.5
    assert ci["ndcg"]["point"] == 0.5
    assert ci["recall"]["low"] <= ci["recall"]["point"] <= ci["recall"]["high"]
    assert by_source["synthetic_clause"]["ndcg@10"] == 0.5
