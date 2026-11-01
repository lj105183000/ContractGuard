"""真实合同标注辅助：导出条款清单、校验标签。

用法（backend 目录）：
    uv run python scripts/annotate_real.py dump \
      --pdf "C:\\path\\to\\contract.pdf" \
      --out-dir ../contractguard-private/real_eval/work/real_01

    uv run python scripts/annotate_real.py validate \
      --labels ../contractguard-private/real_eval/labels/real_01.json \
      --parsed ../contractguard-private/real_eval/work/real_01/parsed.json

注意：parsed.json 含合同全文，只能放在 private/.work 目录，不要提交。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.clause_splitter import split_clauses  # noqa: E402
from app.parsing.contract_parser import parse_contract  # noqa: E402
from app.rag.corpus import load_kb  # noqa: E402
from app.schemas import Category, EvalLabel  # noqa: E402


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def dump(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"文件不存在：{pdf}")
        return 1
    parsed = parse_contract(pdf, pdf.name)
    clauses = split_clauses(parsed)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": args.id or pdf.stem,
        "filename": pdf.name,
        "sha256": digest,
        "pages": parsed.page_count,
        "used_ocr": parsed.used_ocr,
        "ocr_pages": parsed.ocr_pages,
        "ocr_failed_pages": parsed.ocr_failed_pages,
        "ocr_confidence": parsed.ocr_confidence,
        "text": parsed.text,
        "clauses": [clause.model_dump(mode="json") for clause in clauses],
    }
    path = out_dir / "parsed.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"写入 {path}；pages={parsed.page_count} clauses={len(clauses)} "
        f"used_ocr={parsed.used_ocr} ocr_pages={parsed.ocr_pages} "
        f"ocr_confidence={parsed.ocr_confidence}"
    )
    print(
        "条款清单（前 20 条）："
    )
    for clause in clauses[:20]:
        preview = clause.text.replace("\n", " ")[:80]
        print(f"  {clause.id}  article={clause.article!r}  page={clause.page}  {preview}")
    return 0


def universe(args: argparse.Namespace) -> int:
    """Generate a fixed candidate universe for IAA kappa.

    Candidate keys are (clause_id, category, "") for semantic risks plus
    (clause_id, rule.category, rule.id) for rule-detectable risks. The universe
    is intentionally generous; it is the negative space for agreement, not a
    claim that every candidate is a real risk.
    """
    parsed = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    clauses = parsed.get("clauses", [])
    kb = load_kb()
    categories = [category.value for category in Category]
    keys = []
    seen = set()
    for clause in clauses:
        clause_id = str(clause.get("id") or "")
        article = str(clause.get("article") or "")
        if not clause_id:
            continue
        for category in categories:
            key = (clause_id, category, "")
            if key not in seen:
                seen.add(key)
                keys.append(
                    {
                        "clause_id": clause_id,
                        "clause_article": article,
                        "category": category,
                        "rule_id": "",
                    }
                )
        for rule in kb.rules:
            key = (clause_id, rule.category.value, rule.id)
            if key not in seen:
                seen.add(key)
                keys.append(
                    {
                        "clause_id": clause_id,
                        "clause_article": article,
                        "category": rule.category.value,
                        "rule_id": rule.id,
                    }
                )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"写入 {out}；候选键 {len(keys)} 条（clauses={len(clauses)}）")
    return 0


def validate(args: argparse.Namespace) -> int:
    parsed = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    labels_raw = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if not isinstance(labels_raw, list):
        print("labels 必须是 JSON 数组")
        return 1
    labels = [EvalLabel.model_validate(item) for item in labels_raw]
    normalized_text = _normalize(parsed.get("text", ""))
    clause_ids = {clause["id"] for clause in parsed.get("clauses", [])}
    seen: set[str] = set()
    errors: list[str] = []
    layer_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    for label in labels:
        if label.id in seen:
            errors.append(f"重复标签 id：{label.id}")
        seen.add(label.id)
        if label.clause_id and label.clause_id not in clause_ids:
            errors.append(f"{label.id}: clause_id 不存在：{label.clause_id}")
        if label.quote:
            if _normalize(label.quote) not in normalized_text:
                errors.append(f"{label.id}: quote 不是解析文本的逐字子串")
            if len(label.quote) > 200:
                errors.append(f"{label.id}: quote 超过 200 字")
        if not label.quote and not label.clause_id and not label.clause_article:
            errors.append(f"{label.id}: 缺少 clause_id/quote/clause_article 锚点")
        layer_counts[label.label_layer or "unspecified"] += 1
        scope_counts[label.scope or "unspecified"] += 1

    print(f"labels={len(labels)}")
    print(f"label_layer={dict(layer_counts)}")
    print(f"scope={dict(scope_counts)}")
    print(f"rule_detectable={sum(1 for label in labels if label.rule_id)}")
    print(f"semantic={sum(1 for label in labels if not label.rule_id)}")
    if errors:
        print("校验失败：")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("校验通过")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dump_parser = sub.add_parser("dump")
    dump_parser.add_argument("--pdf", required=True)
    dump_parser.add_argument("--out-dir", required=True)
    dump_parser.add_argument("--id", default=None)
    dump_parser.set_defaults(func=dump)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--labels", required=True)
    validate_parser.add_argument("--parsed", required=True)
    validate_parser.set_defaults(func=validate)

    universe_parser = sub.add_parser("universe")
    universe_parser.add_argument("--parsed", required=True)
    universe_parser.add_argument("--out", required=True)
    universe_parser.set_defaults(func=universe)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
