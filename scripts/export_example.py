#!/usr/bin/env python3
"""Export a compact, path-sanitized public example from a sealed release run."""

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from integrity import seal_directory, sha256, verify_bound_artifacts, verify_file
from run_state import atomic_json
from verify_results import Assets


def sanitize(value):
    if isinstance(value, dict):
        return {
            k: sanitize(v)
            for k, v in value.items()
            if k not in ("source_path", "manifest_path", "research_path")
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("file:")):
        return "[local path omitted; regenerate with the documented workflow]"
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results/full/main/current")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/example")
    args = parser.parse_args()
    source = args.results.resolve()
    out = args.out.resolve()
    run = json.loads((source.parent / "record.json").read_text())
    verify_bound_artifacts(run["artifacts"])
    checked = source.parent / "validation/completion_audit.json"
    verify_file(checked)
    audit = json.loads(checked.read_text())
    if audit["run_id"] != run["run_id"] or audit["report_sha256"] != sha256(source / "report.html"):
        raise ValueError("Release audit is stale")
    if out.exists():
        if out != ROOT / "docs/example":
            raise ValueError("Refusing to replace an existing custom output directory")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for folder in ("tables", "figures", "evidence"):
        shutil.copytree(source / folder, out / folder)
    for name in ("report.html", "FINDINGS.md", "summary.json", "settings.json"):
        shutil.copyfile(source / name, out / name)
    for name in ("run.json", "runtime.json"):
        (out / "evidence" / name).unlink(missing_ok=True)
    for path in out.rglob("*.json"):
        atomic_json(path, sanitize(json.loads(path.read_text())))
    for path in (out / "tables").glob("*.tsv"):
        with path.open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
            handle.seek(0)
            fields = next(csv.reader(handle, delimiter="\t"))
        fields = [name for name in fields if name != "source_path"]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows({k: sanitize(r[k]) for k in fields} for r in rows)
    public = {
        k: audit[k]
        for k in (
            "status",
            "version",
            "completed_utc",
            "run_id",
            "implementation",
            "coverage",
            "independent_donors_verified",
            "canonical_counts_masks_match_reference",
            "unit_tests",
            "integration_cases",
            "full_tasks_completed",
            "resume_tasks_cached",
            "offline_browser_cases",
            "manual_visual_review",
            "peak_concurrent_heavy_tasks",
            "full_execution_seconds",
            "peak_donor_process_rss_bytes",
            "task_storage_bytes",
            "source_bytes",
            "runtime",
            "report_sha256",
            "platform_status",
            "limits",
        )
    }
    public["source_completion_audit_sha256"] = sha256(checked)
    public["scope"] = (
        "Sanitized release summary. Absolute run/source locations and detailed local logs are omitted."
    )
    atomic_json(out / "evidence/release_summary.json", public)
    (out / "REPRODUCE.md").write_text(
        "# Reproduce this example\n\nFrom the repository root, run `bash scripts/bootstrap.sh`, `bash scripts/run.sh full`, `bash scripts/check_reproducibility.sh`, then `bash scripts/verify.sh`. Complete offline browser and manual figure review as described in the release record, run `python scripts/audit_completion.py`, then `python scripts/export_example.py` after sourcing `scripts/env.sh`.\n\nThis bundle retains the full donor summaries and combined integer count tables. It omits per-cell QC, AnnData, raw downloads, local run logs and filesystem locations. Regeneration requires approximately 1.59 GB of public expression downloads. Dates, runtime measurements and run IDs vary; biological count/mask fingerprints must match the frozen reference.\n"
    )
    parsed = Assets()
    parsed.feed((out / "report.html").read_text())
    for link in parsed.links:
        if "://" not in link and not link.startswith("#") and not (out / link).is_file():
            raise ValueError(f"Missing public report link {link}")
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in (".html", ".json", ".tsv", ".md", ".svg"):
            if re.search(r"/Users/|/home/|OneDrive|file://", path.read_text()):
                raise ValueError(f"Private path leaked into example: {path.name}")
    seal_directory(out)
    print(f"PASS: sanitized example at {out}")


if __name__ == "__main__":
    main()
