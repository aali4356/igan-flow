#!/usr/bin/env python3
"""Check a completed full cohort resumes without changing analytical outputs."""

import csv
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from run_state import atomic_json


def canonical(out):
    paths = list((out / "cell_qc").glob("*.gz"))
    paths += [
        out / "tables" / name
        for name in (
            "donor_qc.tsv",
            "genes.tsv",
            "pseudobulk_baseline.tsv.gz",
            "pseudobulk_strict.tsv.gz",
            "samples_baseline.tsv",
            "samples_strict.tsv",
            "excluded_samples.tsv",
        )
    ]
    return {
        str(p.relative_to(out)): hashlib.sha256(
            gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
        ).hexdigest()
        for p in sorted(paths)
    }


def latest(scope):
    return json.loads((scope / "latest_attempt.json").read_text())


def trace(record):
    with Path(record["task_trace"]).open() as handle:
        return {r["name"]: r["status"] for r in csv.DictReader(handle, delimiter="\t")}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    scope = ROOT / "results/full/main"
    prior = latest(scope)
    require(prior["status"] == "PASS", "Need a completed full cohort")
    before = canonical(Path(prior["portable"]))
    subprocess.run(["bash", str(ROOT / "scripts/run.sh"), "full", "--resume"], check=True)
    current = latest(scope)
    statuses = trace(current)
    require(
        len(statuses) == 133 and set(statuses.values()) == {"CACHED"},
        f"Unexpected cache behavior: {statuses}",
    )
    require(
        before == canonical(Path(current["portable"])),
        "Canonical outputs changed on unchanged resume",
    )
    result = {
        "status": "PASS",
        "kind": "full_unchanged_resume",
        "tasks_cached": len(statuses),
        "previous_run_id": prior["run_id"],
        "current_run_id": current["run_id"],
        "execution_trace": prior["task_trace"],
        "resume_trace": current["task_trace"],
        "canonical_hashes": before,
        "implementation": current["implementation"],
    }
    atomic_json(Path(current["run_root"]) / "validation/cache_full.json", result)
    print("PASS: all 133 tasks cached; canonical outputs unchanged")


if __name__ == "__main__":
    main()
