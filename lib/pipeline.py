#!/usr/bin/env python3
"""CLI stages orchestrated individually per donor by Nextflow."""

import argparse
import gzip
import hashlib
import io
import json
import re
import resource
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import pandas as pd
from common import gzip_check, load_json, save_json, sha256, versions, write_tsv
from integrity import seal_directory, verify_directory
from scipy import io as spio
from scipy import sparse
from threadpoolctl import threadpool_limits

KINDS = ("matrix", "features", "barcodes")


def validate_manifest(args):
    frame = pd.read_csv(args.manifest, dtype=str, keep_default_na=False)
    required = {
        "sample_id",
        "donor_id",
        "author_label",
        "group",
        "batch",
        "chemistry",
        "metadata_source_url",
        *[f"{k}_{s}" for k in KINDS for s in ("url", "bytes")],
    }
    if not required.issubset(frame.columns):
        raise ValueError(f"Manifest missing columns: {sorted(required - set(frame.columns))}")
    if frame.empty or frame[list(required)].eq("").any().any():
        raise ValueError("Manifest contains empty required metadata")
    if frame.sample_id.duplicated().any() or frame.donor_id.duplicated().any():
        raise ValueError("Duplicate sample or donor IDs")
    if not frame.sample_id.str.fullmatch(r"[A-Za-z0-9_-]+").all():
        raise ValueError("Unsafe sample identifier")
    if not set(frame.group).issubset({"Early", "Late", "Healthy"}):
        raise ValueError("Unknown author group")
    if not set(frame.batch).issubset({"First", "Second"}):
        raise ValueError("Unknown chemistry batch")
    for r in frame.to_dict("records"):
        if r["chemistry"] != {"First": "5prime_v1.1", "Second": "5prime_v2.0"}[r["batch"]]:
            raise ValueError("Chemistry and batch disagree")
        for k in KINDS:
            if int(r[k + "_bytes"]) <= 0:
                raise ValueError("Nonpositive source byte size")
            scheme = urlparse(r[k + "_url"]).scheme
            if scheme not in (("file",) if args.synthetic else ("https",)):
                raise ValueError("Source URL type conflicts with real/synthetic mode")
    if not args.synthetic:
        expected = pd.read_csv(args.research / "GSE285335_sample_manifest.tsv", sep="\t", dtype=str)
        if len(frame) != 26 or set(frame.sample_id) != set(expected.sample_id):
            raise ValueError("Real cohort must contain all 26 researched donors")
        fields = [("group", "group"), ("batch", "batch"), ("author_label", "source_title")]
        fields += [(k + "_url", k + "_url") for k in KINDS]
        fields += [(k + "_bytes", k + "_compressed_bytes") for k in KINDS]
        for r in frame.to_dict("records"):
            e = expected.set_index("sample_id").loc[r["sample_id"]]
            if r["donor_id"] != r["sample_id"] or any(r[a] != e[b] for a, b in fields):
                raise ValueError(f"Manifest conflicts with researched source: {r['sample_id']}")
        import csv

        raw = gzip.decompress(
            (args.research / "GSE285335_series_matrix.txt.gz").read_bytes()
        ).decode()
        records = list(csv.reader(io.StringIO(raw), delimiter="\t"))
        ids = next(r[1:] for r in records if r and r[0] == "!Sample_geo_accession")
        groups = next(
            r[1:]
            for r in records
            if r and r[0] == "!Sample_characteristics_ch1" and r[1].startswith("disease state:")
        )
        batches = next(
            r[1:]
            for r in records
            if r and r[0] == "!Sample_characteristics_ch1" and r[1].startswith("batch:")
        )
        aligned = {
            i: (g.split(": ", 1)[1], b.split(": ", 1)[1]) for i, g, b in zip(ids, groups, batches)
        }
        if any((r.group, r.batch) != aligned[r.sample_id] for r in frame.itertuples()):
            raise ValueError("Manifest group/batch mapping conflicts with GEO metadata")
    out = args.out
    (out / "records").mkdir(parents=True)
    selected = frame if not args.sample_id else frame[frame.sample_id == args.sample_id]
    if selected.empty:
        raise ValueError("Requested sample is not in the manifest")
    for row in selected.sort_values("sample_id").to_dict("records"):
        row["synthetic"] = args.synthetic
        row["upstream_filtering_status"] = "unknown"
        save_json(out / "records" / (row["sample_id"] + ".json"), row)
    save_json(
        out / "manifest_validation.json",
        {
            "status": "PASS",
            "dataset": "SYNTHETIC" if args.synthetic else "GSE285335",
            "manifest_donors": len(frame),
            "selected_donors": len(selected),
            "manifest_sha256": sha256(args.manifest),
            "groups": dict(Counter(frame.group)),
            "batches": dict(Counter(frame.batch)),
            "sample_ids": sorted(selected.sample_id),
        },
    )


