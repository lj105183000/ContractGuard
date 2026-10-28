from eval.run_cost_report import estimate_cost, summarize


def test_estimate_cost():
    assert estimate_cost(1000, 500, 0.001, 0.002) == 0.002


def test_summarize_usage_latency_and_failures():
    payload = {
        "mode": "full",
        "model": "qwen-plus",
        "generated_at": "2026-01-01T00:00:00+08:00",
        "samples": [
            {
                "sample_id": "s1",
                "duration_s": 10.0,
                "usage": {
                    "llm_calls": 3,
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "traces": [
                        {"model": "qwen-plus", "success": True, "error": None},
                        {
                            "model": "qwen-turbo",
                            "success": False,
                            "error": "Error code: 429",
                        },
                    ],
                },
            },
            {
                "sample_id": "s2",
                "duration_s": 20.0,
                "usage": {
                    "llm_calls": 2,
                    "input_tokens": 500,
                    "output_tokens": 250,
                    "traces": [
                        {"model": "qwen-plus", "success": True, "error": None},
                    ],
                },
            },
        ],
    }
    summary = summarize(payload, 0.001, 0.002)
    assert summary["calls"] == 5
    assert summary["failed_calls"] == 1
    assert summary["rate_limited_calls"] == 1
    assert summary["input_tokens"] == 1500
    assert summary["output_tokens"] == 750
    assert summary["cost_estimate"] == 0.003
    assert summary["latency_s"]["mean"] == 15.0
    assert summary["latency_s"]["p50"] == 15.0
    assert summary["model_counts"] == {"qwen-plus": 2, "qwen-turbo": 1}
