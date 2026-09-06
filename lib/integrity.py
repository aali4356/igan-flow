"""Content seals for nested task outputs and immutable published snapshots."""

import hashlib
import json
from pathlib import Path

SEAL = ".integrity.json"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inventory(folder):
    folder = Path(folder)
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.name == SEAL:
            continue
        if path.is_symlink() and not path.exists():
            raise ValueError(f"Missing integrity input: {path}")
        if path.is_file():
            result[str(path.relative_to(folder))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    return result


def seal_directory(folder):
    folder = Path(folder)
    value = {"schema_version": 1, "files": inventory(folder)}
    (folder / SEAL).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return value


def verify_directory(folder):
    folder = Path(folder)
    seal = folder / SEAL
    if not seal.is_file():
        raise ValueError(f"Missing integrity seal: {folder}")
    expected = json.loads(seal.read_text())
    actual = inventory(folder)
    if expected.get("schema_version") != 1 or not isinstance(expected.get("files"), dict):
        raise ValueError(f"Invalid integrity seal: {folder}")
    if expected["files"] != actual:
        changed = sorted(
            k
            for k in set(expected["files"]) | set(actual)
            if expected["files"].get(k) != actual.get(k)
        )
        raise ValueError(
            f"Artifact integrity mismatch in {folder}: {', '.join(changed[:5])}. Run without --resume to rebuild task outputs; investigate changed source data."
        )
    return sha256(seal)


def seal_file(path):
    path = Path(path)
    path.with_suffix(path.suffix + ".sha256").write_text(sha256(path) + "\n")


def verify_file(path):
    path = Path(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file() or sidecar.read_text().strip() != sha256(path):
        raise ValueError(f"Artifact integrity mismatch: {path}")
    return sha256(sidecar)


def verify_bound_artifacts(entries):
    for entry in entries:
        path = Path(entry["path"])
        digest = verify_directory(path) if entry["kind"] == "directory" else verify_file(path)
        if digest != entry["seal_sha256"]:
            raise ValueError(f"Artifact seal changed since validation: {path}")


def bind_artifact(path):
    path = Path(path).absolute()
    is_dir = path.is_dir()
    return {
        "path": str(path),
        "kind": "directory" if is_dir else "file",
        "seal_sha256": verify_directory(path) if is_dir else verify_file(path),
    }
