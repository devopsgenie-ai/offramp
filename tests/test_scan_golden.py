import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scan import OUTPUT_NAMES, scan_repo
from spec import canonical_json, check_enums

FIXTURES = sorted(
    path for path in Path("fixtures").iterdir() if (path / "expected_bare").is_dir()
)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_scan_output_matches_the_golden_tree(fixture, tmp_path):
    subprocess.run(
        [sys.executable, "skills/offramp/scripts/scan.py", str(fixture),
         "--out", str(tmp_path)],
        check=True, capture_output=True,
    )
    for name in OUTPUT_NAMES:
        expected = (fixture / "expected_bare" / name).read_text()
        actual = (tmp_path / name).read_text()
        assert actual == expected, f"{fixture.name}/{name} drifted"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_scan_is_deterministic_across_runs(fixture):
    first = scan_repo(fixture)
    second = scan_repo(fixture)
    assert canonical_json(first.appspec) == canonical_json(second.appspec)
    assert canonical_json(first.gaps) == canonical_json(second.gaps)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_scan_output_does_not_depend_on_the_checkout_path(fixture, tmp_path):
    """AppSpec.name from the remote, never the directory."""
    clone = tmp_path / "a-totally-different-directory-name"
    shutil.copytree(fixture, clone)
    assert canonical_json(scan_repo(fixture).appspec) == canonical_json(
        scan_repo(clone).appspec
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_the_enum_check_passes_on_every_fixture(fixture):
    result = scan_repo(fixture)
    assert check_enums(result.appspec, result.gaps) == []


def test_no_committed_credential_value_reaches_any_output_file(tmp_path):
    """AGENTS.md §4, asserted against the bytes actually written."""
    fixture = Path("fixtures/emergent-fastapi-mongo")
    subprocess.run(
        [sys.executable, "skills/offramp/scripts/scan.py", str(fixture),
         "--out", str(tmp_path)],
        check=True, capture_output=True,
    )
    for name in OUTPUT_NAMES:
        body = (tmp_path / name).read_text()
        for secret in ("not-a-real-password", "not-a-real-signing-key",
                       "this-is-a-dependency-example-and-must-never-be-reported"):
            assert secret not in body, f"{name} leaked a credential value"


def test_scan_exits_non_zero_on_a_repository_it_cannot_read(tmp_path):
    result = subprocess.run(
        [sys.executable, "skills/offramp/scripts/scan.py", str(tmp_path / "nope"),
         "--out", str(tmp_path / "out")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr
