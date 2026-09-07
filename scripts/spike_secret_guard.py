#!/usr/bin/env python3
"""SPIKE ONLY. Prove apply's secret rule fires -- see SPIKE.md #4.

It cannot be reached through a normal plan, because scan's answerable set contains
no gap pointing at a sensitive value, so the address-space check refuses first.
This calls apply_plan directly with a declaration that does point at one: the case
AGENTS.md §4 is actually about, and the one RFC-0001's rule would miss because it
keys on EnvVar.source, which for MONGO_URL is 'datastore', not 'secret'.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "offramp" / "scripts"))

from answers import ANSWERS_VERSION, AnswersFile  # noqa: E402
from apply import apply_plan  # noqa: E402
from render import load_appspec  # noqa: E402

scan_dir, repo = Path(sys.argv[1]), Path(sys.argv[2])
appspec, sha = load_appspec(scan_dir / "appspec.json")
index, var = next(
    (i, v) for i, v in enumerate(appspec.services[0].env) if v.name == "MONGO_URL"
)
assert var.sensitive and var.source == "datastore", "the fixture no longer sets up the case"

target = "service.backend.env.MONGO_URL.value"
declared = {
    target: {"kind": "value", "pointers": [f"/services/0/env/{index}/value"], "severity": "blocking"}
}
plan = {
    "basis": {"content_sha256": sha, "repo_commit": None, "detectors_version": "spike-2"},
    "entries": [
        {
            "target": target,
            "kind": "resolve",
            "current": None,
            "proposed": "mongodb://real:secret@cluster/db",
            "rationale": "the natural reading of 'supply the value the tool could not determine'",
            "evidence": [],
            "confidence": "high",
        }
    ],
}
result, rejections = apply_plan(
    appspec, sha, plan, declared, {}, AnswersFile(ANSWERS_VERSION, []),
    {target}, set(), "2026-09-07T00:00:00Z", repo,
)
blob = json.dumps([a.__dict__ for a in result.answers], default=str)
if not rejections or result.answers or "real:secret" in blob:
    print("FAIL: a sensitive value was accepted into the answers file", file=sys.stderr)
    raise SystemExit(1)
print(f"  rejected: sensitive value refused (source={var.source!r}, sensitive={var.sensitive})")
