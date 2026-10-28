from pathlib import Path

from app.rag.corpus import load_kb, sample_path

BACKEND = Path(__file__).resolve().parents[1]


def test_kb_counts_and_integrity():
    kb = load_kb()
    assert len(kb.legal_articles) == 32
    assert len(kb.rules) == 25
    assert len(kb.samples) == 8
    assert len(kb.legal_by_id) == len(kb.legal_articles)
    assert len(kb.rules_by_id) == len(kb.rules)
    for article in kb.legal_articles:
        assert article.text.strip()
        assert article.source_url.startswith("http")
        assert article.effective_date
    for rule in kb.rules:
        assert rule.recommendation.strip()
        assert all(law_id in kb.legal_by_id for law_id in rule.legal_basis)


def test_sample_files_exist():
    kb = load_kb()
    for sample in kb.samples:
        path = sample_path(sample)
        assert path.exists(), path
        assert path.suffix.lower().lstrip(".") == sample.format
