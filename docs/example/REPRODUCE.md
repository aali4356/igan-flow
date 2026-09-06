# Reproduce this example

From the repository root, run `bash scripts/bootstrap.sh`, `bash scripts/run.sh full`, `bash scripts/check_reproducibility.sh`, then `bash scripts/verify.sh`. Complete offline browser and manual figure review as described in the release record, run `python scripts/audit_completion.py`, then `python scripts/export_example.py` after sourcing `scripts/env.sh`.

This bundle retains the full donor summaries and combined integer count tables. It omits per-cell QC, AnnData, raw downloads, local run logs and filesystem locations. Regeneration requires approximately 1.59 GB of public expression downloads. Dates, runtime measurements and run IDs vary; biological count/mask fingerprints must match the frozen reference.
