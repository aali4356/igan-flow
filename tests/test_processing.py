"""Count/QC invariants and adversarial input checks independent of the full cohort."""

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from aggregate import design_audit
from common import gzip_check
from pipeline import apply_masks, metrics_and_masks, read_validated_matrix, validate_manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "tests/fixtures/TEST_A", source)
    settings = json.loads((ROOT / "tests/fixtures/settings.json").read_text())
    return source, settings


def replace_matrix(source, text):
    (source / "matrix.gz").write_bytes(gzip.compress(text.encode(), mtime=0))


def replace_features(source, settings, text):
    raw = text.encode()
    (source / "features.gz").write_bytes(gzip.compress(raw, mtime=0))
    settings["feature_content_sha256"] = hashlib.sha256(raw).hexdigest()


def test_hand_calculated_counts_and_masks(fixture):
    source, settings = fixture
    matrix, genes, bars, _, _ = read_validated_matrix(source, settings, 2)
    qc = metrics_and_masks(matrix, genes, settings)
    assert bars == ["A", "B", "C"]
    assert qc.total_counts.tolist() == [4, 4, 1]
    assert qc.mitochondrial_pct.tolist() == [50, 0, 100]
    assert qc.baseline_keep.tolist() == [True, True, False]
    assert qc.strict_keep.tolist() == [False, True, False]
    np.testing.assert_array_equal(
        np.asarray(matrix[:, qc.baseline_keep.to_numpy()].sum(axis=1)).ravel(), [2, 5, 1]
    )
    np.testing.assert_array_equal(
        np.asarray(matrix[:, qc.strict_keep.to_numpy()].sum(axis=1)).ravel(), [0, 3, 1]
    )


@pytest.mark.parametrize(
    "text,match",
    [
        ("%%MatrixMarket matrix coordinate real general\n3 3 1\n1 1 0.5\n", "integer counts"),
        ("%%MatrixMarket matrix coordinate integer general\n3 3 1\n1 1 0.5\n", "integer tokens"),
        ("%%MatrixMarket matrix coordinate integer general\n3 3 1\n1 1 1e2\n", "integer tokens"),
        ("%%MatrixMarket matrix coordinate integer general\n3 3 1\n1 1 -1\n", "nonnegative"),
        (
            "%%MatrixMarket matrix coordinate integer general\n3 3 2\n1 1 1\n1 1 2\n",
            "Repeated sparse",
        ),
        ("%%MatrixMarket matrix coordinate integer general\n3 4 1\n1 1 1\n", "dimensions"),
    ],
)
def test_invalid_matrices_rejected(fixture, text, match):
    source, settings = fixture
    replace_matrix(source, text)
    with pytest.raises(ValueError, match=match):
        read_validated_matrix(source, settings, 2)


def test_out_of_range_coordinate_rejected(fixture):
    source, settings = fixture
    replace_matrix(source, "%%MatrixMarket matrix coordinate integer general\n3 3 1\n4 1 1\n")
    with pytest.raises(ValueError):
        read_validated_matrix(source, settings, 2)


def test_duplicate_barcodes_rejected(fixture):
    source, settings = fixture
    (source / "barcodes.gz").write_bytes(gzip.compress(b"A\nA\nC\n", mtime=0))
    with pytest.raises(ValueError, match="duplicate barcode"):
        read_validated_matrix(source, settings, 2)