def acquire(args):
    row = load_json(args.record)
    out = args.out
    out.mkdir(parents=True)
    provenance = []
    for kind in KINDS:
        url, expected = row[kind + "_url"], int(row[kind + "_bytes"])
        known_hash = row.get(kind + "_sha256", "")
        if url.startswith("file:"):
            source = Path(unquote(urlparse(url).path))
            if not source.is_file():
                raise ValueError(f"Missing local input: {source}")
            retrieved = datetime.now(timezone.utc).isoformat()
        else:
            if urlparse(url).hostname != "ftp.ncbi.nlm.nih.gov":
                raise ValueError("Unexpected data download host")
            folder = args.cache / "downloads" / "GSE285335" / row["sample_id"]
            folder.mkdir(parents=True, exist_ok=True)
            source = folder / Path(urlparse(url).path).name
            sidecar = source.with_suffix(source.suffix + ".json")
            if not source.exists():
                partial = source.with_suffix(source.suffix + ".partial")
                subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--retry",
                        "2",
                        "--retry-delay",
                        "2",
                        "--connect-timeout",
                        "30",
                        "--max-time",
                        "600",
                        "--silent",
                        "--show-error",
                        url,
                        "-o",
                        str(partial),
                    ],
                    check=True,
                )
                if partial.stat().st_size != expected:
                    raise ValueError(f"{kind}: download size mismatch")
                gzip_check(partial)
                h = sha256(partial)
                if known_hash and h != known_hash:
                    raise ValueError(f"{kind}: researched checksum mismatch")
                partial.replace(source)
                save_json(
                    sidecar,
                    {
                        "url": url,
                        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                        "sha256": h,
                    },
                )
            if not sidecar.exists():
                raise ValueError("Cached source is missing its provenance sidecar")
            saved = load_json(sidecar)
            if saved["url"] != url or saved["sha256"] != sha256(source):
                raise ValueError("Immutable cached source changed; investigate before reuse")
            retrieved = saved["retrieved_utc"]
        if source.stat().st_size != expected:
            raise ValueError(f"{kind}: source byte count mismatch")
        gzip_check(source)
        digest = sha256(source)
        if known_hash and digest != known_hash:
            raise ValueError(f"{kind}: input checksum mismatch")
        (out / (kind + ".gz")).symlink_to(source.resolve())
        provenance.append(
            {
                "sample_id": row["sample_id"],
                "kind": kind,
                "url": url,
                "retrieved_utc": retrieved,
                "bytes": expected,
                "sha256": digest,
                "checksum_origin": "local SHA-256; researched digest where available",
                "source_path": str(source.resolve()),
                "gzip_integrity": "PASS",
            }
        )
    save_json(out / "metadata.json", row)
    save_json(out / "provenance.json", provenance)


def read_validated_matrix(source, settings, threads):
    feature_bytes = gzip.decompress((source / "features.gz").read_bytes())
    if hashlib.sha256(feature_bytes).hexdigest() != settings["feature_content_sha256"]:
        raise ValueError("Feature content/order differs from the fixed source contract")
    genes = pd.read_csv(
        io.BytesIO(feature_bytes), sep="\t", header=None, dtype=str, keep_default_na=False
    )
    if genes.shape != (settings["expected_feature_rows"], 3):
        raise ValueError("Feature file has unexpected row count or columns")
    genes.columns = ["gene_id", "gene_symbol", "feature_type"]
    if genes.gene_id.duplicated().any() or genes.eq("").any().any():
        raise ValueError("Duplicate or empty feature identifier")
    genes.insert(0, "source_row", np.arange(1, len(genes) + 1))
    with gzip.open(source / "barcodes.gz", "rt") as handle:
        barcodes = [line.rstrip("\n\r") for line in handle]
    if not all(barcodes) or len(barcodes) != len(set(barcodes)):
        raise ValueError("Empty or duplicate barcode within donor")
    # SciPy accepts a fractional token under an integer header by truncating it.
    # Validate the source grammar before trusting the parser's integer dtype.
    integer_triplet = re.compile(rb"[ \t]*[+]?[0-9]+[ \t]+[+]?[0-9]+[ \t]+[+-]?[0-9]+[ \t]*\r?\n?")
    with gzip.open(source / "matrix.gz", "rb") as handle:
        if handle.readline().strip().lower() != b"%%matrixmarket matrix coordinate integer general":
            raise ValueError(
                "Matrix must be original coordinate integer counts, not normalized/real data"
            )
        dimensions = next(
            (line for line in handle if line.strip() and not line.lstrip().startswith(b"%")), b""
        )
        if not integer_triplet.fullmatch(dimensions):
            raise ValueError("Malformed MatrixMarket dimensions")
        rows, columns, entries = map(int, dimensions.split())
        if (rows, columns) != (len(genes), len(barcodes)) or not 0 <= entries <= rows * columns:
            raise ValueError("Matrix/feature/barcode dimensions disagree")
        seen = 0
        for line in handle:
            if not line.strip() or line.lstrip().startswith(b"%"):
                continue
            if not integer_triplet.fullmatch(line):
                raise ValueError("Matrix entries must contain three complete integer tokens")
            seen += 1
        if seen != entries:
            raise ValueError("Declared MatrixMarket entry count differs from source records")
    with threadpool_limits(limits=threads), gzip.open(source / "matrix.gz", "rb") as handle:
        coo = spio.mmread(handle)
    if coo.shape != (len(genes), len(barcodes)):
        raise ValueError(f"Matrix/feature/barcode dimensions disagree: {coo.shape}")
    if not sparse.issparse(coo) or coo.dtype.kind not in "iu" or (coo.data < 0).any():
        raise ValueError("Expression values must be nonnegative integer sparse counts")
    stored = coo.nnz
    if stored and int(coo.data.max()) > np.iinfo(np.int64).max // stored:
        raise ValueError("Count range cannot guarantee overflow-free int64 aggregation")
    # CSR conversion merges repeated coordinates: detect the difference before removing zeros.
    matrix = coo.tocsr().astype(np.int64, copy=False)
    if matrix.nnz != stored:
        raise ValueError("Repeated sparse coordinates are not allowed")
    matrix.eliminate_zeros()
    keep = genes.feature_type.eq("Gene Expression").to_numpy()
    if not keep.any():
        raise ValueError("No Gene Expression features")
    matrix = matrix[keep, :].tocsr()
    genes = genes.loc[keep].reset_index(drop=True)
    if not genes.gene_symbol.str.startswith("MT-").any():
        raise ValueError("Missing mitochondrial feature annotation")
    if not settings["synthetic"] and not keep.all():
        raise ValueError("Real source feature types changed")
    return matrix, genes, barcodes, stored, int((~keep).sum())


