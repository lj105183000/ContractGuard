import yaml

from scripts.validate_eval_assets import validate


def test_public_eval_assets_validate():
    errors, counts = validate()
    assert errors == []
    assert counts["samples"] == 8
    assert counts["labels"] == 51
    assert counts["label_files"] == 8
    assert counts["real_public"] == 3
    assert counts["retrieval_queries"] == 25


def test_validator_reports_missing_files_and_private_fields(tmp_path, monkeypatch):
    samples_dir = tmp_path / "samples"
    labels_dir = tmp_path / "labels"
    samples_dir.mkdir()
    labels_dir.mkdir()
    (samples_dir / "samples.yaml").write_text(
        yaml.safe_dump(
            [{"id": "s1", "title": "T", "filename": "missing.txt"}],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (labels_dir / "s1.json").write_text(
        '[{"id":"s1-L1","category":"payment","severity":"high"}]',
        encoding="utf-8",
    )
    real_manifest = tmp_path / "real.yaml"
    real_manifest.write_text(
        yaml.safe_dump(
            [{"id": "r1", "scope": "in_scope", "split": "dev", "filename": "secret.pdf"}],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    queries = tmp_path / "queries.yaml"
    queries.write_text(
        yaml.safe_dump([{"id": "q1", "relevant": ["NOPE"]}], allow_unicode=True),
        encoding="utf-8",
    )
    badcases = tmp_path / "badcases.yaml"
    badcases.write_text(
        yaml.safe_dump(
            {"rules": {"known_false_negatives": [{"label_id": "missing-L1"}]}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.validate_eval_assets.SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("scripts.validate_eval_assets.LABELS_DIR", labels_dir)
    monkeypatch.setattr("scripts.validate_eval_assets.REAL_MANIFEST", real_manifest)
    monkeypatch.setattr("scripts.validate_eval_assets.RETRIEVAL_QUERIES", queries)
    monkeypatch.setattr("scripts.validate_eval_assets.BADCASES", badcases)

    errors, _counts = validate()

    joined = "\n".join(errors)
    assert "合同文件缺失" in joined
    assert "不允许字段" in joined
    assert "不存在的法条" in joined
    assert "不存在的 label" in joined