def test_duplicate_features_rejected(fixture):
    source, settings = fixture
    replace_features(
        source,
        settings,
        "MT-ND1\tMT-ND1\tGene Expression\nMT-ND1\tMT-ND1\tGene Expression\nLYZ\tLYZ\tGene Expression\n",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        read_validated_matrix(source, settings, 2)


def test_reordered_features_rejected(fixture):
    source, settings = fixture
    content = gzip.decompress((source / "features.gz").read_bytes()).splitlines(keepends=True)
    (source / "features.gz").write_bytes(gzip.compress(b"".join(reversed(content)), mtime=0))
    with pytest.raises(ValueError, match="content/order"):
        read_validated_matrix(source, settings, 2)


def test_missing_mito_annotation_rejected(fixture):
    source, settings = fixture
    replace_features(
        source,
        settings,
        "G1\tG1\tGene Expression\nCD3D\tCD3D\tGene Expression\nLYZ\tLYZ\tGene Expression\n",
    )
    with pytest.raises(ValueError, match="mitochondrial"):
        read_validated_matrix(source, settings, 2)


def test_explicit_zeros_are_not_detected_features(fixture):
    source, settings = fixture
    replace_matrix(
        source, "%%MatrixMarket matrix coordinate integer general\n3 3 3\n1 1 1\n2 1 0\n2 2 1\n"
    )
    matrix, genes, _, _, _ = read_validated_matrix(source, settings, 2)
    qc = metrics_and_masks(matrix, genes, settings)
    assert qc.n_features.tolist() == [1, 1, 0]
    assert np.isnan(qc.mitochondrial_pct.iloc[2])
    assert qc.baseline_reject_zero_counts.iloc[2]
    assert not qc.baseline_keep.iloc[2]


def test_truncated_gzip_is_rejected(fixture):
    source, _ = fixture
    f = source / "matrix.gz"
    f.write_bytes(f.read_bytes()[:-6])
    with pytest.raises((EOFError, OSError)):
        gzip_check(f)


def test_production_boundary_values():
    settings = json.loads((ROOT / "assets/qc_real.json").read_text())
    frame = pd.DataFrame(
        {
            "total_counts": [1000] * 10 + [0],
            "n_features": [199, 200, 6000, 6001, 500, 499, 5000, 5001, 500, 500, 0],
            "mitochondrial_pct": [0, 20, 20, 0, 10, 10, 10, 10, 10.00001, 20.00001, np.nan],
        }
    )
    result = apply_masks(frame, settings)
    assert result.baseline_keep.tolist() == [
        False,
        True,
        True,
        False,
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    assert result.strict_keep.tolist() == [
        False,
        False,
        False,
        False,
        True,
        False,
        True,
        False,
        False,
        False,
        False,
    ]


def test_rank_keeps_all_four_columns_and_handles_zero_donors():
    rows = []
    for setting in ["baseline", "strict"]:
        for sid, group, batch in [("A", "Healthy", "First"), ("B", "Early", "Second")]:
            rows.append(
                dict(
                    sample_id=sid,
                    donor_id=sid,
                    group=group,
                    batch=batch,
                    setting=setting,
                    retained=1 if setting == "baseline" else 0,
                    low_cell_flag=True,
                    low_retention_flag=False,
                )
            )
    result = design_audit(pd.DataFrame(rows))
    assert result[0]["rank"] == 2 and result[0]["expected_rank"] == 4
    assert result[1]["rank"] is None and result[1]["status"] == "unavailable"


@pytest.mark.parametrize(
    "issue", ["duplicate", "missing_group", "wrong_chemistry", "missing_column"]
)
def test_manifest_rejects_ambiguous_input(tmp_path, issue):
    frame = pd.read_csv(ROOT / "tests/fixtures/samplesheet.csv", keep_default_na=False)
    if issue == "duplicate":
        frame.loc[1, "donor_id"] = frame.loc[0, "donor_id"]
    elif issue == "missing_group":
        frame.loc[0, "group"] = ""
    elif issue == "wrong_chemistry":
        frame.loc[0, "chemistry"] = "5prime_v2.0"
    else:
        frame = frame.drop(columns="matrix_url")
    dest = tmp_path / "bad.csv"
    frame.to_csv(dest, index=False)
    with pytest.raises(ValueError):
        validate_manifest(
            SimpleNamespace(
                manifest=dest, synthetic=True, research=None, out=tmp_path / "out", sample_id=""
            )
        )


def test_missing_local_file_cli_fails(tmp_path):
    row = (
        pd.read_csv(ROOT / "tests/fixtures/samplesheet.csv", keep_default_na=False)
        .iloc[0]
        .to_dict()
    )
    row["matrix_url"] = (tmp_path / "does-not-exist.gz").as_uri()
    record = tmp_path / "record.json"
    record.write_text(json.dumps(row))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "lib/pipeline.py"),
            "acquire",
            "--record",
            str(record),
            "--cache",
            str(tmp_path / "cache"),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Missing local input" in result.stderr


def test_zero_cell_export_roundtrips_and_independent_verifier_passes(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "tests/fixtures/TEST_A", source)
    replace_matrix(source, "%%MatrixMarket matrix coordinate integer general\n3 3 0\n")
    row = (
        pd.read_csv(ROOT / "tests/fixtures/samplesheet.csv", keep_default_na=False)
        .iloc[0]
        .to_dict()
    )
    for kind in ("matrix", "features", "barcodes"):
        path = source / (kind + ".gz")
        row[kind + "_url"] = path.as_uri()
        row[kind + "_bytes"] = path.stat().st_size
        row[kind + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    row["upstream_filtering_status"] = "unknown"
    record = tmp_path / "record.json"
    record.write_text(json.dumps(row))
    settings = ROOT / "tests/fixtures/settings.json"

    def stage(name, *options):
        dest = tmp_path / name
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "lib/pipeline.py"),
                name,
                *map(str, options),
                "--out",
                str(dest),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return dest

    acquired = stage("acquire", "--record", record, "--cache", tmp_path / "cache")
    converted = stage("convert", "--source", acquired, "--settings", settings)
    qc = stage("qc", "--source", converted, "--settings", settings)
    exported = stage("export", "--source", converted, "--qc", qc)
    check = tmp_path / "verification.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "lib/verify_donor.py"),
            "--source",
            str(acquired),
            "--export",
            str(exported),
            "--settings",
            str(settings),
            "--out",
            str(check),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(check.read_text())["baseline_retained"] == 0
    summaries = json.loads((exported / "qc_summary.json").read_text())
    assert all(
        s["status"] == "zero_cells" and s["retained"] == 0 and s["rejected_union"] == 3
        for s in summaries
    )
    manifest_audit = tmp_path / "manifest_validation.json"
    manifest_audit.write_text(json.dumps({"sample_ids": [row["sample_id"]]}))
    combined = tmp_path / "combined"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "lib/aggregate.py"),
            "--donors",
            str(exported),
            "--verifications",
            str(check),
            "--manifest-audit",
            str(manifest_audit),
            "--settings",
            str(settings),
            "--large-root",
            str(tmp_path),
            "--out",
            str(combined),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    exclusions = pd.read_csv(combined / "tables/excluded_samples.tsv", sep="\t")
    assert len(exclusions) == 2 and exclusions.status.eq("zero_cells").all()
    for setting in ("baseline", "strict"):
        matrix = pd.read_csv(combined / "tables" / f"pseudobulk_{setting}.tsv.gz", sep="\t")
        assert matrix.columns.tolist() == ["gene_id"] and len(matrix) == 3
        assert pd.read_csv(combined / "tables" / f"samples_{setting}.tsv", sep="\t").empty
    assert all(
        d["rank"] is None and d["status"] == "unavailable"
        for d in json.loads((combined / "evidence/design_audit.json").read_text())
    )
