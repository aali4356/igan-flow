# IgAN-Flow 1.0.0 release readiness

All available local release gates passed on September 6, 2026: the final implementation, complete cohort, current-run release audit, public-file review and an independent clean-clone check. The public repository is [aali4356/igan-flow](https://github.com/aali4356/igan-flow). [Hosted checks](https://github.com/aali4356/igan-flow/actions/workflows/ci.yml) and [versioned releases](https://github.com/aali4356/igan-flow/releases) provide the publication record.

## Executed evidence

| Check | Result |
| --- | --- |
| Full cohort | 26 donors, 78 files, 36,601 features, 282,463 supplied barcodes |
| Fixed QC | 281,496 baseline and 268,880 strict retained; all 26 donors represented |
| Study labels | 9 Healthy, 11 Early, 6 Late; 8 first-batch and 18 second-batch donors |
| Independent original-entry verification | All 26 published AnnData/count/mask exports reverified; every canonical count/mask fingerprint matches the frozen reference |
| Preserved baseline comparison | All 33 canonical analytical files identical after decompression where applicable |
| Full execution and resume | 133 completed tasks; unchanged resume cached all 133 with unchanged analytical outputs |
| Selective invalidation | Changed donor: 5 unchanged-donor tasks cached, 8 affected/cohort tasks recomputed; report edit: 12 cached, 1 recomputed |
| Lint, formatting and focused tests | Passed; 35 tests |
| Actual offline integration regressions | 17 checks passed, including nested corruption/missing files, current source changes, stale-state failures, namespace collisions, quoted relocation and optimized-Python metadata tampering |
| Offline Chromium | Four desktop/mobile, light/dark cases passed; no external requests or page errors |
| Visual inspection | Codex inspected current screenshots and both full figure assets; additional mobile scroll capture passed. This records a technical inspection, with no user approval implied. |
| Bootstrap | Archive/installed-tool checks and exact dependency versions passed; repeated bootstrap preserved all implementation/lock bytes |
| Preservation | All 16 original research/specification files compared with the preserved archive remain unchanged |

Local full-cohort source fingerprint: `e4e8eebfb09a7304c53f4eccb8d9b223823ad3bc5d61753371fafcc6f97d98c5`. The source map is included in [the public release summary](docs/example/evidence/release_summary.json). Each run records actual settings, manifest, runtime and artifact seals. Historical acceptance files are unnecessary for that audit.

Final complete execution: `20260906T203339.442377Z`. Accepted unchanged resume and fresh verification: `20260906T204541.387961Z`.

## Measured benchmark and environment

- Full workflow wall time with retained downloads: **720.771 seconds**.
- Peak measured donor-process RSS: **1,855,815,680 bytes (1.73 GiB)**. The JVM/OS are separate.
- Peak concurrent heavy tasks: **1**; at most **4** processing workers.
- Total task-cache storage at audit, including preserved baseline, release runs and clone tools: **20,456,644,608 bytes (19.05 GiB)**, below the 30 GiB planning limit.
- Host: Apple M5 Pro, 48 GiB RAM, macOS 26.5.1, ARM. Python 3.11.0; Temurin 21.0.12.1+1; Nextflow 26.04.6; local Node v25.4.0; Playwright 1.55.0 / Chromium 140.0.7339.16.

The synthetic executor budget is 2 GB with a 512 MB heavy-task request; Java uses a separate bounded heap. Full processing uses the fixed 16 GiB executor budget.

## Report and local evidence

- [Compact public report](docs/example/report.html), [findings](docs/example/FINDINGS.md), [source-bound release summary](docs/example/evidence/release_summary.json).
- Local full report: `results/full/main/current/report.html`, also `results/report.html`.
- `results/full/main/latest_attempt.json` identifies the accepted immutable run directory. Its `validation/` folder contains `final_validation.json`, `cache_full.json`, `completion_audit.json`, browser checks/screenshots and the separate visual record.
- Local CI: `results/ci/checks.json`, `pytest.xml`, `regressions.json` and `browser/qa.json`.
- Publication review: `results/release/public_files.json`, `index_bytes.json`, `baseline_comparison.json` and `preserved_originals.json`. Detailed local logs and filesystem paths stay ignored.

The public example contains all donor-level summaries and combined count exports, with valid relative report links and no personal filesystem locations. It omits raw matrices, AnnData, per-cell QC and local run logs. [Reproduction procedure](docs/RELEASE_CHECKS.md), [output schema](docs/OUTPUT_SCHEMA.md).

## Clean clone and Git

The dedicated Git repository is rooted in this project. Private notes and historical workspace inventories are ignored. Commit identity uses Ahmad Ali and the verified GitHub noreply address. The committed release implementation passed two bootstrap runs, all 35 tests, 17 integration checks, 13 fresh synthetic tasks, fresh fixture verification, and four browser cases each for the synthetic and public reports in an independent clone. The clone used a separate Python environment and empty work/result cache; every fixture source resolved inside the clone, and its real-download cache was absent. Only verified tool archives/browser binaries were shared. The clone retained a clean Git status and had its original remote removed. [Sanitized clean-clone evidence](docs/release/clean_clone_checks.json). The public set contains 155 files (about 5.3 MB), including original code/docs, metadata snapshots and the compact example. Automated path/credential/size scans and manual review passed. No raw expression downloads, AnnData, environments or private planning files are tracked. The checked-out commit is available through `git rev-parse HEAD`; the exact-commit local release follow-up clone record is kept at `results/release/final_clone.json`. Publication updates the README, citation, release record and CI cache configuration; platform coverage below explains the source-fingerprint difference.

## Platform coverage

macOS ARM is the full-cohort validation platform. GitHub Actions runs the offline synthetic release suite on Ubuntu 24.04 x86_64 using a checksum-pinned Temurin release. Consult the [workflow history](https://github.com/aali4356/igan-flow/actions/workflows/ci.yml) for executed Linux results and the exact commit checked. Synthetic Linux checks do not establish full-cohort Linux validation.

The committed local release summary and clean-clone evidence record platform coverage when those audits were created, before GitHub publication. Publication corrected the CI cache setup: GitHub does not expose the `runner` context in job-level environment expressions, so a step now exports the cache path through `GITHUB_ENV`. This changes the workflow file included in the overall source fingerprint. The scientific processing, verification code, inputs and fixed settings remain unchanged from the full-cohort audit. Preserve these historical records when assessing later hosted results.

## Published source

The repository is named **IgAN-Flow** independently of the original workspace folder. Clone the published source with:

```bash
git clone https://github.com/aali4356/igan-flow.git
cd igan-flow
```

The README links installation, examples, output contracts and interpretation limits. GitHub exposes the MIT license and the software citation from the committed release metadata.
