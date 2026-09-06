"""Audit current full-cohort outputs, optionally recomputing every donor from sources."""

import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from contracts import check_sources
from integrity import seal_file, verify_bound_artifacts
from run_state import implementation_fingerprint


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "src" in a:
            self.sources.append(a["src"])
        if tag == "link" and "href" in a:
            self.sources.append(a["href"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, default=ROOT / "results/full/main/current")
    p.add_argument(
        "--quick",
        action="store_true",
        help="Check current artifact consistency without rerunning original-entry verification",
    )
    args = p.parse_args()
    out = args.results.resolve()
    summary = json.loads((out / "summary.json").read_text())
    record_path = out.parent / "record.json"
    run = json.loads(record_path.read_text())
    if run["status"] != "PASS" or run["implementation"] != implementation_fingerprint(ROOT):
        raise ValueError("Result is failed or belongs to different implementation bytes")
    verify_bound_artifacts(run["artifacts"])
    if (
        digest(Path(run["manifest"])) != run["manifest_sha256"]
        or digest(Path(run["settings_path"])) != run["settings_sha256"]
    ):
        raise ValueError("Current manifest/settings changed since execution")
    selected = [r for r in run["input_records"] if f"EXPORT ({r['sample_id']})" in run["tasks"]]
    check_sources(selected, Path(os.environ["IGAN_CACHE"]), allow_download=False)
    validation = out.parent / "validation"
    validation.mkdir(exist_ok=True)
    reference = json.loads((ROOT / "assets/reference_results.json").read_text())
    manifest = pd.DataFrame(selected).sort_values("sample_id")
    ids = manifest.sample_id.tolist()
    if not len(ids) == len(set(ids)) == summary["donors"]:
        raise ValueError("Validation failed: len(ids) == len(set(ids)) == summary['donors']")
    if run["mode"] == "full":
        if not (
            len(ids) == 26 and Counter(manifest.group) == {"Early": 11, "Late": 6, "Healthy": 9}
        ):
            raise ValueError(
                "Validation failed: len(ids) == 26 and Counter(manifest.group) == {'Early': 11, 'Late': 6, 'Healthy': 9}"
            )
        if not summary["baseline_retained"] == reference["baseline_retained"]:
            raise ValueError(
                "Validation failed: summary['baseline_retained'] == reference['baseline_retained']"
            )
        if not summary["strict_retained"] == reference["strict_retained"]:
            raise ValueError(
                "Validation failed: summary['strict_retained'] == reference['strict_retained']"
            )
    audit = pd.read_csv(out / "tables/donor_qc.tsv", sep="\t")
    if not (
        len(audit) == 2 * len(ids) and (not audit[["sample_id", "setting"]].duplicated().any())
    ):
        raise ValueError(
            "Validation failed: len(audit) == 2 * len(ids) and (not audit[['sample_id', 'setting']].duplicated().any())"
        )
    if not set(audit.sample_id) == set(ids):
        raise ValueError("Validation failed: set(audit.sample_id) == set(ids)")
    if not (audit.supplied == audit.retained + audit.rejected_union).all():
        raise ValueError(
            "Validation failed: (audit.supplied == audit.retained + audit.rejected_union).all()"
        )
    if not audit[audit.setting == "baseline"].supplied.sum() == summary["supplied_barcodes"]:
        raise ValueError(
            "Validation failed: audit[audit.setting == 'baseline'].supplied.sum() == summary['supplied_barcodes']"
        )
    genes = pd.read_csv(out / "tables/genes.tsv", sep="\t", keep_default_na=False)
    if not (len(genes) == summary["features"] and genes.gene_id.is_unique):
        raise ValueError(
            "Validation failed: len(genes) == summary['features'] and genes.gene_id.is_unique"
        )
    if not genes.gene_id.tolist() == sorted(genes.gene_id):
        raise ValueError("Validation failed: genes.gene_id.tolist() == sorted(genes.gene_id)")
    locations = pd.read_csv(out / "tables/data_locations.tsv", sep="\t").set_index("sample_id")
    if not set(locations.index) == set(ids):
        raise ValueError("Validation failed: set(locations.index) == set(ids)")
    checks = []
    for sid in ids:
        folder = Path(locations.loc[sid, "donor_export_directory"])
        if not (folder.is_dir() and Path(locations.loc[sid, "baseline_h5ad"]).is_file()):
            raise ValueError(
                "Validation failed: folder.is_dir() and Path(locations.loc[sid, 'baseline_h5ad']).is_file()"
            )
        source_meta = json.loads((folder / "metadata.json").read_text())
        record = manifest.set_index("sample_id").loc[sid]
        if not source_meta["sample_id"] == source_meta["donor_id"] == sid:
            raise ValueError(
                "Validation failed: source_meta['sample_id'] == source_meta['donor_id'] == sid"
            )
        for key in ["group", "batch", "chemistry", "author_label"]:
            if not source_meta[key] == record[key]:
                raise ValueError("Validation failed: source_meta[key] == record[key]")
        if not args.quick:
            with tempfile.TemporaryDirectory(
                prefix="igan-verify-", dir=os.environ.get("IGAN_CACHE")
            ) as tmp:
                source = Path(tmp)
                prov = json.loads((folder / "provenance.json").read_text())
                (source / "metadata.json").write_text(json.dumps(source_meta))
                (source / "provenance.json").write_text(json.dumps(prov))
                for item in prov:
                    (source / (item["kind"] + ".gz")).symlink_to(item["source_path"])
                target = validation / "donors" / f"{sid}.json"
                target.parent.mkdir(exist_ok=True, parents=True)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "lib/verify_donor.py"),
                        "--source",
                        str(source),
                        "--export",
                        str(folder),
                        "--settings",
                        str(out / "settings.json"),
                        "--threads",
                        "4",
                        "--out",
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode:
                    raise AssertionError(f"{sid} re-verification failed: {result.stderr}")
                checks.append(json.loads(target.read_text()))
        else:
            checks.append(
                json.loads((out / "evidence/donors" / f"{sid}.verification.json").read_text())
            )
        if run["mode"] != "test":
            if not checks[-1]["canonical_fingerprints"] == reference["canonical_fingerprints"][sid]:
                raise ValueError(
                    "Validation failed: checks[-1]['canonical_fingerprints'] == reference['canonical_fingerprints'][sid]"
                )
        donor_counts = (
            pd.read_csv(folder / "counts.tsv", sep="\t", keep_default_na=False)
            .set_index("gene_id")
            .loc[genes.gene_id]
        )
        qc = pd.read_csv(out / "cell_qc" / f"{sid}.tsv.gz", sep="\t")
        if not digest(out / "cell_qc" / f"{sid}.tsv.gz") == digest(folder / "cell_qc.tsv.gz"):
            raise ValueError(
                "Validation failed: digest(out / 'cell_qc' / f'{sid}.tsv.gz') == digest(folder / 'cell_qc.tsv.gz')"
            )
        if not (qc.cell_id.is_unique and qc.sample_id.eq(sid).all()):
            raise ValueError(
                "Validation failed: qc.cell_id.is_unique and qc.sample_id.eq(sid).all()"
            )
        if not not (qc.strict_keep & ~qc.baseline_keep).any():
            raise ValueError("Validation failed: not (qc.strict_keep & ~qc.baseline_keep).any()")
        for setting in ("baseline", "strict"):
            item = audit[(audit.sample_id == sid) & (audit.setting == setting)].iloc[0]
            if not item.retained == qc[setting + "_keep"].sum():
                raise ValueError("Validation failed: item.retained == qc[setting + '_keep'].sum()")
            if not item.retained_umi == int(donor_counts[setting].sum()):
                raise ValueError(
                    "Validation failed: item.retained_umi == int(donor_counts[setting].sum())"
                )
        print(f"Verified {sid}", flush=True)
    for setting in ("baseline", "strict"):
        counts = pd.read_csv(
            out / "tables" / f"pseudobulk_{setting}.tsv.gz", sep="\t", keep_default_na=False
        )
        samples = pd.read_csv(out / "tables" / f"samples_{setting}.tsv", sep="\t")
        eligible = sorted(audit.loc[(audit.setting == setting) & (audit.retained > 0), "sample_id"])
        if not counts.columns.tolist() == ["gene_id", *eligible]:
            raise ValueError("Validation failed: counts.columns.tolist() == ['gene_id', *eligible]")
        if not samples.sample_id.tolist() == eligible:
            raise ValueError("Validation failed: samples.sample_id.tolist() == eligible")
        if not counts.gene_id.tolist() == genes.gene_id.tolist():
            raise ValueError("Validation failed: counts.gene_id.tolist() == genes.gene_id.tolist()")
        for sid in eligible:
            folder = Path(locations.loc[sid, "donor_export_directory"])
            donor = (
                pd.read_csv(folder / "counts.tsv", sep="\t", keep_default_na=False)
                .set_index("gene_id")
                .loc[genes.gene_id]
            )
            if counts[sid].dtype.kind not in "iu":
                raise ValueError("Validation failed: counts[sid].dtype.kind in 'iu'")
            np.testing.assert_array_equal(counts[sid], donor[setting])
        if not summary[setting + "_retained"] == int(
            audit.loc[audit.setting == setting, "retained"].sum()
        ):
            raise ValueError(
                "Validation failed: summary[setting + '_retained'] == int(audit.loc[audit.setting == setting, 'retained'].sum())"
            )
    prov = pd.read_csv(out / "tables/input_provenance.tsv", sep="\t")
    if not (len(prov) == 3 * len(ids) and (not prov[["sample_id", "kind"]].duplicated().any())):
        raise ValueError(
            "Validation failed: len(prov) == 3 * len(ids) and (not prov[['sample_id', 'kind']].duplicated().any())"
        )
    if not prov.bytes.sum() == summary["source_bytes"]:
        raise ValueError("Validation failed: prov.bytes.sum() == summary['source_bytes']")
    for item in prov.to_dict("records"):
        source = Path(item["source_path"])
        if not (source.stat().st_size == item["bytes"] and digest(source) == item["sha256"]):
            raise ValueError(
                "Validation failed: source.stat().st_size == item['bytes'] and digest(source) == item['sha256']"
            )
    parsed = Assets()
    html = (out / "report.html").read_text()
    parsed.feed(html)
    if not ("<script" not in html.lower() and "cdn." not in html.lower()):
        raise ValueError(
            "Validation failed: '<script' not in html.lower() and 'cdn.' not in html.lower()"
        )
    if not (
        len(parsed.sources) == 2
        and all((s.startswith("data:image/png;base64,") for s in parsed.sources))
    ):
        raise ValueError(
            "Validation failed: len(parsed.sources) == 2 and all((s.startswith('data:image/png;base64,') for s in parsed.sources))"
        )
    for uri in parsed.sources:
        with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as image:
            if not (image.width > 200 and image.height > 100):
                raise ValueError("Validation failed: image.width > 200 and image.height > 100")
            image.verify()
    for link in parsed.links:
        if "://" not in link and (not link.startswith("#")):
            if not (out / link).is_file():
                raise ValueError(f"Missing report link {link}")
    for value in ("baseline_retained", "strict_retained", "supplied_barcodes"):
        if f"{summary[value]:,}" not in html:
            raise ValueError("Validation failed: f'{summary[value]:,}' in html")
    result = {
        "status": "PASS",
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "artifact consistency only"
        if args.quick
        else "fresh independent original-entry verification plus artifact audit",
        "donors": len(ids),
        "files": len(prov),
        "supplied_barcodes": summary["supplied_barcodes"],
        "features": len(genes),
        "run_id": run["run_id"],
        "record_sha256": digest(record_path),
        "implementation": run["implementation"],
        "settings_sha256": run["settings_sha256"],
        "manifest_sha256": run["manifest_sha256"],
        "artifacts": run["artifacts"],
        "reference_counts_masks_match": run["mode"] != "test",
        "all_gene_sums_both_settings_reverified": not args.quick,
        "combined_matrices_match_donor_exports": True,
        "source_sha256_match_current_files": True,
        "portable_report_links_and_embedded_images": True,
        "report_sha256": digest(out / "report.html"),
        "implementation_sha256": {
            str(path.relative_to(ROOT)): digest(path)
            for path in sorted(
                [
                    ROOT / "main.nf",
                    ROOT / "nextflow.config",
                    ROOT / "requirements.lock",
                    *list((ROOT / "lib").glob("*.py")),
                    ROOT / "lib/report.html.j2",
                    ROOT / "modules/local/workflow.nf",
                ]
            )
        },
        "canonical_fingerprints": {c["sample_id"]: c["canonical_fingerprints"] for c in checks},
        "limits": "Visual browser QA and cache behavior have separate evidence files.",
    }
    verify_bound_artifacts(run["artifacts"])
    if not run["implementation"] == implementation_fingerprint(ROOT):
        raise ValueError(
            "Validation failed: run['implementation'] == implementation_fingerprint(ROOT)"
        )
    target = validation / ("artifact_validation.json" if args.quick else "final_validation.json")
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    seal_file(target)
    print(f"PASS: {target}")


if __name__ == "__main__":
    main()
