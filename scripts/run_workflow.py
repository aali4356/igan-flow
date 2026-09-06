#!/usr/bin/env python3
"""Validated Nextflow execution with atomic snapshots and content-checked resume."""

import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from contracts import check_sources, namespace, resolve_manifest, settings_contract
from integrity import bind_artifact, seal_directory, sha256, verify_bound_artifacts
from run_state import (
    atomic_json,
    atomic_link,
    check_registry,
    implementation_fingerprint,
    show_attempt,
    task_artifacts,
)


def persist_text(path, text):
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["test", "sample", "full"])
    parser.add_argument("--sample-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--run-key")
    args = parser.parse_args()
    if args.sample_id and args.mode != "sample":
        parser.error("--sample-id is only valid in sample mode")
    sample = args.sample_id or ("GSM8700986" if args.mode == "sample" else None)
    try:
        scope = namespace(args.mode, args.run_key, sample)
    except ValueError as exc:
        parser.error(str(exc))
    cache = Path(os.environ["IGAN_CACHE"]).expanduser().resolve()
    project_key = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    namespace_key = f"release-v1/{project_key}/{scope}"
    launch = cache / "launch" / namespace_key
    work = cache / "work" / namespace_key
    launch.mkdir(parents=True, exist_ok=True)
    out = ROOT / "results" / scope
    out.mkdir(parents=True, exist_ok=True)
    lock = (cache / "release-run.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another IgAN-Flow run is active in this task cache")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_root = cache / "runs" / namespace_key / stamp
    run_root.mkdir(parents=True)
    logs = run_root / "execution"
    logs.mkdir()
    prior = (
        json.loads((out / "last_success.json").read_text())
        if (out / "last_success.json").is_file()
        else None
    )
    record = {
        "schema_version": 1,
        "run_id": stamp,
        "mode": args.mode,
        "namespace": scope,
        "status": "RUNNING",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "resume_requested": args.resume,
        "run_root": str(run_root),
        "cwd": str(launch),
        "work": str(work),
    }
    show_attempt(out, run_root, record, prior)
    if scope == "full/main":
        for name in (
            "report.html",
            "summary.json",
            "tables",
            "figures",
            "cell_qc",
            "evidence",
            "settings.json",
            "FINDINGS.md",
        ):
            atomic_link(out / "current" / name, ROOT / "results" / name)
    registry = launch / "resume_tasks.json"
    try:
        threshold = (1 if args.mode == "test" else 12) * 1024**3
        if shutil.disk_usage(cache).free < threshold:
            raise ValueError(
                f"Preflight requires {threshold // 1024**3} GiB free disk for {args.mode}"
            )
        manifest = (
            args.input
            or ROOT
            / (
                "tests/fixtures/samplesheet.csv"
                if args.mode == "test"
                else "assets/samplesheet.csv"
            )
        ).resolve()
        settings = (
            args.settings
            or ROOT
            / ("tests/fixtures/settings.json" if args.mode == "test" else "assets/qc_real.json")
        ).resolve()
        config = settings_contract(settings, args.mode == "test")
        if args.mode != "test" and (
            config["expected_feature_rows"] != 36601
            or config["feature_content_sha256"]
            != "6bacbd038808fc3ccc6d340d24c48b1db76aa0b57a8fb9033e607fe1774a4a97"
        ):
            raise ValueError("Real feature contract is immutable")
        rows = resolve_manifest(
            manifest, args.mode == "test", ROOT / "assets/reference_inputs.json"
        )
        selected = (
            rows if args.mode != "sample" else [row for row in rows if row["sample_id"] == sample]
        )
        if not selected:
            raise ValueError("Requested sample is not in the frozen cohort")
        check_sources(selected, cache, allow_download=True)
        tasks = {}
        if args.resume:
            tasks = check_registry(registry, rows)
            if prior:
                verify_bound_artifacts(prior["artifacts"])
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        resolved_manifest = launch / "resolved_samplesheet.csv"
        persist_text(resolved_manifest, text.getvalue())
        version_data = json.loads(
            subprocess.check_output([sys.executable, str(ROOT / "scripts/versions.py")], text=True)
        )
        runtime = launch / "runtime.json"
        persist_text(runtime, json.dumps(version_data, indent=2, sort_keys=True) + "\n")
        fingerprint = implementation_fingerprint(ROOT)
        record.update(
            manifest_sha256=sha256(manifest),
            resolved_manifest_sha256=sha256(resolved_manifest),
            settings_sha256=sha256(settings),
            input_records=rows,
            settings=config,
            implementation=fingerprint,
            software_versions=version_data,
            manifest=str(manifest),
            settings_path=str(settings),
        )
        cmd = [
            "nextflow",
            "-log",
            str(logs / "nextflow.log"),
            "run",
            str(ROOT / "main.nf"),
            "-profile",
            "test" if args.mode == "test" else "local",
            "-ansi-log",
            "false",
            "-work-dir",
            str(work),
            "--input",
            str(resolved_manifest),
            "--settings",
            str(settings),
            "--cache",
            str(cache),
            "--run_key",
            namespace_key,
            "--runtime_manifest",
            str(runtime),
            "-with-trace",
            str(logs / "trace.tsv"),
            "-with-timeline",
            str(logs / "timeline.html"),
            "-with-report",
            str(logs / "nextflow_report.html"),
        ]
        if sample:
            cmd += ["--sample_id", sample]
        if args.resume:
            cmd += ["-resume"]
        record["command"] = cmd
        atomic_json(logs / "command.json", record)
        print(" ".join(__import__("shlex").quote(arg) for arg in cmd), flush=True)
        with (logs / "console.log").open("w") as handle:
            process = subprocess.Popen(
                cmd,
                cwd=launch,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in process.stdout:
                print(line, end="", flush=True)
                handle.write(line)
                handle.flush()
            result = process.wait()
        record["nextflow_exit_code"] = result
        new_tasks = task_artifacts(logs / "trace.tsv", work)
        tasks.update(new_tasks)
        atomic_json(registry, {"tasks": tasks, "attempt": stamp, "input_records": rows})
        if result:
            raise ValueError(f"Nextflow failed with exit code {result}; see {logs}")
        expected_names = {"VALIDATE_MANIFEST", "AGGREGATE", "REPORT"} | {
            f"{stage} ({row['sample_id']})"
            for row in selected
            for stage in ("ACQUIRE", "CONVERT", "QC", "EXPORT", "VERIFY_DONOR")
        }
        if set(new_tasks) != expected_names:
            raise ValueError("Successful trace does not cover every selected donor and stage")
        check_sources(selected, cache, allow_download=False)
        if implementation_fingerprint(ROOT) != fingerprint:
            raise ValueError("Implementation changed during execution; rerun on stable code")
        # Publish only after the current source bytes and every nested task output pass.
        portable = run_root / "portable"
        shutil.copytree(Path(new_tasks["REPORT"]["path"]), portable)
        donors = run_root / "donors"
        donors.mkdir()
        locations = []
        for row in selected:
            sid = row["sample_id"]
            target = donors / f"{sid}.export"
            shutil.copytree(Path(new_tasks[f"EXPORT ({sid})"]["path"]), target)
            locations.append(
                {
                    "sample_id": sid,
                    "baseline_h5ad": str(target / "baseline.h5ad"),
                    "donor_export_directory": str(target),
                }
            )
        with (portable / "tables/data_locations.tsv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(locations[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(locations)
        summary = json.loads((portable / "summary.json").read_text())
        summary["software_versions"] = version_data
        summary["execution"] = {
            "run_id": stamp,
            "mode": args.mode,
            "namespace": scope,
            "implementation_sha256": fingerprint["sha256"],
        }
        atomic_json(portable / "summary.json", summary)
        shutil.copyfile(settings, portable / "settings.json")
        atomic_json(portable / "evidence/runtime.json", version_data)
        record.update(
            status="PASS",
            exit_code=0,
            finished_utc=datetime.now(timezone.utc).isoformat(),
            portable=str(portable),
            donors=str(donors),
            task_trace=str(logs / "trace.tsv"),
            tasks=new_tasks,
        )
        atomic_json(portable / "evidence/run.json", record)
        seal_directory(portable)
        # Individual donor seals also bind every nested HDF5/count/metadata file.
        artifacts = [bind_artifact(portable)] + [bind_artifact(p) for p in sorted(donors.iterdir())]
        record["artifacts"] = artifacts
        atomic_json(run_root / "record.json", record)
        atomic_json(out / "last_success.json", record)
        atomic_json(out / "latest_attempt.json", record)
        atomic_link(portable, out / "current")
        atomic_json(logs / "command.json", record)
        print(f"PASS: {out / 'current/report.html'}", flush=True)
    except Exception as exc:
        record.update(
            status="FAILED",
            exit_code=1,
            finished_utc=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
        atomic_json(run_root / "record.json", record)
        atomic_json(logs / "command.json", record)
        show_attempt(out, run_root, record, prior)
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
