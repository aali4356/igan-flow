#!/usr/bin/env python3
"""Actual offline Nextflow cache, failure, integrity and relocation regressions."""

import csv
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from check_cache import canonical, latest, require, trace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from run_state import atomic_json, implementation_fingerprint


def main():
    implementation = implementation_fingerprint(ROOT)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    isolated = Path(os.environ["IGAN_CACHE"]) / "regressions" / stamp / "relocated project's path"
    isolated.mkdir(parents=True)
    for name in (
        "main.nf",
        "nextflow.config",
        "requirements.in",
        "requirements.lock",
        "requirements-dev.lock",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
    ):
        if (ROOT / name).is_file():
            shutil.copyfile(ROOT / name, isolated / name)
    for name in ("lib", "scripts", "modules", "conf", "assets", "tests", "research", ".github"):
        if (ROOT / name).exists():
            shutil.copytree(
                ROOT / name,
                isolated / name,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
    scope = isolated / "results/test/main"
    logdir = isolated / "results/regressions"
    logdir.mkdir(parents=True)
    runs = []
    cases = []
    command = ["bash", str(isolated / "scripts/run.sh"), "test"]

    def run(*extra, success=True, error=None):
        attempt = subprocess.run([*command, *extra], capture_output=True, text=True)
        text = attempt.stdout + attempt.stderr
        (logdir / f"attempt-{len(runs):02d}.log").write_text(text)
        runs.append({"arguments": list(extra), "exit_code": attempt.returncode})
        require((attempt.returncode == 0) == success, text[-8000:])
        if error:
            require(error in text, text[-8000:])
        record = latest(scope)
        require(
            record["status"] == ("PASS" if success else "FAILED"), "Stale current attempt status"
        )
        if not success:
            require(
                "FAILED" in (scope / "current/report.html").read_text(),
                "Failure retained a successful current report",
            )
            require(
                json.loads((scope / "last_success.json").read_text())["status"] == "PASS",
                "Historical success lost",
            )
        return record

    initial = run()
    require(
        len(trace(initial)) == 13 and set(trace(initial).values()) == {"COMPLETED"},
        "Initial fixture did not execute all 13 tasks",
    )
    before = canonical(Path(initial["portable"]))
    current = run("--resume")
    require(
        len(trace(current)) == 13 and set(trace(current).values()) == {"CACHED"},
        "Unchanged fixture failed to cache",
    )
    require(before == canonical(Path(current["portable"])), "Unchanged fixture changed counts")
    cases += [
        "relocated_default_fixture_with_spaces_and_apostrophe",
        "13_task_execution",
        "13_task_unchanged_resume",
    ]

    # Cached and published nested products must fail closed before publication.
    cached = Path(current["tasks"]["EXPORT (TEST_A)"]["path"]) / "baseline.h5ad"
    published = Path(current["donors"]) / "TEST_A.export/baseline.h5ad"
    for location, target in (("cached", cached), ("published", published)):
        original = target.read_bytes()
        for kind in ("corrupt", "missing"):
            if kind == "corrupt":
                target.write_bytes(b"invalid AnnData")
            else:
                target.unlink()
            failed = run("--resume", success=False, error="integrity mismatch")
            require(failed["run_id"] != current["run_id"], "Failed attempt reused old identity")
            target.write_bytes(original)
            cases.append(f"{location}_{kind}_h5ad_fails_closed")
    matrix = isolated / "tests/fixtures/TEST_A/matrix.gz"
    original = matrix.read_bytes()
    matrix.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    run("--resume", success=False, error="Source integrity mismatch")
    matrix.write_bytes(original)
    matrix.unlink()
    run("--resume", success=False, error="Missing input")
    matrix.write_bytes(original)
    cases += [
        "current_source_corruption",
        "current_source_missing",
        "failed_attempt_does_not_inherit_pass",
    ]

    # Optimization must never disable metadata verification.
    source = Path(current["tasks"]["ACQUIRE (TEST_A)"]["path"])
    exported = Path(current["donors"]) / "TEST_A.export"
    qc = exported / "cell_qc.tsv.gz"
    original_qc = qc.read_bytes()
    frame = pd.read_csv(qc, sep="\t")
    frame["group"] = "Late"
    frame.to_csv(qc, sep="\t", index=False)
    optimized = subprocess.run(
        [
            sys.executable,
            "-O",
            str(isolated / "lib/verify_donor.py"),
            "--source",
            str(source),
            "--export",
            str(exported),
            "--settings",
            str(isolated / "tests/fixtures/settings.json"),
            "--out",
            str(logdir / "must-not-pass.json"),
        ],
        capture_output=True,
        text=True,
    )
    qc.write_bytes(original_qc)
    require(
        optimized.returncode != 0 and "QC group differs from source" in optimized.stderr,
        "python -O bypassed verification",
    )
    cases.append("optimized_python_metadata_tamper_rejected")

    # A valid changed donor input recomputes only its branch and cohort stages.
    matrix.write_bytes(
        gzip.compress(gzip.decompress(original).replace(b"2 2 3\n", b"2 2 4\n"), mtime=0)
    )
    manifest = isolated / "tests/fixtures/samplesheet.csv"
    with manifest.open() as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["sample_id"] == "TEST_A":
            row["matrix_bytes"] = matrix.stat().st_size
            row["matrix_sha256"] = hashlib.sha256(matrix.read_bytes()).hexdigest()
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    changed = run("--resume")
    statuses = trace(changed)
    require(len(statuses) == 13, "Incomplete selective trace")
    require(
        all(
            status == ("CACHED" if name.endswith("(TEST_B)") else "COMPLETED")
            for name, status in statuses.items()
        ),
        f"Unexpected selective invalidation: {statuses}",
    )
    counts = pd.read_csv(
        Path(changed["portable"]) / "tables/pseudobulk_baseline.tsv.gz", sep="\t"
    ).set_index("gene_id")
    require(
        counts.loc["CD3D", "TEST_A"] == 6 and counts.loc["CD3D", "TEST_B"] == 5,
        "Changed donor counts are wrong",
    )
    cases.append("single_donor_invalidation_5_cached_8_recomputed")

    template = isolated / "lib/report.html.j2"
    marker = "<!-- report dependency regression -->"
    template.write_text(template.read_text() + "\n" + marker + "\n")
    changed_report = run("--resume")
    statuses = trace(changed_report)
    require(
        all(
            status == ("COMPLETED" if name == "REPORT" else "CACHED")
            for name, status in statuses.items()
        ),
        f"Report dependency wrong: {statuses}",
    )
    require(
        marker in (scope / "current/report.html").read_text(), "Changed template was not rendered"
    )
    cases.append("report_source_invalidation_12_cached_1_recomputed")

    # Reject mode collisions and conflicting arguments before touching any scope.
    successful = (scope / "last_success.json").read_bytes()
    for extra in (["--run-key", "full"], ["--sample-id", "GSM8700986"]):
        result = subprocess.run([*command, *extra], capture_output=True, text=True)
        require(result.returncode != 0, "Invalid CLI combination succeeded")
        require(
            (scope / "last_success.json").read_bytes() == successful,
            "Invalid CLI changed successful state",
        )
    cases += ["reserved_mode_collision_rejected", "conflicting_sample_option_rejected"]
    subprocess.run(
        [
            sys.executable,
            str(isolated / "scripts/verify_results.py"),
            "--results",
            str(scope / "current"),
        ],
        check=True,
    )
    cases.append("fresh_fixture_independent_audit")

    # A declared but truncated gzip passes byte preflight and must fail in Nextflow.
    matrix.write_bytes(matrix.read_bytes()[:-6])
    for row in rows:
        if row["sample_id"] == "TEST_A":
            row["matrix_bytes"] = matrix.stat().st_size
            row["matrix_sha256"] = hashlib.sha256(matrix.read_bytes()).hexdigest()
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    failed = run("--resume", success=False, error="end-of-stream")
    require(
        failed.get("nextflow_exit_code", 0) != 0, "Truncated gzip never exercised Nextflow failure"
    )
    cases.append("nextflow_truncated_gzip_failure")
    result = {
        "status": "PASS",
        "cases": cases,
        "case_count": len(cases),
        "runs": runs,
        "isolated_project": str(isolated),
        "initial_trace": initial["task_trace"],
        "unchanged_trace": current["task_trace"],
        "selective_trace": changed["task_trace"],
        "report_trace": changed_report["task_trace"],
        "implementation": implementation,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    require(
        implementation == implementation_fingerprint(ROOT),
        "Implementation changed during regressions",
    )
    atomic_json(ROOT / "results/ci/regressions.json", result)
    print(f"PASS: {len(cases)} integration checks; evidence results/ci/regressions.json")


if __name__ == "__main__":
    main()
