from eval.run_ablation import build_ablation, variant_metrics


def _payload(precision, recall, f1, tp, fp, fn, high_fp=0):
    return {
        "overall": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "evidence_valid_rate": 1.0,
            "citation_valid_rate": 1.0,
            "quality": {
                "confirmed_precision": 1.0,
                "advisory_precision": 0.2,
                "unsupported_rate": 0.1,
                "high_risk_fp": high_fp,
                "high_risk_fn": 0,
            },
        }
    }


def test_ablation_deltas_and_retrieval_rows():
    rules = _payload(1.0, 0.94, 0.97, 48, 0, 3)
    full = _payload(0.56, 0.98, 0.71, 50, 40, 1, high_fp=18)
    no_rag = _payload(0.50, 0.96, 0.66, 49, 49, 2, high_fp=22)
    retrieval = {
        "configs": [
            {
                "name": "bm25",
                "retrieval_mode": "bm25_only",
                "rerank_mode": "off",
                "overall": {"hit@10": 0.8, "recall@10": 0.5, "mrr@10": 0.5, "ndcg@10": 0.4},
            }
        ]
    }
    payload = build_ablation(rules, full, no_rag, retrieval)
    assert payload["deltas"]["full_rag_minus_rules_only"]["precision"] == -0.44
    assert payload["deltas"]["full_no_rag_minus_full_rag"]["f1"] == -0.05
    assert payload["retrieval"][0]["name"] == "bm25"
    assert variant_metrics(None) is None