def convert(args):
    verify_directory(args.source)
    config = load_json(args.settings)
    matrix, genes, barcodes, stored, excluded = read_validated_matrix(
        args.source, config, args.threads
    )
    out = args.out
    out.mkdir(parents=True)
    sparse.save_npz(out / "counts.npz", matrix)
    write_tsv(genes, out / "genes.tsv")
    write_tsv(pd.DataFrame({"original_barcode": barcodes}), out / "barcodes.tsv")
    for name in ("metadata.json", "provenance.json", "task_metrics.json"):
        shutil.copyfile(
            args.source / name,
            out / ("acquisition_metrics.json" if name == "task_metrics.json" else name),
        )
    save_json(
        out / "input_validation.json",
        {
            "status": "PASS",
            "feature_rows": matrix.shape[0],
            "supplied_barcodes": matrix.shape[1],
            "source_stored_entries": stored,
            "nonzero_entries": matrix.nnz,
            "excluded_non_gene_features": excluded,
            "count_dtype": str(matrix.dtype),
            "feature_content_sha256": config["feature_content_sha256"],
            "checks": [
                "gzip CRC",
                "dimensions",
                "integer_token_grammar",
                "integer_nonnegative_values",
                "unique_sparse_coordinates",
                "unique_barcodes",
                "unique_feature_keys",
                "fixed_feature_mapping",
                "mitochondrial_annotation",
            ],
        },
    )


def metrics_and_masks(matrix, genes, config):
    total = np.asarray(matrix.sum(axis=0, dtype=np.int64)).ravel()
    detected = np.asarray((matrix > 0).sum(axis=0)).ravel()
    mt = genes.gene_symbol.str.startswith("MT-").to_numpy()
    mito = np.asarray(matrix[mt].sum(axis=0, dtype=np.int64)).ravel()
    percent = np.divide(mito * 100.0, total, out=np.full(len(total), np.nan), where=total > 0)
    df = pd.DataFrame(
        {
            "total_counts": total,
            "n_features": detected,
            "mitochondrial_counts": mito,
            "mitochondrial_pct": percent,
        }
    )
    return apply_masks(df, config)


def apply_masks(df, config):
    df = df.copy()
    for name in ("baseline", "strict"):
        c = config[name]
        flags = {
            "zero_counts": df.total_counts.eq(0),
            "low_features": df.n_features.lt(c["min_features"]),
            "high_features": df.n_features.gt(c["max_features"]),
            "high_mito": df.mitochondrial_pct.gt(c["max_mito_pct"]),
        }
        for reason, values in flags.items():
            df[name + "_reject_" + reason] = values
        df[name + "_keep"] = ~np.logical_or.reduce(list(flags.values()))
    if (df.strict_keep & ~df.baseline_keep).any():
        raise ValueError("Strict QC is not a subset of baseline QC")
    return df


def qc(args):
    verify_directory(args.source)
    config, row = load_json(args.settings), load_json(args.source / "metadata.json")
    matrix = sparse.load_npz(args.source / "counts.npz")
    genes = pd.read_csv(
        args.source / "genes.tsv", sep="\t", dtype={"gene_id": str, "gene_symbol": str}
    )
    bars = pd.read_csv(args.source / "barcodes.tsv", sep="\t", dtype=str).original_barcode.tolist()
    frame = metrics_and_masks(matrix, genes, config)
    frame.insert(0, "original_barcode", bars)
    frame.insert(0, "cell_id", [row["sample_id"] + ":" + b for b in bars])
    for k in ("sample_id", "donor_id", "group", "batch", "chemistry"):
        frame[k] = row[k]
    args.out.mkdir(parents=True)
    write_tsv(frame, args.out / "cell_qc.tsv.gz")
    summary = []
    for setting in ("baseline", "strict"):
        retained = frame[setting + "_keep"]
        n = int(retained.sum())
        info = {
            k: row[k]
            for k in (
                "sample_id",
                "donor_id",
                "author_label",
                "group",
                "batch",
                "chemistry",
                "upstream_filtering_status",
            )
        }
        info.update(
            setting=setting,
            supplied=len(frame),
            retained=n,
            rejected_union=len(frame) - n,
            retention=n / len(frame) if len(frame) else 0.0,
            input_umi=int(frame.total_counts.sum()),
            retained_umi=int(frame.loc[retained, "total_counts"].sum()),
            low_cell_flag=n < config["review_min_cells"],
            low_retention_flag=n / len(frame) < config["review_min_retention"]
            if len(frame)
            else True,
            status="zero_cells" if n == 0 else "retained",
        )
        for reason in ("zero_counts", "low_features", "high_features", "high_mito"):
            info["rejected_" + reason] = int(frame[setting + "_reject_" + reason].sum())
        for suffix, sub in [("input", frame), ("retained", frame.loc[retained])]:
            for metric in ("n_features", "mitochondrial_pct"):
                for qname, q in [("q25", 0.25), ("median", 0.5), ("q75", 0.75)]:
                    value = sub[metric].quantile(q)
                    info[f"{suffix}_{metric}_{qname}"] = None if pd.isna(value) else float(value)
        summary.append(info)
    save_json(args.out / "qc_summary.json", summary)
    save_json(args.out / "settings.json", config)


