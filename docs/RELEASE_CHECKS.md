# Reproduce release verification

Run from a clean checkout on a supported platform. Setup requires network access. Synthetic processing and report QA run offline after setup; full mode retrieves any missing public expression files.

```bash
bash scripts/bootstrap.sh
bash scripts/bootstrap.sh
bash scripts/bootstrap_browser.sh
bash scripts/ci.sh
bash scripts/run.sh sample --sample-id GSM8700986
bash scripts/run.sh full
source scripts/env.sh
python scripts/check_cache.py
python scripts/verify_results.py
IGAN_RELEASE_ROOT=$(python -c 'import json; from pathlib import Path; print(json.loads(Path("results/full/main/latest_attempt.json").read_text())["run_root"])')
node scripts/qa_report.cjs "$IGAN_RELEASE_ROOT/portable/report.html" "$IGAN_RELEASE_ROOT/validation/browser"
```

The repeated bootstrap must leave source and lock files unchanged (`git diff --exit-code`). `ci.sh` includes the isolated failure and selective-invalidation demonstrations. `check_cache.py` requires all 133 tasks to cache on unchanged full resume. The subsequent verifier independently recomputes every donor from the retained originals and compares all count/mask fingerprints with `assets/reference_results.json`.

## Separate visual review

Automated browser checks do not establish that a figure communicates correctly. Inspect the current `report.html`, both figure PNGs, and the desktop/mobile screenshots in both appearances. Check:

- Coverage numbers, percentages, denominators and fixed settings agree with the audit tables.
- Every donor label is represented; axes, points and legends remain readable.
- The mobile report retains access to wide figures and tables through local scrolling.
- Light/dark surfaces have readable text and links; no content is clipped by page layout.
- Limits, author labels and whole-PBMC composition caveats remain visible.

Record the inspection **only after performing it** in `$IGAN_RELEASE_ROOT/validation/browser/visual_review.json`. Use these required fields, with the actual reviewer, current report hash and concrete observations:

```json
{
  "status": "PASS",
  "review_method": "manual image inspection",
  "reviewer": "Actual person or agent that inspected the images",
  "report_sha256": "Actual SHA-256 of this run's portable/report.html",
  "reviewed_files": ["Names of the screenshots and figure assets actually inspected"],
  "observations": ["Specific findings from the inspection"],
  "user_approval": "Not requested or implied by this technical inspection"
}
```

A failed or incomplete inspection must remain failed/incomplete. Do not copy a historical record, infer a human approval or prepopulate a completed inspection for another run.

Then join current evidence and export the compact example:

```bash
python scripts/audit_completion.py
python scripts/export_example.py
```

The audit reads current implementation, input/settings/artifact bindings, independent verification, local CI, full resume and browser evidence. It requires no historical development acceptance file.

## Publication review

Review the exact Git index before committing. The project must be its own Git root. Stage only the public project files; run `python scripts/audit_public_files.py`, inspect its index listing under `results/release/public_files.json`, and manually review the public example, README and license/citation metadata. Original private planning files and all runtime results stay ignored. Historical CSV/TSV snapshots retain their original CRLF bytes.

After committing locally, clone to a different directory, use an empty work/output cache, run both bootstrap commands and `ci.sh`, and verify a clean Git status. Reusing verified tool archives/browser binaries is acceptable; synthetic inputs must come from the clone and historical project outputs must not be available to the verification commands. Record the tested implementation fingerprint and distinguish source identity from machine-specific result hashes.

The first GitHub-hosted Linux result is a separate external gate. A configured workflow and local macOS success do not establish that the hosted runner passed.
