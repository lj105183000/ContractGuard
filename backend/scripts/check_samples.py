"""检查 8 份示例合同的解析、切分、事实与规则命中情况。

用法（backend 目录）：
    PYTHONUTF8=1 uv run python scripts/check_samples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import parse_contract
from app.parsing.facts import extract_heuristic_facts
from app.rag.corpus import load_kb
from app.rules.engine import evaluate_rules

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "data" / "samples"
LABELS = BACKEND / "eval" / "labels"


def main() -> None:
    kb = load_kb()
    samples = yaml.safe_load((SAMPLES / "samples.yaml").read_text(encoding="utf-8"))
    for sample in samples:
        path = SAMPLES / sample["filename"]
        parsed = parse_contract(path, sample["filename"])
        clauses = split_clauses(parsed)
        facts = extract_heuristic_facts(parsed.text, clauses)
        result = evaluate_rules(kb.rules, clauses, facts, parsed.text)
        labels = json.loads((LABELS / f"{sample['id']}.json").read_text(encoding="utf-8"))
        predicted = {(f.rule_id, f.clause_article or "合同级") for f in result.findings}
        expected = {(label["rule_id"], label["clause_article"]) for label in labels if label.get("rule_id")}
        hit = predicted & expected
        print(f"\n== {sample['id']} {sample['format']} pages={parsed.page_count} chars={len(parsed.text)}")
        print(f"clauses={len(clauses)} rules={len(result.findings)} labels={len(labels)} rule_hits={len(hit)}/{len(expected)}")
        for clause in clauses:
            print(f"  {clause.article:6s} {clause.clause_type.value:14s} {clause.text[:36]!r}")
        print("  predicted:", sorted(predicted))
        print("  expected :", sorted(expected))
        print("  missing  :", sorted(expected - predicted))
        print("  extra    :", sorted(predicted - expected))


if __name__ == "__main__":
    main()
