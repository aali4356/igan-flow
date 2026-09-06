"""Independent verification from original COO entries, without production helpers."""

import argparse
import gzip
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from integrity import seal_file
from scipy import sparse
from scipy.io import mmread
from threadpoolctl import threadpool_limits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--export", type=Path, required=True)
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args()
    start = time.monotonic()
    config = json.loads(args.settings.read_text())
    meta = json.loads((args.source / "metadata.json").read_text())
    with (
        threadpool_limits(limits=args.threads),
        gzip.open(args.source / "matrix.gz", "rb") as handle,
    ):
        raw = mmread(handle).tocoo()
    if not (raw.dtype.kind in "iu" and np.all(raw.data >= 0)):
        raise ValueError("Original count values invalid")
    genes = pd.read_csv(
        args.source / "features.gz",
        compression="gzip",
        sep="\t",
        header=None,
        dtype=str,
        keep_default_na=False,
    )
    with gzip.open(args.source / "barcodes.gz", "rt") as handle:
        bars = [b.strip() for b in handle]
    if not raw.shape == (len(genes), len(bars)):
        raise ValueError("Source orientation/dimensions mismatch")
    if not len(set(bars)) == len(bars):
        raise ValueError("Source within-donor duplicate barcodes")
    if not genes[0].is_unique:
        raise ValueError("Source feature IDs must be unique")
    feature_mask = genes[2].eq("Gene Expression").to_numpy()
    if not feature_mask.all():
        raise ValueError("Frozen real and test sources must contain only Gene Expression")
    mito_features = genes[1].str.startswith("MT-").to_numpy()
    if not mito_features.any():
        raise ValueError("Mitochondrial annotation unavailable")
    qc = pd.read_csv(args.export / "cell_qc.tsv.gz", sep="\t")
    if not qc.original_barcode.tolist() == bars:
        raise ValueError("QC barcode order mismatch")
    if not qc.cell_id.tolist() == [meta["sample_id"] + ":" + b for b in bars]:
        raise ValueError("Cell namespace mismatch")
    for key in ("sample_id", "donor_id", "group", "batch", "chemistry"):
        if not qc[key].eq(meta[key]).all():
            raise ValueError(f"QC {key} differs from source")
    sums = np.zeros(len(bars), dtype=np.int64)
    np.add.at(sums, raw.col, raw.data)
    nfeatures = np.bincount(raw.col[raw.data > 0], minlength=len(bars))
    mt_sums = np.zeros(len(bars), dtype=np.int64)
    use = mito_features[raw.row]
    np.add.at(mt_sums, raw.col[use], raw.data[use])
    pct = np.full(len(bars), np.nan)
    nonzero = sums > 0
    pct[nonzero] = 100 * mt_sums[nonzero] / sums[nonzero]
    np.testing.assert_array_equal(qc.total_counts, sums)
    np.testing.assert_array_equal(qc.n_features, nfeatures)
    np.testing.assert_array_equal(qc.mitochondrial_counts, mt_sums)
    np.testing.assert_allclose(qc.mitochondrial_pct, pct, rtol=1e-10, atol=1e-09, equal_nan=True)
    counts = pd.read_csv(
        args.export / "counts.tsv",
        sep="\t",
        dtype={"gene_id": str, "gene_symbol": str},
        keep_default_na=False,
    )
    if not counts.gene_id.tolist() == genes[0].tolist():
        raise ValueError("Counts gene mapping differs from source")
    if not counts.gene_symbol.tolist() == genes[1].tolist():
        raise ValueError("Gene annotation changed")
    summary = json.loads((args.export / "qc_summary.json").read_text())
    masks = {}
    fingerprints = {}
    for setting in ("baseline", "strict"):
        c = config[setting]
        reasons = {
            "zero_counts": sums == 0,
            "low_features": nfeatures < c["min_features"],
            "high_features": nfeatures > c["max_features"],
            "high_mito": pct > c["max_mito_pct"],
        }
        keep = (
            nonzero
            & (nfeatures >= c["min_features"])
            & (nfeatures <= c["max_features"])
            & (pct <= c["max_mito_pct"])
        )
        masks[setting] = keep
        np.testing.assert_array_equal(qc[setting + "_keep"], keep)
        for reason, expected in reasons.items():
            np.testing.assert_array_equal(qc[setting + "_reject_" + reason], expected)
        expected = np.zeros(len(genes), dtype=np.int64)
        select = keep[raw.col]
        np.add.at(expected, raw.row[select], raw.data[select])
        if counts[setting].dtype.kind not in "iu":
            raise ValueError("Export is not integer counts")
        np.testing.assert_array_equal(counts[setting], expected)
        item = next((s for s in summary if s["setting"] == setting))
        if not (item["supplied"] == len(bars) and item["retained"] == int(keep.sum())):
            raise ValueError("Independent verification failed at check 84")
        if not item["supplied"] == item["retained"] + item["rejected_union"]:
            raise ValueError("Independent verification failed at check 85")
        if not item["retained_umi"] == int(expected.sum()):
            raise ValueError("Independent verification failed at check 86")
        fingerprints[setting + "_counts"] = hashlib.sha256(
            expected.astype("<i8").tobytes()
        ).hexdigest()
        fingerprints[setting + "_mask"] = hashlib.sha256(keep.tobytes()).hexdigest()
    if not not (masks["strict"] & ~masks["baseline"]).any():
        raise ValueError("Independent verification failed at check 89")
    data = ad.read_h5ad(args.export / "baseline.h5ad")
    if not (sparse.issparse(data.X) and data.X.dtype.kind in "iu"):
        raise ValueError("h5ad does not preserve sparse integer counts")
    expected_matrix = raw.tocsr()[:, masks["baseline"]].T.tocsr()
    if not data.shape == expected_matrix.shape:
        raise ValueError("Independent verification failed at check 93")
    if not (data.X != expected_matrix).nnz == 0:
        raise ValueError("h5ad count/orientation mismatch")
    if not data.obs_names.tolist() == qc.loc[masks["baseline"], "cell_id"].tolist():
        raise ValueError("Independent verification failed at check 95")
    if not data.var_names.tolist() == genes[0].tolist():
        raise ValueError("Independent verification failed at check 96")
    for key in ("sample_id", "donor_id", "group", "batch", "chemistry", "original_barcode"):
        if (
            not data.obs[key].astype(str).tolist()
            == qc.loc[masks["baseline"], key].astype(str).tolist()
        ):
            raise ValueError("Independent verification failed at check 98")
    if not json.loads(data.uns["parameters_json"]) == config:
        raise ValueError("Independent verification failed at check 99")
    provenance = json.loads((args.source / "provenance.json").read_text())
    if not json.loads(data.uns["source_provenance_json"]) == provenance:
        raise ValueError("Independent verification failed at check 101")
    for item in provenance:
        with (args.source / (item["kind"] + ".gz")).open("rb") as handle:
            if not hashlib.file_digest(handle, "sha256").hexdigest() == item["sha256"]:
                raise ValueError("Independent verification failed at check 104")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "sample_id": meta["sample_id"],
        "status": "PASS",
        "features": len(genes),
        "supplied": len(bars),
        "baseline_retained": int(masks["baseline"].sum()),
        "strict_retained": int(masks["strict"].sum()),
        "checks": [
            "source_hashes",
            "cell_identity",
            "independent_metrics",
            "all_reason_flags",
            "strict_subset",
            "all_gene_count_sums_both_settings",
            "attrition_accounting",
            "h5ad_sparse_integer_roundtrip",
            "h5ad_identity_and_provenance",
        ],
        "canonical_fingerprints": fingerprints,
        "elapsed_seconds": time.monotonic() - start,
        "peak_rss_bytes": int(
            usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        ),
    }
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    seal_file(args.out)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
