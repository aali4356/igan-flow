# Output schema — v1.0.0

Paths below are relative to a successful scope's `current/` directory. JSON run records use `schema_version: 1`; biological output tables use the fixed v1.0 column contract. TSV exports use UTF-8, a header and no row index. Gzip count tables are deterministic after decompression. Empty eligible cohorts retain headers and feature rows.

| Output | Grain and contract |
| --- | --- |
| `summary.json` | One run: dataset, synthetic flag, donor/file/feature counts, supplied/baseline/strict totals, fixed settings, design audit, measured resources, software versions and execution identity. |
| `tables/donor_qc.tsv` | One donor × setting. Unique `(sample_id, setting)`; complete cohort. Includes `supplied`, `retained`, `rejected_union`, overlapping reason counts, retention, retained UMI/genes, quartiles and review flags. `supplied = retained + rejected_union`. |
| `cell_qc/<sample_id>.tsv.gz` | One supplied barcode. `cell_id = sample_id:original_barcode`; original order retained. Donor/group/batch/chemistry, total counts, detected features, mitochondrial count/percentage, baseline/strict masks and overlapping rejection flags. |
| `tables/pseudobulk_<setting>.tsv.gz` | One sorted gene per row, `gene_id` followed by eligible donor columns in sorted sample order. Original nonnegative integer sums across retained PBMCs. Zero-library donors excluded explicitly. |
| `tables/samples_<setting>.tsv` | Eligible donors in exactly the corresponding matrix column order, with metadata and QC audit fields. |
| `tables/genes.tsv` | One sorted feature: `source_row`, `gene_id`, `gene_symbol`, `feature_type`. Preserves source row mapping. Source IDs are unique symbols; no Ensembl remapping or collapse. |
| `tables/excluded_samples.tsv` | Donor × setting with zero cells; `sample_id`, `setting`, `status`. A header-only file means no exclusions. |
| `tables/group_batch.tsv`, `group_retention.tsv` | Descriptive donor coverage and donor-weighted retention summaries. |
| `tables/input_provenance.tsv` | One source file: sample/kind, URL, bytes, SHA-256, retrieval date and local `source_path`. All 78 required in full mode. |
| `tables/task_metrics.tsv` | Donor × processing stage: measured elapsed seconds and peak process RSS bytes. |
| `tables/data_locations.tsv` | One donor: absolute paths to its immutable export directory and baseline AnnData. Public example replaces these machine-specific paths with a regeneration note. |
| `evidence/donors/` | Independent original-entry checks and input-format audits per donor. |
| `evidence/design_audit.json` | Each setting's fixed four-column design, rank, eligibility, exclusions and review flags. Rank is an estimability check. |
| `evidence/run.json`, `runtime.json` | Execution identity, settings/input/code fingerprints and actual runtime versions. Local records contain local paths and are excluded from the public example. |
| `report.html`, `FINDINGS.md`, `figures/` | Static report, findings and PNG/SVG figures. |

## AnnData

`baseline.h5ad` lives in the immutable donor export directory identified by `data_locations.tsv`.

- `X`: sparse original integer UMI counts, retained baseline cells × all source Gene Expression features.
- `obs_names`: namespaced cell IDs in retained source order.
- `obs`: donor/sample/group/batch/chemistry, original barcode and cell QC fields.
- `var_names`: original unique feature IDs; annotations preserve gene symbols and feature type.
- `uns`: serialized fixed parameters, source provenance and software versions.

Strict cell masks and strict donor sums are exported; a second strict AnnData copy is unnecessary. Every AnnData entry is independently compared with the corresponding original source entry.

## Local evidence and integrity

Each scope's `latest_attempt.json` reports `RUNNING`, `FAILED` or `PASS`, with a unique timestamped `run_id`. `last_success.json` retains the last completed snapshot. Both identify immutable cache directories. Nested task and published outputs carry `.integrity.json` inventories of file sizes and SHA-256 hashes; their seals are bound in the run record. Verification sidecars use `.sha256`.

Fresh validation lives beside the immutable `portable/` directory under `validation/`, so auditing does not mutate previously sealed results. `final_validation.json` binds the run record, source implementation, settings, manifest and artifact seals. `completion_audit.json` joins that evidence with CI, full resume and separate automated/manual browser checks. Historical acceptance does not transfer to changed code or files.
