"""Gaps carry a track. RFC-0005, *Gaps carry a track* and *Testing*.

The label is derived from the gap id. These tests hold it to the rule the table is
built on -- a gap is `auditor` or `both` exactly when an audit check reads it or links
it -- rather than to the table's current contents.
"""

import fnmatch
from dataclasses import replace
from pathlib import Path

import pytest
from audit import audit_repo
from checks import CHECKS
from gaps import TRACKS, track
from scan import scan_repo

FIXTURES = sorted(
    path for path in Path("fixtures").iterdir()
    if path.is_dir() and (path / "truth.yaml").is_file()
)


@pytest.fixture(scope="module")
def scans():
    return {fixture: scan_repo(fixture) for fixture in FIXTURES}


@pytest.mark.parametrize("gap_id, expected", [
    ("datastore.supabase.table.public.notes.write_scope", "auditor"),
    ("credential.backend.env.JWT_SECRET", "both"),
    ("credential.supabase.migrations.0001_init.sql.literal", "both"),
    ("service.backend.probes", "both"),
    ("service.backend.routes.prefix", "both"),
    # No check reads these, so they belong to the generator alone.
    ("service.backend.probes.port", "generator"),
    ("datastore.mongodb.mode", "generator"),
    ("datastore.supabase.mode", "generator"),
    ("delivery.image_registry", "generator"),
    ("environment.production.namespace", "generator"),
    ("app.name", "generator"),
])
def test_track_labels_a_gap_id(gap_id, expected):
    assert track(gap_id) == expected


def test_the_first_matching_pattern_wins():
    """`write_scope` would also match the final `*`; its own row comes first."""
    assert TRACKS[-1] == ("*", "generator")
    assert track("datastore.supabase.table.public.notes.write_scope") == "auditor"


def test_every_gap_in_every_fixture_has_a_label(scans):
    for fixture, result in scans.items():
        for gap in result.gaps:
            assert track(gap.id) in ("auditor", "generator", "both"), (fixture, gap.id)


def test_every_pattern_but_the_last_matches_a_fixture_gap(scans):
    """A pattern no fixture exercises is stale, or untested. Either way it fails."""
    ids = {gap.id for result in scans.values() for gap in result.gaps}
    for pattern, label in TRACKS[:-1]:
        assert any(fnmatch.fnmatchcase(gap_id, pattern) for gap_id in ids), (
            f"pattern {pattern!r} ({label}) matches no gap in any fixture"
        )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_every_related_gap_is_labelled_for_the_auditor(fixture):
    for finding in audit_repo(fixture)["findings"]:
        for gap_id in finding["related_gaps"]:
            assert track(gap_id) in ("auditor", "both"), (finding["id"], gap_id)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.id)
def test_no_check_reads_a_generator_gap(fixture, check, scans):
    """A check that starts reading a `generator` gap changes its output here."""
    result = scans[fixture]
    without = replace(result, gaps=[gap for gap in result.gaps
                                    if track(gap.id) != "generator"])
    assert check.run(fixture, without) == check.run(fixture, result)


def test_removing_the_gaps_checks_read_changes_their_output(scans):
    """The test above is only worth something if removing gaps can move a check.
    Removing every non-`generator` gap moves both checks that read gaps today."""
    changed = set()
    for fixture, result in scans.items():
        without = replace(result, gaps=[gap for gap in result.gaps
                                        if track(gap.id) == "generator"])
        changed |= {check.id for check in CHECKS
                    if check.run(fixture, without) != check.run(fixture, result)}
    assert {"credential.committed", "service.no_health_endpoint"} <= changed
