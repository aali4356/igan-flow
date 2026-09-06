"""Small I/O helpers shared by processing stages, not by the independent verifier."""

import gzip
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text())


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n")


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def gzip_check(path):
    with gzip.open(path, "rb") as handle:
        while handle.read(1024 * 1024):
            pass


def write_tsv(frame, path):
    path = Path(path)
    frame.to_csv(
        path,
        sep="\t",
        index=False,
        float_format="%.12g",
        compression={"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None,
    )


def versions():
    return {
        "python": platform.python_version(),
        **{
            k: importlib.metadata.version(k)
            for k in ("numpy", "scipy", "pandas", "anndata", "h5py", "threadpoolctl")
        },
    }
