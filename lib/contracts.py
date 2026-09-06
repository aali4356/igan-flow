"""Strict release modes, immutable real cohort, portable synthetic inputs."""

import csv
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from integrity import sha256

KINDS = ("matrix", "features", "barcodes")
REAL_PRESETS = {
    "baseline": {"min_features": 200, "max_features": 6000, "max_mito_pct": 20},
    "strict": {"min_features": 500, "max_features": 5000, "max_mito_pct": 10},
}


def namespace(mode, key=None, sample_id=None):
    key = key or (sample_id if mode == "sample" else "main")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", key):
        raise ValueError("run-key must be a short ASCII identifier")
    if key in {"test", "sample", "full"}:
        raise ValueError("Reserved run-key: mode namespaces are selected automatically")
    if mode == "sample" and not re.fullmatch(r"GSM[0-9]{7}", sample_id or ""):
        raise ValueError("Sample mode requires a valid GEO sample ID")
    return f"{mode}/{key}"


def settings_contract(path, synthetic):
    config = json.loads(Path(path).read_text())
    keys = {
        "dataset",
        "synthetic",
        "expected_feature_rows",
        "feature_content_sha256",
        "baseline",
        "strict",
        "review_min_cells",
        "review_min_retention",
    }
    if (
        set(config) != keys
        or type(config["synthetic"]) is not bool
        or config["synthetic"] != synthetic
    ):
        raise ValueError("Settings schema or synthetic/real mode mismatch")
    if config["dataset"] != ("SYNTHETIC" if synthetic else "GSE285335"):
        raise ValueError("Dataset and execution mode disagree")
    if type(config["expected_feature_rows"]) is not int or config["expected_feature_rows"] <= 0:
        raise ValueError("Feature row count must be a positive integer")
    if not re.fullmatch(r"[a-f0-9]{64}", config["feature_content_sha256"]):
        raise ValueError("Invalid feature contract digest")
    for setting in ("baseline", "strict"):
        value = config[setting]
        if set(value) != {"min_features", "max_features", "max_mito_pct"}:
            raise ValueError("Invalid QC parameter names")
        if (
            any(type(value[k]) is not int for k in ("min_features", "max_features"))
            or not 0 <= value["min_features"] <= value["max_features"]
        ):
            raise ValueError("Invalid QC feature bounds")
        if (
            type(value["max_mito_pct"]) not in (float, int)
            or not math.isfinite(value["max_mito_pct"])
            or not 0 <= value["max_mito_pct"] <= 100
        ):
            raise ValueError("Invalid mitochondrial percentage threshold")
    if (
        type(config["review_min_cells"]) is not int
        or config["review_min_cells"] < 0
        or type(config["review_min_retention"]) not in (int, float)
        or not 0 <= config["review_min_retention"] <= 1
    ):
        raise ValueError("Invalid donor review thresholds")
    b, s = config["baseline"], config["strict"]
    if (
        s["min_features"] < b["min_features"]
        or s["max_features"] > b["max_features"]
        or s["max_mito_pct"] > b["max_mito_pct"]
    ):
        raise ValueError("Strict parameters must be a subset of baseline")
    if not synthetic and (
        any(config[k] != v for k, v in REAL_PRESETS.items())
        or config["review_min_cells"] != 500
        or config["review_min_retention"] != 0.5
    ):
        raise ValueError("The real release uses immutable QC presets")
    return config


def resolve_manifest(path, synthetic, reference):
    path = Path(path).resolve()
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Empty manifest")
    required = {
        "sample_id",
        "donor_id",
        "author_label",
        "group",
        "batch",
        "chemistry",
        "metadata_source_url",
        *[f"{k}_{s}" for k in KINDS for s in ("url", "bytes", "sha256")],
    }
    if not required.issubset(rows[0]) or any(not row[k] for row in rows for k in required):
        raise ValueError("Missing required manifest metadata")
    if len({r["sample_id"] for r in rows}) != len(rows) or len(
        {r["donor_id"] for r in rows}
    ) != len(rows):
        raise ValueError("Duplicate sample/donor identifier")
    for row in rows:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", row["sample_id"]):
            raise ValueError("Invalid sample identifier")
        if (
            row["group"] not in {"Healthy", "Early", "Late"}
            or row["batch"] not in {"First", "Second"}
            or row["chemistry"] != {"First": "5prime_v1.1", "Second": "5prime_v2.0"}[row["batch"]]
        ):
            raise ValueError("Invalid group/batch/chemistry metadata")
        for kind in KINDS:
            if int(row[kind + "_bytes"]) <= 0 or not re.fullmatch(
                r"[a-f0-9]{64}", row[kind + "_sha256"]
            ):
                raise ValueError("Every input requires a positive size and SHA-256")
            url = row[kind + "_url"]
            parsed = urlparse(url)
            if synthetic:
                if parsed.scheme not in ("", "file") or parsed.netloc:
                    raise ValueError("Synthetic inputs must be local files")
                source = Path(unquote(parsed.path))
                if not source.is_absolute():
                    source = path.parent / source
                row[kind + "_url"] = source.resolve().as_uri()
            elif parsed.scheme != "https" or parsed.hostname != "ftp.ncbi.nlm.nih.gov":
                raise ValueError("Unexpected real data host")
    if not synthetic:
        locked = json.loads(Path(reference).read_text())["files"]
        expected = {(r["sample_id"], r["kind"]): r for r in locked}
        if len(rows) != 26 or {(r["sample_id"], k) for r in rows for k in KINDS} != set(expected):
            raise ValueError("Real cohort must contain all 26 frozen donors")
        for row in rows:
            for k in KINDS:
                item = expected[row["sample_id"], k]
                if any(
                    str(row[k + "_" + field]) != str(item[field])
                    for field in ("url", "bytes", "sha256")
                ):
                    raise ValueError("Real input differs from the frozen source reference")
    return sorted(rows, key=lambda r: r["sample_id"])


def source_path(row, kind, cache):
    url = row[kind + "_url"]
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(cache) / "downloads/GSE285335" / row["sample_id"] / Path(parsed.path).name


def check_sources(rows, cache, allow_download):
    checked = []
    for row in rows:
        for kind in KINDS:
            path = source_path(row, kind, cache)
            if not path.is_file():
                if allow_download and row[kind + "_url"].startswith("https:"):
                    continue
                raise ValueError(f"Missing input: {path}")
            if (
                path.stat().st_size != int(row[kind + "_bytes"])
                or sha256(path) != row[kind + "_sha256"]
            ):
                raise ValueError(f"Source integrity mismatch: {path}")
            checked.append(
                {"sample_id": row["sample_id"], "kind": kind, "sha256": row[kind + "_sha256"]}
            )
    return checked
