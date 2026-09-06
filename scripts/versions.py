#!/usr/bin/env python3
"""Verify pinned runtime versions and executable integrity; emit actual provenance."""

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "assets/toolchain.json").read_text())
if platform.python_version_tuple()[:2] != tuple(config["python_minor"].split(".")):
    raise SystemExit("Python 3.11 is required")
packages = {}
for lock in ("requirements.lock", "requirements-dev.lock"):
    for line in (root / lock).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise SystemExit(
                f"Installed {name}={actual} differs from lock {expected}; bootstrap again"
            )
        packages[name] = actual
java = subprocess.run(
    ["java", "-version"], capture_output=True, text=True, check=True
).stderr.strip()
nf = subprocess.check_output(["nextflow", "-version"], text=True).strip()
if config["java_version"] not in java or config["nextflow_version"] not in nf:
    raise SystemExit("Installed Java/Nextflow versions differ from toolchain lock")
cache = Path(os.environ["IGAN_CACHE"])
with (cache / "tools/nextflow").open("rb") as handle:
    nf_hash = hashlib.file_digest(handle, "sha256").hexdigest()
if nf_hash != config["nextflow_sha256"]:
    raise SystemExit("Installed Nextflow integrity mismatch; bootstrap again")
environment = {
    k: os.environ.get(k)
    for k in (
        "NXF_VER",
        "NXF_OFFLINE",
        "NXF_DISABLE_CHECK_LATEST",
        "NXF_OPTS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "MPLBACKEND",
    )
}
print(
    json.dumps(
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "java": java,
            "nextflow": nf,
            "nextflow_sha256": nf_hash,
            "packages": packages,
            "environment": environment,
        },
        indent=2,
        sort_keys=True,
    )
)
