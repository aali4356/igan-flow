# IgAN-Flow QC findings

Dataset: GSE285335 (public human PBMC data).

Baseline retained 281,496 of 282,463 supplied barcodes; strict retained 268,880, a further reduction of 12,616.
The largest retention change was 12.07 percentage points for GSM8700994.

Processed 26 donors and 78 expression files. Every gene count in both exports passed independent verification against original entries.
Maximum measured donor-process RSS: 1.73 GiB. Sum of measured donor stage times: 10.7 minutes; this is not workflow wall time.

## Design audit
- baseline: 26 nonzero-library donors; rank 4/4; full_rank.
- strict: 26 nonzero-library donors; rank 4/4; full_rank.

Strict median retention ranged from 92.52% in Healthy / Second (5 donors) to 97.88% in Early / Second (9 donors). These descriptive strata combine study group and chemistry; the differences do not establish a disease effect.

Of 13,583 strict-rejected barcodes, 10,706 triggered the mitochondrial threshold, 4,844 the minimum-feature threshold, and 170 the maximum-feature threshold. Flags overlap and cannot be added.

## Interpretation limits
- Combined PBMC sums mix cell composition and expression.
- Early/Late are author labels; no individual clinical values inferred.
- Upstream barcode filtering is unknown; alignment and empty-droplet calling are not repeated.
- Doublets and ambient RNA have not been removed.
- Two fixed QC settings test sensitivity; neither is a universal quality standard.

Report: report.html. Exact donor values: tables/donor_qc.tsv. Large count files: tables/data_locations.tsv.

Future work: a separately specified annotation and donor-aware statistical analysis could assess cell-type questions after validating cell identity, ambient RNA, doublets, and clinical confounding.
