#!/usr/bin/env python3
"""Build deterministic cohort and tiny-fixture manifests from preserved evidence."""

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "sample_id",
    "donor_id",
    "author_label",
    "group",
    "batch",
    "chemistry",
    "metadata_source_url",
]
FIELDS += [
    k + "_" + suffix
    for k in ("matrix", "features", "barcodes")
    for suffix in ("url", "bytes", "sha256")
]


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def real():
    research = ROOT / "research"
    original = list(
        csv.DictReader((research / "GSE285335_sample_manifest.tsv").open(), delimiter="\t")
    )
    inspection = json.loads((research / "GSM8700986_inspection.json").read_text())
    reference = {
        (r["sample_id"], r["kind"]): r
        for r in json.loads((ROOT / "assets/reference_inputs.json").read_text())["files"]
    }
    rows = []
    for r in original:
        row = dict(
            sample_id=r["sample_id"],
            donor_id=r["sample_id"],
            author_label=r["source_title"],
            group=r["group"],
            batch=r["batch"],
            chemistry={"First": "5prime_v1.1", "Second": "5prime_v2.0"}[r["batch"]],
            metadata_source_url="https://ftp.ncbi.nlm.nih.gov/geo/series/GSE285nnn/GSE285335/matrix/GSE285335_series_matrix.txt.gz",
        )
        for kind in ("matrix", "features", "barcodes"):
            row[kind + "_url"] = r[kind + "_url"]
            row[kind + "_bytes"] = r[kind + "_compressed_bytes"]
            row[kind + "_sha256"] = r.get(kind + "_sha256", "")
            if r["sample_id"] == "GSM8700986":
                row[kind + "_sha256"] = inspection["files"][kind]["sha256"]
            row[kind + "_sha256"] = reference[r["sample_id"], kind]["sha256"]
        rows.append(row)
    write_csv(ROOT / "assets/samplesheet.csv", rows)


def fixture(folder):
    folder.mkdir(parents=True, exist_ok=True)
    features = (
        b"MT-ND1\tMT-ND1\tGene Expression\nCD3D\tCD3D\tGene Expression\nLYZ\tLYZ\tGene Expression\n"
    )
    barcodes = b"A\nB\nC\n"
    matrix = b"%%MatrixMarket matrix coordinate integer general\n3 3 5\n1 1 2\n2 1 2\n2 2 3\n3 2 1\n1 3 1\n"
    rows = []
    for sid, group, batch in [("TEST_A", "Healthy", "First"), ("TEST_B", "Early", "Second")]:
        sample = folder / sid
        sample.mkdir(exist_ok=True)
        row = dict(
            sample_id=sid,
            donor_id=sid,
            author_label="Synthetic " + sid,
            group=group,
            batch=batch,
            chemistry={"First": "5prime_v1.1", "Second": "5prime_v2.0"}[batch],
            metadata_source_url="synthetic:hand_calculated_fixture",
        )
        for kind, raw in [("matrix", matrix), ("features", features), ("barcodes", barcodes)]:
            target = sample / (kind + ".gz")
            data = gzip.compress(raw, mtime=0)
            target.write_bytes(data)
            row[kind + "_url"] = target.relative_to(folder).as_posix()
            row[kind + "_bytes"] = len(data)
            row[kind + "_sha256"] = hashlib.sha256(data).hexdigest()
        rows.append(row)
    write_csv(folder / "samplesheet.csv", rows)
    settings = {
        "dataset": "SYNTHETIC",
        "synthetic": True,
        "expected_feature_rows": 3,
        "feature_content_sha256": hashlib.sha256(features).hexdigest(),
        "baseline": {"min_features": 1, "max_features": 3, "max_mito_pct": 50},
        "strict": {"min_features": 2, "max_features": 3, "max_mito_pct": 25},
        "review_min_cells": 500,
        "review_min_retention": 0.5,
    }
    (folder / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path)
    args = parser.parse_args()
    if args.fixture_dir:
        fixture(args.fixture_dir.resolve())
    else:
        real()
        if not (ROOT / "tests/fixtures/samplesheet.csv").exists():
            fixture(ROOT / "tests/fixtures")
