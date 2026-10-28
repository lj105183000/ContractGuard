"""校验法条、Playbook 和示例合同的一致性。

用法（backend 目录）：
    uv run python scripts/validate_kb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.corpus import load_kb, sample_path  # noqa: E402


def main() -> None:
    kb = load_kb()
    errors: list[str] = []
    legal_ids = {article.id for article in kb.legal_articles}
    rule_ids = {rule.id for rule in kb.rules}
    if len(legal_ids) != len(kb.legal_articles):
        errors.append("法条 ID 重复")
    if len(rule_ids) != len(kb.rules):
        errors.append("规则 ID 重复")
    for article in kb.legal_articles:
        if not article.text.strip():
            errors.append(f"法条 {article.id} 正文为空")
        if not article.source_url.startswith("http"):
            errors.append(f"法条 {article.id} 缺少来源 URL")
    for rule in kb.rules:
        missing = [law_id for law_id in rule.legal_basis if law_id not in legal_ids]
        if missing:
            errors.append(f"规则 {rule.id} 引用了不存在的法条：{missing}")
        if not rule.recommendation.strip():
            errors.append(f"规则 {rule.id} 缺少推荐立场")
    for sample in kb.samples:
        path = sample_path(sample)
        if not path.exists():
            errors.append(f"示例合同缺失：{path}")

    print(
        f"法条 {len(kb.legal_articles)} 条；规则 {len(kb.rules)} 条；"
        f"示例合同 {len(kb.samples)} 份；文档 {len(kb.documents)} 条"
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("知识库校验通过。")


if __name__ == "__main__":
    main()
