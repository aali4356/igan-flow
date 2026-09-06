#!/usr/bin/env python3
"""Join current-run release evidence without depending on development history."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from integrity import seal_file, sha256, verify_bound_artifacts, verify_file
from run_state import atomic_json, implementation_fingerprint


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results/full/main/current")
    args = parser.parse_args()
    out = args.results.resolve()
    run_root = out.parent
    evidence = run_root / "validation"
    run = json.loads((run_root / "record.json").read_text())
    require(
        run["status"] == "PASS" and run["mode"] == "full", "Need a successful complete real cohort"
    )
    current = implementation_fingerprint(ROOT)
    require(run["implementation"] == current, "Run belongs to different implementation")
    verify_bound_artifacts(run["artifacts"])
    final = evidence / "final_validation.json"
    verify_file(final)
    checked = json.loads(final.read_text())
    require(
        checked["run_id"] == run["run_id"]
        and checked["record_sha256"] == sha256(run_root / "record.json"),
        "Independent validation binding is stale",
    )
    require(
        checked["all_gene_sums_both_settings_reverified"]
        and checked["reference_counts_masks_match"],
        "Need fresh original-entry checks and reference match",
    )
    require(checked["artifacts"] == run["artifacts"], "Verification artifact binding differs")
    ci = json.loads((ROOT / "results/ci/checks.json").read_text())
    require(
        ci["status"] == "PASS" and ci["implementation"] == current,
        "Local CI is stale or incomplete",
    )
    require(
        all(
            sha256(ROOT / "results/ci" / name) == digest
            for name, digest in ci["evidence_sha256"].items()
        ),
        "CI evidence bytes changed",
    )
    cache = json.loads((evidence / "cache_full.json").read_text())
    require(
        cache["status"] == "PASS"
        and cache["current_run_id"] == run["run_id"]
        and cache["tasks_cached"] == 133,
        "Full resume proof is stale",
    )
    browser = json.loads((evidence / "browser/qa.json").read_text())
    report_hash = sha256(out / "report.html")
    require(
        browser["status"] == "PASS"
        and browser["reportSha256"] == report_hash
        and len(browser["cases"]) == 4,
        "Offline browser evidence is stale/incomplete",
    )
    visual = json.loads((evidence / "browser/visual_review.json").read_text())
    require(
        visual["status"] == "PASS"
        and visual["report_sha256"] == report_hash
        and visual["review_method"] == "manual image inspection",
        "Need a separate current manual figure/layout review",
    )
    trace = pd.read_csv(cache["execution_trace"], sep="\t")
    require(
        len(trace) == 133 and trace.status.eq("COMPLETED").all(),
        "Need actual complete 133-task execution",
    )
    heavy = trace[trace.name.str.match(r"^(CONVERT|QC|EXPORT|VERIFY_DONOR|AGGREGATE)( |$)")]
    events = []
    for row in heavy.itertuples():
        events.extend([(pd.Timestamp(row.start), 1), (pd.Timestamp(row.complete), -1)])
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(active, peak)
    require(peak == 1, "Heavy tasks overlapped")
    original = json.loads((Path(cache["execution_trace"]).parent / "command.json").read_text())
    require(original["implementation"] == current, "Complete execution ran different code")
    seconds = (
        datetime.fromisoformat(original["finished_utc"])
        - datetime.fromisoformat(original["started_utc"])
    ).total_seconds()
    summary = json.loads((out / "summary.json").read_text())
    reference = json.loads((ROOT / "assets/reference_results.json").read_text())
    reference.update(
        donors=len(reference["canonical_fingerprints"]),
        files=3 * len(reference["canonical_fingerprints"]),
        supplied_barcodes=reference["supplied"],
    )
    for key in (
        "donors",
        "files",
        "features",
        "supplied_barcodes",
        "baseline_retained",
        "strict_retained",
    ):
        require(summary[key] == reference[key], f"Reference mismatch: {key}")
    require(
        all(
            d["rank"] == 4 and d["eligible_donors"] == 26 and not d["review_flagged_donors"]
            for d in summary["design"]
        ),
        "Unexpected donor/design result",
    )
    storage = (
        int(subprocess.check_output(["du", "-sk", os.environ["IGAN_CACHE"]], text=True).split()[0])
        * 1024
    )
    require(storage < 30 * 1024**3, "Task storage exceeded 30 GiB planning limit")
    require(
        summary["peak_donor_process_rss_bytes"] < 12 * 1024**3,
        "Donor process exceeded memory request",
    )
    result = {
        "status": "PASS",
        "version": "1.0.0",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run["run_id"],
        "record_sha256": sha256(run_root / "record.json"),
        "implementation": current,
        "coverage": {
            k: summary[k]
            for k in (
                "donors",
                "files",
                "features",
                "supplied_barcodes",
                "baseline_retained",
                "strict_retained",
            )
        },
        "independent_donors_verified": 26,
        "canonical_counts_masks_match_reference": True,
        "unit_tests": ci["tests"],
        "integration_cases": ci["integration_cases"],
        "full_tasks_completed": 133,
        "resume_tasks_cached": 133,
        "offline_browser_cases": 4,
        "manual_visual_review": "PASS",
        "peak_concurrent_heavy_tasks": peak,
        "full_execution_seconds": seconds,
        "peak_donor_process_rss_bytes": summary["peak_donor_process_rss_bytes"],
        "task_storage_bytes": storage,
        "source_bytes": summary["source_bytes"],
        "runtime": run["software_versions"],
        "report_sha256": report_hash,
        "artifacts": run["artifacts"],
        "evidence_sha256": {
            str(p.relative_to(run_root)): sha256(p)
            for p in (
                final,
                evidence / "cache_full.json",
                evidence / "browser/qa.json",
                evidence / "browser/visual_review.json",
            )
        },
        "platform_status": {
            "executed": sys.platform + " " + run["software_versions"]["architecture"],
            "linux_x86_64": "configured; unverified unless separate execution evidence is supplied",
            "github_hosted_ci": "not executed locally",
        },
        "limits": "Process RSS excludes the JVM/OS; PBMC sums are composition-sensitive. Manual review is distinct from automated browser checks.",
    }
    target = evidence / "completion_audit.json"
    atomic_json(target, result)
    seal_file(target)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("implementation", "runtime", "artifacts")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
