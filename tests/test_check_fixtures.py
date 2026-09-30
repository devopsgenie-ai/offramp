import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, "scripts")
from check_fixtures import assertion_guard, check_fixture, gap_counts  # noqa: E402
from scan import scan_repo  # noqa: E402

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def test_the_shipped_fixtures_agree_with_their_ground_truth():
    assert check_fixture(FIXTURE) == []
    assert check_fixture(Path("fixtures/emergent-settings-prefix")) == []


def test_gap_counts_cover_every_severity():
    counts = gap_counts(scan_repo(FIXTURE).gaps)
    assert set(counts) == {"blocking", "important", "cosmetic", "total"}
    assert counts["total"] == sum(
        counts[level] for level in ("blocking", "important", "cosmetic")
    )


def test_the_recorded_gap_count_matches_the_current_run():
    recorded = json.loads((FIXTURE / "gap_count.json").read_text())
    assert gap_counts(scan_repo(FIXTURE).gaps) == recorded


def test_a_wrong_route_in_truth_is_reported(tmp_path):
    """The assertion that fails when a detector guesses."""
    clone = tmp_path / "f"
    shutil.copytree(FIXTURE, clone)
    truth = yaml.safe_load((clone / "truth.yaml").read_text())
    backend = next(s for s in truth["services"] if s["name"] == "backend")
    backend["routes"] = ["/health"]          # the decorator literal: wrong
    (clone / "truth.yaml").write_text(yaml.safe_dump(truth))
    problems = check_fixture(clone)
    assert any("/health" in problem for problem in problems)


def test_a_must_not_detect_violation_is_reported(tmp_path):
    clone = tmp_path / "f"
    shutil.copytree(FIXTURE, clone)
    truth = yaml.safe_load((clone / "truth.yaml").read_text())
    truth["must_not_detect"]["env_names"].append("MONGO_URL")
    (clone / "truth.yaml").write_text(yaml.safe_dump(truth))
    problems = check_fixture(clone)
    assert any("MONGO_URL" in problem for problem in problems)


def test_the_assertion_guard_rejects_truth_that_asserts_nothing_about_a_service():
    appspec = scan_repo(FIXTURE).appspec
    problems = assertion_guard({"app": {"name": "widgets"}, "services": []}, appspec)
    assert any("backend" in problem for problem in problems)
    assert any("frontend" in problem for problem in problems)


def test_the_assertion_guard_accepts_the_shipped_truth():
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text())
    assert assertion_guard(truth, scan_repo(FIXTURE).appspec) == []


LOVABLE = Path("fixtures/lovable-vite-supabase")


def _with_truth(tmp_path, edit):
    clone = tmp_path / "f"
    shutil.copytree(LOVABLE, clone)
    truth = yaml.safe_load((clone / "truth.yaml").read_text())
    edit(truth)
    (clone / "truth.yaml").write_text(yaml.safe_dump(truth))
    return check_fixture(clone)


def test_a_guessed_owner_fails_ground_truth(tmp_path):
    """RFC-0003, Testing: a detector that starts guessing owners from names fails
    here even as the gap count falls."""
    def edit(truth):
        truth["write_scope"]["public.notes"] = "id"
    problems = _with_truth(tmp_path, edit)
    assert any("public.notes" in problem and "undetermined" in problem
               for problem in problems)


def test_an_unasserted_owner_table_fails_the_guard(tmp_path):
    def edit(truth):
        del truth["write_scope"]["public.tasks"]
    problems = _with_truth(tmp_path, edit)
    assert any("public.tasks" in problem and "asserts nothing" in problem
               for problem in problems)


def test_a_missing_write_scope_key_fails_the_guard(tmp_path):
    problems = _with_truth(tmp_path, lambda truth: truth.pop("write_scope"))
    assert any("does not assert write_scope" in problem for problem in problems)
