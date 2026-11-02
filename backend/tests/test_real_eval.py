import pytest
import yaml

from eval.run_real_eval import load_manifest, select_contracts


def _write_manifest(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_legacy_manifest_infers_dev_split(tmp_path):
    path = tmp_path / "manifest.yaml"
    _write_manifest(path, [{"id": "r1", "filename": "r1.pdf"}])
    meta, contracts = load_manifest(path)
    assert meta["split_inferred"] is True
    assert contracts[0]["split"] == "dev"


def test_v2_manifest_keeps_declared_splits(tmp_path):
    path = tmp_path / "manifest.yaml"
    _write_manifest(
        path,
        {
            "dataset": {"id": "real", "dataset_version": "0.1.0"},
            "contracts": [
                {"id": "r1", "filename": "r1.pdf", "split": "dev"},
                {"id": "r2", "filename": "r2.pdf", "split": "holdout"},
            ],
        },
    )
    meta, contracts = load_manifest(path)
    assert meta["dataset_version"] == "0.1.0"
    assert [item["split"] for item in contracts] == ["dev", "holdout"]


def test_holdout_requires_explicit_allow(tmp_path):
    contracts = [{"id": "r1", "split": "dev"}, {"id": "r2", "split": "holdout"}]
    with pytest.raises(SystemExit):
        select_contracts(contracts, "holdout", allow_holdout=False)
    assert [item["id"] for item in select_contracts(
        contracts, "holdout", allow_holdout=True
    )] == ["r2"]
    assert [item["id"] for item in select_contracts(
        contracts, "all", allow_holdout=True
    )] == ["r1", "r2"]


def test_empty_split_fails():
    with pytest.raises(SystemExit):
        select_contracts([{"id": "r1", "split": "dev"}], "holdout", True)
