"""Dataset/label/KB provenance for evaluation runs.

All versions are content-addressed; no private paths or filenames are included
in public results. Callers decide which hashes may be published.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from app.provenance import (
    aggregate_sha256,
    kb_provenance,
    sha256_file,
)


def _file_entries(paths: Iterable[Path]) -> list[tuple[str, str | None]]:
    entries = []
    for path in sorted(paths):
        entries.append((path.name, sha256_file(path)))
    return entries


def label_fingerprint(labels_dir: Path) -> dict[str, Any]:
    paths = sorted(Path(labels_dir).glob("*.json"))
    entries = _file_entries(paths)
    digest = aggregate_sha256(entries)
    count = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                count += len(data)
        except (OSError, json.JSONDecodeError):
            continue
    return {
        "version": digest[:12],
        "sha256": digest,
        "file_count": len(paths),
        "label_count": count,
    }


def sample_fingerprint(samples: list[dict], samples_dir: Path) -> dict[str, Any]:
    entries = []
    for sample in sorted(samples, key=lambda item: item["id"]):
        path = Path(samples_dir) / sample["filename"]
        entries.append((sample["id"], sha256_file(path)))
    digest = aggregate_sha256(entries)
    return {
        "version": digest[:12],
        "sha256": digest,
        "sample_count": len(samples),
    }


def kb_fingerprint(kb: Any) -> dict[str, Any]:
    """Delegate to the shared runtime helper; ``kb`` is accepted for callers."""
    return kb_provenance()


def synthetic_dataset_provenance(
    samples: list[dict],
    samples_dir: Path,
    labels_dir: Path,
    kb: Any,
    *,
    limited: bool = False,
) -> dict[str, Any]:
    samples_info = sample_fingerprint(samples, samples_dir)
    labels_info = label_fingerprint(labels_dir)
    kb_info = kb_fingerprint(kb)
    dataset_digest = aggregate_sha256(
        [
            ("samples", samples_info["sha256"]),
            ("labels", labels_info["sha256"]),
            ("kb", kb_info["sha256"]),
        ]
    )
    return {
        "id": "synthetic_procurement",
        "version": dataset_digest[:12],
        "dataset_sha256": dataset_digest,
        "label_version": labels_info["version"],
        "labels_sha256": labels_info["sha256"],
        "samples_sha256": samples_info["sha256"],
        "sample_count": samples_info["sample_count"],
        "label_count": labels_info["label_count"],
        "label_files": labels_info["file_count"],
        "kb_version": kb_info["version"],
        "kb_sha256": kb_info["sha256"],
        "legal_articles": kb_info["legal_articles"],
        "rules": kb_info["rules"],
        "split": "regression_only",
        "limited": limited,
    }


def real_contracts_provenance(
    contracts: list[dict[str, Any]],
    labels_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    contract_entries = [
        (item["id"], item.get("sha256") or item.get("_sha256"))
        for item in contracts
    ]
    contracts_digest = aggregate_sha256(contract_entries)
    labels_entries = []
    for item in contracts:
        label_path = Path(labels_dir) / f"{item['id']}.json"
        labels_entries.append((item["id"], sha256_file(label_path)))
    labels_digest = aggregate_sha256(labels_entries)
    manifest_digest = sha256_file(manifest_path)
    dataset_digest = aggregate_sha256(
        [
            ("manifest", manifest_digest),
            ("contracts", contracts_digest),
            ("labels", labels_digest),
        ]
    )
    return {
        "id": "real_procurement",
        "version": dataset_digest[:12],
        "dataset_sha256": dataset_digest,
        "manifest_sha256": manifest_digest,
        "contracts_sha256": contracts_digest,
        "labels_sha256": labels_digest,
        "contract_count": len(contracts),
        "labeled_count": sum(1 for item in contracts if item.get("has_labels")),
    }
