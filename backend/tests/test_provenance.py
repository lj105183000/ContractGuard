from app.provenance import (
    aggregate_sha256,
    canonical_json,
    command_provenance,
    contract_sha256,
    git_provenance,
    kb_provenance,
    profiles_provenance,
    prompt_provenance,
    redact_argv,
    sha256_bytes,
    sha256_file,
    sha256_text,
)


def test_hash_helpers_are_stable(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("合同", encoding="utf-8")
    assert sha256_bytes("合同".encode("utf-8")) == sha256_text("合同")
    assert sha256_file(path) == sha256_bytes("合同".encode("utf-8"))
    assert sha256_file(tmp_path / "missing.txt") is None
    assert contract_sha256("") is None
    assert contract_sha256("合同") == sha256_text("合同")


def test_canonical_json_and_aggregate_are_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert aggregate_sha256([("b", "2"), ("a", "1")]) == aggregate_sha256(
        [("a", "1"), ("b", "2")]
    )
    assert aggregate_sha256([("a", "1")]) != aggregate_sha256([("a", "2")])


def test_prompt_kb_profiles_have_versions():
    prompt = prompt_provenance()
    assert prompt["version"]
    assert len(prompt["file_sha256"]) == 64
    kb = kb_provenance()
    assert kb["legal_articles"] == 32
    assert kb["rules"] == 25
    assert kb["version"].startswith("kb-")
    profiles = profiles_provenance()
    assert profiles["version"].startswith("profiles-")


def test_git_provenance_never_raises():
    result = git_provenance()
    assert "commit" in result
    assert "dirty" in result


def test_redact_argv_hides_paths_but_keeps_flags():
    argv = [
        "eval/run_eval.py",
        "--manifest",
        "/private/manifest.yaml",
        "--mode",
        "rules",
        r"C:\secret\contract.pdf",
        "--labels-dir=../private/labels",
    ]
    redacted, count = redact_argv(argv)
    assert redacted[0] == "eval/run_eval.py"
    assert "--manifest" in redacted
    assert "/private/manifest.yaml" not in redacted
    assert "--labels-dir=<redacted>" in redacted
    assert r"C:\secret\contract.pdf" not in redacted
    assert count == 3

    command = command_provenance("eval/run_eval.py", argv[1:])
    assert command["entrypoint"] == "eval/run_eval.py"
    assert command["redacted_paths"] == 3
