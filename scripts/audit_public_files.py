#!/usr/bin/env python3
"""Review the exact staged/tracked Git file set for publication mistakes."""

import argparse
import gzip
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from integrity import sha256
from run_state import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="Audit committed/tracked files; default also uses the current Git index",
    )
    parser.parse_args()
    git_root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True
        ).strip()
    )
    if git_root != ROOT:
        raise ValueError("Git root is outside this project")
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    forbidden = {"results", "private", "review", "work", ".venv", "node_modules", "__pycache__"}
    private_docs = {
        "PROJECT_GUIDELINE.md",
        "GOAL_PROMPT.md",
        "FINAL_GOAL_PROMPT.md",
        "RELEASE_REVIEW.md",
        "RESEARCH.md",
        "PLAN.md",
        "ACCEPTANCE.md",
        "DECISIONS.md",
        "FINAL_RELEASE_PLAN.md",
    }
    issues = []
    files = []
    patterns = [
        r"/Users/[A-Za-z0-9._-]+/",
        r"/home/[A-Za-z0-9._-]+/",
        r"file:///(?:Users|home)/[A-Za-z0-9._-]+/",
        r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{40,}",
        r"AKIA[0-9A-Z]{16}",
        r"sk-[A-Za-z0-9]{32,}",
    ]
    for name in filter(None, names):
        path = ROOT / name
        if any(part in forbidden for part in path.relative_to(ROOT).parts) or name in private_docs:
            issues.append(f"Excluded file staged: {name}")
        if (
            path.is_symlink()
            or path.suffix in (".h5ad", ".npz", ".mtx", ".bam", ".fastq")
            or path.stat().st_size > 5 * 1024**2
        ):
            issues.append(f"Oversized/derived/symlink artifact: {name}")
        data = path.read_bytes()
        try:
            text = (gzip.decompress(data) if path.suffix == ".gz" else data).decode()
        except (UnicodeDecodeError, OSError):
            text = ""
        # The audit script intentionally contains detection patterns, never real secrets.
        if name != "scripts/audit_public_files.py" and any(
            re.search(pattern, text) for pattern in patterns
        ):
            issues.append(f"Personal path or credential pattern: {name}")
        files.append({"path": name, "bytes": len(data), "sha256": sha256(path)})
    if not files:
        issues.append("Empty public Git index")
    result = {
        "status": "FAILED" if issues else "PASS",
        "file_count": len(files),
        "total_bytes": sum(r["bytes"] for r in files),
        "files": files,
        "issues": issues,
        "scope": "Exact current Git index paths; manual review remains separate",
    }
    atomic_json(ROOT / "results/release/public_files.json", result)
    if issues:
        raise ValueError("\n".join(issues))
    print(
        f"PASS: {len(files)} public files, {result['total_bytes']:,} bytes; no configured publication-error patterns found"
    )


if __name__ == "__main__":
    main()
