"""Atomic run publication, attempt records, and declared-output integrity registry."""

import csv
import hashlib
import html
import json
import os
import re
import uuid
from pathlib import Path

from integrity import bind_artifact, verify_bound_artifacts


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def atomic_link(target, link):
    target, link = Path(target).absolute(), Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise ValueError(f"Refusing to replace an ordinary path with a result pointer: {link}")
    temp = link.with_name(link.name + "." + uuid.uuid4().hex + ".tmp")
    temp.symlink_to(target, target_is_directory=target.is_dir())
    os.replace(temp, link)


def show_attempt(out, run_root, record, previous=None):
    status = run_root / "status"
    status.mkdir(parents=True, exist_ok=True)
    previous_link = ""
    if previous:
        previous_link = f'<p>Historical result: <a href="{html.escape(Path(previous["portable"]).as_uri() + "/report.html")}">last successful run {html.escape(previous["run_id"])}</a>. It is not the current attempt.</p>'
    (status / "report.html").write_text(
        f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>IgAN-Flow {record["status"]}</title><main><h1>IgAN-Flow: {record["status"]}</h1><p>Attempt {html.escape(record["run_id"])}</p><p>{html.escape(record.get("error", "Processing and integrity verification are in progress."))}</p>{previous_link}</main></html>'
    )
    atomic_json(status / "summary.json", record)
    atomic_json(out / "latest_attempt.json", record)
    atomic_link(status, out / "current")


def implementation_fingerprint(root):
    names = {
        "main.nf",
        "nextflow.config",
        "requirements.in",
        "requirements.lock",
        "requirements-dev.lock",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
    }
    paths = [root / name for name in names if (root / name).is_file()]
    paths.extend(
        root / "research" / name
        for name in ("GSE285335_sample_manifest.tsv", "GSE285335_series_matrix.txt.gz")
    )
    for directory in ("lib", "scripts", "modules", "conf", "assets", "tests", ".github"):
        paths.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and ".pyc" != p.suffix
        )
    fingerprints = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)
    }
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": fingerprints}


def task_artifacts(trace_path, work):
    if not Path(trace_path).is_file():
        return {}
    result = {}
    with Path(trace_path).open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        if row["status"] not in ("COMPLETED", "CACHED"):
            continue
        name = row["name"]
        match = re.fullmatch(r"(\w+)(?: \(([A-Za-z0-9_-]+)\))?", name)
        if not match:
            raise ValueError(f"Unexpected task identity in trace: {name}")
        stage, sid = match.groups()
        prefix, suffix = row["hash"].split("/")
        candidates = list((Path(work) / prefix).glob(suffix + "*"))
        if len(candidates) != 1:
            raise ValueError(f"Ambiguous or missing task directory: {name}")
        filename = {
            "VALIDATE_MANIFEST": "manifest",
            "ACQUIRE": f"{sid}.inputs",
            "CONVERT": f"{sid}.validated",
            "QC": f"{sid}.qc",
            "EXPORT": f"{sid}.export",
            "VERIFY_DONOR": f"{sid}.verification.json",
            "AGGREGATE": "summary",
            "REPORT": "final",
        }[stage]
        result[name] = bind_artifact(candidates[0] / filename)
    return result


def check_registry(path, rows=None):
    if Path(path).is_file():
        value = json.loads(Path(path).read_text())
        old = {r["sample_id"]: r for r in value.get("input_records", [])}
        changed = {r["sample_id"] for r in (rows or []) if old.get(r["sample_id"]) != r}
        # Local ACQUIRE directories contain source symlinks. A newly declared,
        # preflight-verified input legitimately changes these bytes and forces
        # Nextflow to recompute that donor. Other cached products stay checked.
        verify_bound_artifacts(
            [
                entry
                for name, entry in value["tasks"].items()
                if name not in {f"ACQUIRE ({sid})" for sid in changed}
            ]
        )
        return value["tasks"]
    return {}
