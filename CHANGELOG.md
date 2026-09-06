# Changelog

## 1.0.0 — 2026-09-06

- Process the frozen 26-donor GSE285335 cohort in separate acquisition, conversion, QC, export and independent-verification tasks.
- Preserve sparse integer counts and compare inclusive baseline/strict QC settings with complete donor accounting.
- Enforce locally calculated SHA-256 references for all 78 expression files.
- Detect damaged cached and published nested artifacts before accepting a resumed run.
- Publish immutable snapshots atomically and distinguish the latest attempt from the last success.
- Validate mode, cohort and settings; support relocated local fixtures and quoted paths.
- Pin macOS ARM and Linux x86_64 toolchains, Python dependencies and optional browser tooling.
- Add reproducible local/CI regressions, an offline report and a compact public example.

Execution evidence and platform limits accompany this release in [RELEASE_READINESS.md](RELEASE_READINESS.md).
