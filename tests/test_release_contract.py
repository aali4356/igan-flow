"""Entry contracts and immutable publication checks for release failures."""

import json
from pathlib import Path

import pytest
from contracts import namespace, resolve_manifest, settings_contract
from integrity import bind_artifact, seal_directory, verify_bound_artifacts
from run_state import atomic_json, show_attempt

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "mode,key,sample", [("test", "full", None), ("test", "../full", None), ("sample", None, "bad")]
)
def test_namespace_rejects_collisions(mode, key, sample):
    with pytest.raises(ValueError):
        namespace(mode, key, sample)


def test_modes_always_have_separate_roots():
    assert len({namespace(mode, "same", "GSM8700986") for mode in ("test", "sample", "full")}) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        {"synthetic": False},
        {"dataset": "GSE285335"},
        {"unexpected": 1},
        {"strict": {"min_features": 0, "max_features": 99, "max_mito_pct": 100}},
        {"baseline": {"min_features": True, "max_features": 3, "max_mito_pct": 50}},
        {"review_min_retention": float("nan")},
    ],
)
def test_invalid_settings_fail_at_entry(tmp_path, mutation):
    value = json.loads((ROOT / "tests/fixtures/settings.json").read_text())
    value.update(mutation)
    target = tmp_path / "settings.json"
    target.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        settings_contract(target, True)


def test_real_checksums_are_complete_and_enforced(tmp_path):
    source = ROOT / "assets/samplesheet.csv"
    rows = resolve_manifest(source, False, ROOT / "assets/reference_inputs.json")
    assert len(rows) == 26 and all(
        len(r[k + "_sha256"]) == 64 for r in rows for k in ("matrix", "features", "barcodes")
    )
    broken = tmp_path / "bad.csv"
    broken.write_text(source.read_text().replace(rows[0]["matrix_sha256"], "0" * 64))
    with pytest.raises(ValueError, match="frozen source"):
        resolve_manifest(broken, False, ROOT / "assets/reference_inputs.json")


def test_seal_replacement_does_not_hide_changed_artifact(tmp_path):
    item = tmp_path / "nested"
    item.mkdir()
    (item / "counts").write_text("original")
    seal_directory(item)
    bound = bind_artifact(item)
    (item / "counts").write_text("tampered")
    seal_directory(item)
    with pytest.raises(ValueError, match="seal changed"):
        verify_bound_artifacts([bound])


def test_failed_attempt_preserves_history_and_exposes_failure(tmp_path):
    out = tmp_path / "out"
    old = tmp_path / "old"
    old.mkdir()
    prior = {"run_id": "old", "portable": str(old), "status": "PASS"}
    atomic_json(out / "last_success.json", prior)
    record = {"run_id": "new", "status": "FAILED", "error": "broken artifact"}
    show_attempt(out, tmp_path / "new", record, prior)
    assert json.loads((out / "last_success.json").read_text()) == prior
    assert json.loads((out / "latest_attempt.json").read_text()) == record
    assert "FAILED" in (out / "current/report.html").read_text()
    assert "Historical result" in (out / "current/report.html").read_text()
