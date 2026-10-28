"""加载并统一法条、Playbook、示例合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import LEGAL_DIR, SAMPLES_DIR
from app.rules.engine import load_playbook
from app.schemas import LegalArticle, PlaybookRule, SampleInfo


@dataclass
class KBDocument:
    id: str
    doc_type: str  # law / rule
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeBase:
    legal_articles: list[LegalArticle]
    rules: list[PlaybookRule]
    legal_by_id: dict[str, LegalArticle]
    rules_by_id: dict[str, PlaybookRule]
    documents: list[KBDocument]
    samples: list[SampleInfo]
    samples_dir: Path


def load_legal_articles(legal_dir: Path = LEGAL_DIR) -> list[LegalArticle]:
    articles: list[LegalArticle] = []
    for path in sorted(legal_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"法条文件必须是列表：{path}")
        articles.extend(LegalArticle.model_validate(item) for item in data)
    ids = [article.id for article in articles]
    duplicates = {item for item in ids if ids.count(item) > 1}
    if duplicates:
        raise ValueError(f"法条 ID 重复：{sorted(duplicates)}")
    if not articles:
        raise ValueError(f"没有找到法条：{legal_dir}")
    return articles


def _law_document(article: LegalArticle) -> KBDocument:
    return KBDocument(
        id=article.id,
        doc_type="law",
        title=f"{article.law} {article.article} {article.title}",
        text=(
            f"{article.law} {article.article}【{article.title}】\n"
            f"{article.text}\n"
            f"生效日期：{article.effective_date}"
        ),
        metadata={
            "law": article.law,
            "article": article.article,
            "tags": article.tags,
            "effective_date": article.effective_date,
            "source_url": article.source_url,
        },
    )


def _rule_document(rule: PlaybookRule) -> KBDocument:
    applies_to = "、".join(item.value for item in rule.applies_to) or "通用"
    return KBDocument(
        id=rule.id,
        doc_type="rule",
        title=f"{rule.id} {rule.title}",
        text=(
            f"{rule.id} {rule.title}\n"
            f"说明：{rule.description}\n"
            f"适用条款：{applies_to}\n"
            f"风险等级：{rule.severity.value}\n"
            f"推荐立场：{rule.recommendation}\n"
            f"退让底线：{rule.fallback}\n"
            f"法律依据：{', '.join(rule.legal_basis)}"
        ),
        metadata={
            "category": rule.category.value,
            "severity": rule.severity.value,
            "applies_to": [item.value for item in rule.applies_to],
            "legal_basis": rule.legal_basis,
        },
    )


def load_samples(samples_dir: Path = SAMPLES_DIR) -> list[SampleInfo]:
    config_path = samples_dir / "samples.yaml"
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or []
        return [SampleInfo.model_validate(item) for item in data]
    samples = []
    for path in sorted(samples_dir.iterdir()):
        if path.suffix.lower() in {".pdf", ".docx", ".txt"}:
            samples.append(
                SampleInfo(
                    id=path.stem,
                    title=path.stem,
                    filename=path.name,
                    format=path.suffix.lower().lstrip("."),
                )
            )
    return samples


@lru_cache(maxsize=1)
def load_kb() -> KnowledgeBase:
    articles = load_legal_articles()
    rules = load_playbook(LEGAL_DIR.parent / "playbook.yaml")
    documents = [_law_document(article) for article in articles] + [
        _rule_document(rule) for rule in rules
    ]
    return KnowledgeBase(
        legal_articles=articles,
        rules=rules,
        legal_by_id={article.id: article for article in articles},
        rules_by_id={rule.id: rule for rule in rules},
        documents=documents,
        samples=load_samples(),
        samples_dir=SAMPLES_DIR,
    )


def find_sample(sample_id: str) -> SampleInfo | None:
    for sample in load_kb().samples:
        if sample.id == sample_id:
            return sample
    return None


def sample_path(sample: SampleInfo) -> Path:
    return load_kb().samples_dir / sample.filename