def export(args):
    verify_directory(args.source)
    verify_directory(args.qc)
    import anndata as ad

    out = args.out
    out.mkdir(parents=True)
    matrix = sparse.load_npz(args.source / "counts.npz")
    genes = pd.read_csv(
        args.source / "genes.tsv", sep="\t", dtype={"gene_id": str, "gene_symbol": str}
    )
    frame = pd.read_csv(args.qc / "cell_qc.tsv.gz", sep="\t")
    counts = genes.copy()
    for setting in ("baseline", "strict"):
        mask = frame[setting + "_keep"].to_numpy(dtype=bool)
        counts[setting] = np.asarray(matrix[:, mask].sum(axis=1, dtype=np.int64)).ravel()
    write_tsv(counts, out / "counts.tsv")
    baseline = frame.baseline_keep.to_numpy(dtype=bool)
    obs = frame.loc[baseline].set_index("cell_id")
    adata = ad.AnnData(
        X=matrix[:, baseline].T.tocsr(), obs=obs, var=genes.set_index("gene_id", drop=False)
    )
    adata.uns["parameters_json"] = json.dumps(load_json(args.qc / "settings.json"), sort_keys=True)
    adata.uns["source_provenance_json"] = json.dumps(
        load_json(args.source / "provenance.json"), sort_keys=True
    )
    adata.uns["software_versions"] = versions()
    adata.uns["counts_convention"] = (
        "X contains original integer UMI counts; no normalization or log transform"
    )
    adata.uns["upstream_filtering_status"] = "unknown"
    adata.write_h5ad(out / "baseline.h5ad", compression="gzip")
    for name in ("metadata.json", "provenance.json", "input_validation.json"):
        shutil.copyfile(args.source / name, out / name)
    for name in ("cell_qc.tsv.gz", "qc_summary.json", "settings.json"):
        shutil.copyfile(args.qc / name, out / name)
    write_tsv(genes, out / "genes.tsv")
    prior = [
        load_json(args.source / "acquisition_metrics.json"),
        load_json(args.source / "task_metrics.json"),
        load_json(args.qc / "task_metrics.json"),
    ]
    save_json(out / "prior_task_metrics.json", prior)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    v = sub.add_parser("manifest")
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--research", type=Path)
    v.add_argument("--sample-id", default="")
    v.add_argument("--synthetic", action="store_true")
    d = sub.add_parser("acquire")
    d.add_argument("--record", type=Path, required=True)
    d.add_argument("--cache", type=Path, required=True)
    c = sub.add_parser("convert")
    q = sub.add_parser("qc")
    e = sub.add_parser("export")
    for p in (c, q, e):
        p.add_argument("--source", type=Path, required=True)
    for p in (c, q):
        p.add_argument("--settings", type=Path, required=True)
    e.add_argument("--qc", type=Path, required=True)
    for p in (v, d, c, q, e):
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    start = time.monotonic()
    {
        "manifest": validate_manifest,
        "acquire": acquire,
        "convert": convert,
        "qc": qc,
        "export": export,
    }[args.stage](args)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    metrics = {
        "stage": args.stage,
        "elapsed_seconds": time.monotonic() - start,
        "peak_rss_bytes": int(
            usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        ),
        "user_cpu_seconds": usage.ru_utime,
        "system_cpu_seconds": usage.ru_stime,
        "requested_threads": args.threads,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_json(args.out / "task_metrics.json", metrics)
    seal_directory(args.out)
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
