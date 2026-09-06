#!/usr/bin/env python3
"""Combine only verified donor-level exports and build descriptive audit tables."""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from common import load_json, save_json, versions, write_tsv
from integrity import seal_directory, verify_directory, verify_file


def design_audit(audit):
    output = []
    columns = ["intercept", "batch_2", "group_Early", "group_Late"]
    for setting in ("baseline", "strict"):
        all_rows = audit[audit.setting == setting]
        eligible = all_rows[all_rows.retained > 0]
        design = np.column_stack(
            [
                np.ones(len(eligible)),
                eligible.batch.eq("Second"),
                eligible.group.eq("Early"),
                eligible.group.eq("Late"),
            ]
        ).astype(float)
        rank = int(np.linalg.matrix_rank(design)) if len(eligible) else None
        output.append(
            {
                "setting": setting,
                "expected_columns": columns,
                "rank": rank,
                "expected_rank": 4,
                "eligible_donors": len(eligible),
                "excluded_zero_library": all_rows.loc[all_rows.retained == 0, "sample_id"].tolist(),
                "review_flagged_donors": all_rows.loc[
                    all_rows.low_cell_flag | all_rows.low_retention_flag, "sample_id"
                ].tolist(),
                "missing_required_metadata": int(
                    all_rows[["group", "batch", "donor_id"]].isna().sum().sum()
                ),
                "status": "unavailable"
                if rank is None
                else ("full_rank" if rank == 4 else "rank_deficient"),
                "interpretation": "Estimability check only; no biological, causal, or clinical validation.",
            }
        )
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--donors", nargs="+", type=Path, required=True)
    p.add_argument("--verifications", nargs="+", type=Path, required=True)
    p.add_argument("--manifest-audit", type=Path, required=True)
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--large-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    for donor in args.donors:
        verify_directory(donor)
    for check in args.verifications:
        verify_file(check)
    audit_manifest = load_json(args.manifest_audit)
    settings = load_json(args.settings)
    donors = {load_json(d / "metadata.json")["sample_id"]: d for d in args.donors}
    verified = {load_json(v)["sample_id"]: load_json(v) for v in args.verifications}
    ids = sorted(audit_manifest["sample_ids"])
    if (
        len(donors) != len(args.donors)
        or len(verified) != len(args.verifications)
        or set(ids) != set(donors)
        or set(ids) != set(verified)
    ):
        raise ValueError("Aggregate donor identities/coverage do not match the manifest exactly")
    if any(v["status"] != "PASS" for v in verified.values()):
        raise ValueError("A donor failed independent verification")
    out = args.out
    for folder in ("tables", "cell_qc", "evidence/donors"):
        (out / folder).mkdir(parents=True, exist_ok=True)
    summaries, provenance, metrics, locations = [], [], [], []
    tables = {}
    genes = None
    for sid in ids:
        d = donors[sid]
        count = (
            pd.read_csv(
                d / "counts.tsv",
                sep="\t",
                dtype={"gene_id": str, "gene_symbol": str},
                keep_default_na=False,
            )
            .set_index("gene_id", drop=False)
            .sort_index()
        )
        mapping = count[["source_row", "gene_id", "gene_symbol", "feature_type"]].reset_index(
            drop=True
        )
        if genes is None:
            genes = mapping
        elif not mapping.equals(genes):
            raise ValueError("Donor gene identities/annotations differ during aggregation")
        tables[sid] = count
        summary = load_json(d / "qc_summary.json")
        for item in summary:
            item["retained_genes"] = int(count[item["setting"]].gt(0).sum())
        summaries += summary
        provenance += load_json(d / "provenance.json")
        prior = load_json(d / "prior_task_metrics.json") + [load_json(d / "task_metrics.json")]
        metrics += [{"sample_id": sid, **m} for m in prior]
        metrics.append(
            {
                "sample_id": sid,
                "stage": "independent_verification",
                "elapsed_seconds": verified[sid]["elapsed_seconds"],
                "peak_rss_bytes": verified[sid]["peak_rss_bytes"],
            }
        )
        shutil.copyfile(d / "cell_qc.tsv.gz", out / "cell_qc" / f"{sid}.tsv.gz")
        save_json(out / "evidence/donors" / f"{sid}.verification.json", verified[sid])
        save_json(
            out / "evidence/donors" / f"{sid}.input_validation.json",
            load_json(d / "input_validation.json"),
        )
        locations.append(
            {
                "sample_id": sid,
                "baseline_h5ad": str(args.large_root / f"{sid}.export/baseline.h5ad"),
                "donor_export_directory": str(args.large_root / f"{sid}.export"),
            }
        )
    audit = pd.DataFrame(summaries).sort_values(["sample_id", "setting"]).reset_index(drop=True)
    if len(audit) != 2 * len(ids) or audit[["sample_id", "setting"]].duplicated().any():
        raise ValueError("Expected exactly two settings per donor")
    if not (audit.supplied == audit.retained + audit.rejected_union).all():
        raise ValueError("Combined attrition totals do not reconcile")
    write_tsv(genes, out / "tables/genes.tsv")
    write_tsv(audit, out / "tables/donor_qc.tsv")
    write_tsv(
        pd.DataFrame(provenance).sort_values(["sample_id", "kind"]),
        out / "tables/input_provenance.tsv",
    )
    write_tsv(
        pd.DataFrame(metrics).sort_values(["sample_id", "stage"]), out / "tables/task_metrics.tsv"
    )
    write_tsv(pd.DataFrame(locations), out / "tables/data_locations.tsv")
    write_tsv(
        audit[audit.retained == 0][["sample_id", "setting", "status"]],
        out / "tables/excluded_samples.tsv",
    )
    design = design_audit(audit)
    save_json(out / "evidence/design_audit.json", design)
    for setting in ("baseline", "strict"):
        selected = audit[(audit.setting == setting) & (audit.retained > 0)].sort_values("sample_id")
        matrix = pd.DataFrame({"gene_id": genes.gene_id})
        for sid in selected.sample_id:
            matrix[sid] = tables[sid][setting].to_numpy(dtype=np.int64)
        write_tsv(matrix, out / "tables" / f"pseudobulk_{setting}.tsv.gz")
        write_tsv(selected, out / "tables" / f"samples_{setting}.tsv")
    base = audit[audit.setting == "baseline"]
    group_batch = (
        pd.crosstab(base.group, base.batch)
        .reindex(index=["Healthy", "Early", "Late"], columns=["First", "Second"], fill_value=0)
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    write_tsv(group_batch, out / "tables/group_batch.tsv")
    group_stats = (
        audit.groupby(["group", "batch", "setting"], sort=True)
        .agg(
            donors=("sample_id", "size"),
            median_retention=("retention", "median"),
            min_retention=("retention", "min"),
            max_retention=("retention", "max"),
            supplied=("supplied", "sum"),
            retained=("retained", "sum"),
        )
        .reset_index()
    )
    write_tsv(group_stats, out / "tables/group_retention.tsv")
    final = {
        "dataset": settings["dataset"],
        "synthetic": settings["synthetic"],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "donors": len(ids),
        "files": len(provenance),
        "supplied_barcodes": int(base.supplied.sum()),
        "baseline_retained": int(base.retained.sum()),
        "strict_retained": int(audit.loc[audit.setting == "strict", "retained"].sum()),
        "source_bytes": sum(int(i["bytes"]) for i in provenance),
        "features": len(genes),
        "independent_verification": "PASS",
        "design": design,
        "settings": settings,
        "software_versions": versions(),
        "peak_donor_process_rss_bytes": max(int(m["peak_rss_bytes"]) for m in metrics),
        "summed_donor_task_seconds": sum(float(m["elapsed_seconds"]) for m in metrics),
        "retrieval_min_utc": min(i["retrieved_utc"] for i in provenance),
        "retrieval_max_utc": max(i["retrieved_utc"] for i in provenance),
        "limitations": [
            "Combined PBMC sums mix cell composition and expression.",
            "Early/Late are author labels; no individual clinical values inferred.",
            "Upstream barcode filtering is unknown; alignment and empty-droplet calling are not repeated.",
            "Doublets and ambient RNA have not been removed.",
            "Two fixed QC settings test sensitivity; neither is a universal quality standard.",
        ],
    }
    save_json(out / "summary.json", final)
    save_json(out / "evidence/manifest_validation.json", audit_manifest)
    save_json(
        out / "evidence/donor_verification_summary.json",
        {
            "status": "PASS",
            "donors": len(ids),
            "sample_ids": ids,
            "all_features_and_both_settings_verified": True,
            "evidence_files": [f"donors/{sid}.verification.json" for sid in ids],
        },
    )
    save_json(out / "settings.json", settings)
    seal_directory(out)
    print(json.dumps(final))


if __name__ == "__main__":
    main()
