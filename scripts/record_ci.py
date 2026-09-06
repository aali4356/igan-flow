#!/usr/bin/env python3
"""Bind completed local CI evidence to actual current source and runtime."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from integrity import sha256
from run_state import atomic_json, implementation_fingerprint

out = ROOT / "results/ci"
regressions = json.loads((out / "regressions.json").read_text())
current = implementation_fingerprint(ROOT)
if regressions["status"] != "PASS" or regressions["implementation"] != current:
    raise ValueError("Regressions are not current")
suites = ET.parse(out / "pytest.xml").getroot()
if any(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites.iter("testsuite")):
    raise ValueError("Unit tests failed")
browser = json.loads((out / "browser/qa.json").read_text())
run = json.loads((ROOT / "results/test/main/latest_attempt.json").read_text())
if browser["status"] != "PASS" or browser["reportSha256"] != sha256(
    Path(run["portable"]) / "report.html"
):
    raise ValueError("Browser QA is stale or failed")
atomic_json(
    out / "checks.json",
    {
        "status": "PASS",
        "implementation": current,
        "tests": sum(int(s.get("tests", 0)) for s in suites.iter("testsuite")),
        "integration_cases": regressions["case_count"],
        "offline_browser_cases": len(browser["cases"]),
        "runtime": run["software_versions"],
        "evidence_sha256": {
            name: sha256(out / name)
            for name in ("regressions.json", "pytest.xml", "browser/qa.json")
        },
        "execution": "local scripts/ci.sh; hosted CI status is separate",
    },
)
print("PASS: local CI evidence bound to current implementation")
