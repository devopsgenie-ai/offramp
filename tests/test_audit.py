"""audit: the entrypoint, the report, and the goldens. RFC-0002, Output and Testing."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from audit import AUDIT_OUTPUT_NAMES, audit_repo
from audit_report import render_audit_markdown
from spec import canonical_json

FIXTURES = sorted(
    path for path in Path("fixtures").iterdir()
    if (path / "expected_audit").is_dir()
)
AUDIT = "skills/offramp/scripts/audit.py"


def _run(*args, check=True):
    return subprocess.run([sys.executable, AUDIT, *map(str, args)],
                          capture_output=True, text=True, check=check)


def test_every_fixture_with_truth_has_an_audit_golden():
    with_truth = sorted(p for p in Path("fixtures").iterdir() if (p / "truth.yaml").is_file())
    assert FIXTURES == with_truth


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_audit_output_matches_the_golden_tree(fixture, tmp_path):
    _run(fixture, "--out", tmp_path)
    for name in AUDIT_OUTPUT_NAMES:
        expected = (fixture / "expected_audit" / name).read_text()
        assert (tmp_path / name).read_text() == expected, f"{fixture.name}/{name} drifted"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_audit_is_deterministic(fixture):
    assert canonical_json(audit_repo(fixture)) == canonical_json(audit_repo(fixture))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_findings_match_hand_written_truth_exactly(fixture):
    truth = yaml.safe_load((fixture / "truth.yaml").read_text())
    assert "findings" in truth, "truth.yaml must assert findings, even an empty list"
    document = audit_repo(fixture)
    assert sorted(f["id"] for f in document["findings"]) == sorted(truth["findings"])
    if "assessments" in truth:
        actual = {a["check"]: a["status"] for a in document["assessments"]}
        for check, status in truth["assessments"].items():
            assert actual[check] == status, f"{check}: {actual[check]} != {status}"


def test_no_credential_value_reaches_the_report(tmp_path):
    _run("fixtures/emergent-fastapi-mongo", "--out", tmp_path)
    for name in AUDIT_OUTPUT_NAMES:
        body = (tmp_path / name).read_text()
        for secret in ("not-a-real-password", "not-a-real-signing-key"):
            assert secret not in body, f"{name} leaked a credential value"


def test_report_carries_the_commit_and_no_timestamp():
    document = audit_repo(Path("fixtures/emergent-fastapi-mongo"))
    text = render_audit_markdown(document)
    assert "20" + "26-" not in text  # no date in the body
    assert json.dumps(document).count("generated_at") == 0


def test_every_report_says_what_it_cannot_see():
    document = audit_repo(Path("fixtures/emergent-fastapi-mongo"))
    assert document["not_visible"], "the not-visible section is never empty"
    assert "## Not visible from the repository" in render_audit_markdown(document)


def test_exit_zero_by_default_even_with_findings(tmp_path):
    assert _run("fixtures/emergent-fastapi-mongo", "--out", tmp_path).returncode == 0


def test_fail_on_threshold_exits_one(tmp_path):
    result = _run("fixtures/emergent-fastapi-mongo", "--out", tmp_path,
                  "--fail-on", "critical", check=False)
    assert result.returncode == 1


def test_fail_on_threshold_not_met_exits_zero(tmp_path):
    result = _run("fixtures/emergent-settings-prefix", "--out", tmp_path,
                  "--fail-on", "high", check=False)
    assert result.returncode == 0


def test_missing_repository_exits_two(tmp_path):
    result = _run(tmp_path / "nope", "--out", tmp_path / "out", check=False)
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_audit_writes_only_its_two_files(tmp_path):
    _run("fixtures/emergent-fastapi-mongo", "--out", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(AUDIT_OUTPUT_NAMES)


#: Checks whose `found` path is exercised by unit tests rather than a fixture, with why.
FOUND_BY_UNIT_TEST = {
    "service.no_health_endpoint": "tests/test_checks.py; no fixture backend lacks one",
}


def test_every_check_is_exercised_found_and_not_found_across_fixtures():
    """RFC-0002, Testing: each check fires somewhere and stays quiet somewhere, so a
    check that always says `found` or never does cannot pass unnoticed."""
    from checks import CHECKS
    seen: dict[str, set[str]] = {check.id: set() for check in CHECKS}
    for fixture in FIXTURES:
        for assessment in audit_repo(fixture)["assessments"]:
            seen[assessment["check"]].add(assessment["status"])
    for check, statuses in sorted(seen.items()):
        if check not in FOUND_BY_UNIT_TEST:
            assert "found" in statuses, f"{check} never fires on any fixture"
        assert statuses - {"found"}, f"{check} fires on every fixture"
