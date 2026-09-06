#!/usr/bin/env python3
"""Install archive-verified tool releases on macOS ARM and Linux x86_64."""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

project, cache = map(Path, sys.argv[1:])
config = json.loads((project / "assets/toolchain.json").read_text())
key = platform.system().lower() + "-" + platform.machine().lower()
if key not in config["platforms"]:
    raise SystemExit(f"Unsupported platform {key}; supported: {list(config['platforms'])}")
selected = config["platforms"][key]
tools = cache / "tools"
downloads = tools / "downloads"
downloads.mkdir(parents=True, exist_ok=True)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


for name in ("java", "nextflow"):
    item = selected if name == "java" else config
    dest = downloads / (f"java-{key}.tar.gz" if name == "java" else "nextflow-dist")
    old = downloads / "java.tar.gz"
    if name == "java" and not dest.exists() and old.exists() and digest(old) == item["java_sha256"]:
        dest.symlink_to(old)
    if not dest.exists():
        partial = dest.with_suffix(".partial")
        subprocess.run(
            [
                "curl",
                "-fL",
                "--retry",
                "2",
                "--max-time",
                "600",
                "--silent",
                "--show-error",
                item[name + "_url"],
                "-o",
                str(partial),
            ],
            check=True,
        )
        partial.replace(dest)
    if digest(dest) != item[name + "_sha256"]:
        raise SystemExit(
            f"{name}: downloaded archive checksum mismatch; remove damaged archive and retry"
        )
    target = tools / name
    if name == "nextflow":
        if not target.exists() or digest(target) != item["nextflow_sha256"]:
            shutil.copyfile(dest, target)
        target.chmod(0o755)
    else:
        if not (target / selected["java_home"] / "bin/java").exists():
            target.mkdir(exist_ok=True)
            subprocess.run(
                ["tar", "-xzf", str(dest), "-C", str(target), "--strip-components=1"], check=True
            )
        # Check the installed executable, libraries and other archive members every time.
        with tarfile.open(dest, "r:gz") as archive:
            for member in archive:
                relative = Path(*Path(member.name).parts[1:])
                installed = target / relative
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        expected = hashlib.file_digest(stream, "sha256").hexdigest()
                    if not installed.is_file() or digest(installed) != expected:
                        raise SystemExit(
                            f"Installed Java integrity mismatch: {relative}; remove task-cache tools/java and bootstrap again"
                        )
                elif member.issym() and (
                    not installed.is_symlink() or installed.readlink() != Path(member.linkname)
                ):
                    raise SystemExit(f"Installed Java symlink mismatch: {relative}")
    print(f"{name}: archive and installed files verified for {key}")
