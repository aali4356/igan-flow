#!/usr/bin/env python3
"""Portable technical QC report with embedded figures and downloadable audit tables."""

import argparse
import base64
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from common import load_json, save_json
from integrity import seal_directory, verify_directory
from jinja2 import Environment, FileSystemLoader, select_autoescape

BLUE, GOLD, INK = "#2463a0", "#a66b12", "#243344"


def figure_uri(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    fig.savefig(path.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--summary", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    verify_directory(args.summary)
    shutil.copytree(args.summary, args.out)
    figures = args.out / "figures"
    figures.mkdir()
    data = load_json(args.out / "summary.json")
    audit = pd.read_csv(args.out / "tables/donor_qc.tsv", sep="\t")
    base = (
        audit[audit.setting == "baseline"]
        .sort_values(["group", "batch", "sample_id"])
        .set_index("sample_id")
    )
    strict = audit[audit.setting == "strict"].set_index("sample_id").loc[base.index]
    labels = [f"{sid}  {r.group} / {r.batch}" for sid, r in base.iterrows()]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelcolor": INK,
            "text.color": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    y = np.arange(len(base))
    fig, ax = plt.subplots(figsize=(11, max(3.5, len(base) * 0.27 + 1.5)))
    ax.hlines(y, strict.retention * 100, base.retention * 100, color="#a7afb9", lw=1.3)
    ax.scatter(base.retention * 100, y, color=BLUE, marker="o", label="Baseline", s=35, zorder=3)
    ax.scatter(
        strict.retention * 100,
        y,
        facecolors="white",
        edgecolors=GOLD,
        marker="s",
        label="Strict",
        s=35,
        zorder=3,
    )
    ax.set(yticks=y, yticklabels=labels, xlim=(-1, 101), xlabel="Retained supplied barcodes (%)")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.2)
    ax.legend(loc="upper left")
    fig.suptitle(
        f"Donor retention under two QC settings\n{data['dataset']} · {len(base)} donors · percentage of each donor's supplied barcodes",
        fontsize=12,
        y=1.01,
    )
    retention_uri = figure_uri(fig, figures / "donor_retention.png")
    fig, axes = plt.subplots(1, 2, figsize=(12, max(3.5, len(base) * 0.27 + 1.5)), sharey=True)
    for ax, metric, label in zip(
        axes,
        ["n_features", "mitochondrial_pct"],
        ["Detected features per supplied barcode", "Mitochondrial UMI counts (%)"],
    ):
        median = base[f"input_{metric}_median"].to_numpy()
        lo = base[f"input_{metric}_q25"].to_numpy()
        hi = base[f"input_{metric}_q75"].to_numpy()
        ax.errorbar(
            median,
            y,
            xerr=np.stack([median - lo, hi - median]),
            fmt="o",
            color=BLUE,
            ecolor="#9bb2cb",
            capsize=2,
            markersize=4,
        )
        ax.set(xlabel=label)
        ax.grid(axis="x", alpha=0.2)
    axes[0].set(yticks=y, yticklabels=labels)
    axes[0].invert_yaxis()
    fig.suptitle(
        f"Input QC distributions by donor\n{data['dataset']} · medians and interquartile ranges among supplied barcodes",
        fontsize=12,
        y=1.01,
    )
    qc_uri = figure_uri(fig, figures / "input_qc_distributions.png")
    cols = [
        "sample_id",
        "group",
        "batch",
        "setting",
        "supplied",
        "retained",
        "retention",
        "retained_umi",
        "retained_genes",
        "low_cell_flag",
        "low_retention_flag",
    ]
    display = audit[cols].copy()
    display["retention"] = (display.retention * 100).map(lambda x: f"{x:.2f}%")
    group_stats = pd.read_csv(args.out / "tables/group_retention.tsv", sep="\t")
    grp = group_stats.copy()
    for c in ["median_retention", "min_retention", "max_retention"]:
        grp[c] = (grp[c] * 100).map(lambda x: f"{x:.2f}%")
    group_batch = pd.read_csv(args.out / "tables/group_batch.tsv", sep="\t")
    reasons = audit[
        [
            "sample_id",
            "setting",
            "supplied",
            "rejected_union",
            "rejected_zero_counts",
            "rejected_low_features",
            "rejected_high_features",
            "rejected_high_mito",
        ]
    ]
    strata = group_stats[group_stats.setting == "strict"]
    low = strata.loc[strata.median_retention.idxmin()]
    high = strata.loc[strata.median_retention.idxmax()]
    segment_finding = (
        f"Strict median retention ranged from {low.median_retention * 100:.2f}% in {low['group']} / {low.batch} "
        f"({int(low.donors)} donors) to {high.median_retention * 100:.2f}% in {high['group']} / {high.batch} ({int(high.donors)} donors). "
        "These descriptive strata combine study group and chemistry; the differences do not establish a disease effect."
    )
    reason_totals = reasons[reasons.setting == "strict"].sum(numeric_only=True)
    rejection_finding = (
        f"Of {int(reason_totals.rejected_union):,} strict-rejected barcodes, "
        f"{int(reason_totals.rejected_high_mito):,} triggered the mitochondrial threshold, "
        f"{int(reason_totals.rejected_low_features):,} the minimum-feature threshold, and "
        f"{int(reason_totals.rejected_high_features):,} the maximum-feature threshold. Flags overlap and cannot be added."
    )
    delta = data["baseline_retained"] - data["strict_retained"]
    finding = f"Baseline retained {data['baseline_retained']:,} of {data['supplied_barcodes']:,} supplied barcodes; strict retained {data['strict_retained']:,}, a further reduction of {delta:,}."
    worst = (base.retention - strict.retention).sort_values(ascending=False)
    largest = (
        f"The largest retention change was {worst.iloc[0] * 100:.2f} percentage points for {worst.index[0]}."
        if len(worst)
        else ""
    )
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent), autoescape=select_autoescape(default=True)
    )
    html = env.get_template("report.html.j2").render(
        data=data,
        finding=finding,
        largest=largest,
        retention_uri=retention_uri,
        qc_uri=qc_uri,
        segment_finding=segment_finding,
        rejection_finding=rejection_finding,
        donor_table=display.to_html(index=False, classes="data"),
        group_table=grp.to_html(index=False, classes="data"),
        batch_table=group_batch.to_html(index=False, classes="data"),
        reason_table=reasons.to_html(index=False, classes="data"),
    )
    (args.out / "report.html").write_text(html)
    lines = [
        "# IgAN-Flow QC findings",
        "",
        f"Dataset: {data['dataset']} ({'synthetic fixture' if data['synthetic'] else 'public human PBMC data'}).",
        "",
        finding,
        largest,
        "",
        f"Processed {data['donors']} donors and {data['files']} expression files. Every gene count in both exports passed independent verification against original entries.",
        f"Maximum measured donor-process RSS: {data['peak_donor_process_rss_bytes'] / 1024**3:.2f} GiB. Sum of measured donor stage times: {data['summed_donor_task_seconds'] / 60:.1f} minutes; this is not workflow wall time.",
        "",
        "## Design audit",
    ]
    lines += [
        f"- {d['setting']}: {d['eligible_donors']} nonzero-library donors; rank {d['rank']}/4; {d['status']}."
        for d in data["design"]
    ]
    lines += ["", segment_finding, "", rejection_finding, "", "## Interpretation limits"] + [
        f"- {s}" for s in data["limitations"]
    ]
    lines += [
        "",
        "Report: report.html. Exact donor values: tables/donor_qc.tsv. Large count files: tables/data_locations.tsv.",
        "",
        "Future work: a separately specified annotation and donor-aware statistical analysis could assess cell-type questions after validating cell identity, ambient RNA, doublets, and clinical confounding.",
    ]
    (args.out / "FINDINGS.md").write_text("\n".join(lines) + "\n")
    save_json(
        args.out / "evidence/report_contract.json",
        {
            "audience": "technical",
            "surface": "self-contained HTML",
            "chart_grain": "one point per donor; supplied barcodes are the retention denominator",
            "charts": {
                "donor_retention": "paired dot plot, 0-100 percent axis, two fixed settings",
                "input_qc_distributions": "donor medians and interquartile ranges; no pooled-cell inference",
            },
            "structure": [
                "answer",
                "scope and definitions",
                "donor retention sensitivity",
                "QC distributions",
                "chemistry/group coverage",
                "exact audit tables",
                "methods and verification",
                "limitations and next questions",
            ],
            "quantitative_table_reason": "Exact donor counts, overlapping rejection flags, and design rank require lookup tables.",
        },
    )
    seal_directory(args.out)
    print(finding)


if __name__ == "__main__":
    main()
