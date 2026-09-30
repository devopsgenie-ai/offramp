"""The behavioural specs are complete and well formed. RFC-0003, Testing.

The database job itself runs in CI (`fix-behaviour`), not here: `make check` needs no
database. This keeps a new fix golden from arriving without a behaviour spec, and a
spec from asserting nothing about the tables the fix changes or leaves open.
"""

import json
from pathlib import Path

import pytest
import yaml

FIX_GOLDENS = sorted(p for p in Path("fixtures").iterdir() if (p / "expected_fix").is_dir())


def _migration(fixture: Path):
    return list((fixture / "expected_fix/supabase/migrations").glob("*.sql"))


@pytest.mark.parametrize("fixture", [f for f in FIX_GOLDENS if _migration(f)],
                         ids=lambda p: p.name)
def test_every_generated_migration_has_a_behaviour_spec(fixture):
    spec = yaml.safe_load((fixture / "behaviour.yaml").read_text())
    assert set(spec["users"]) >= {"alice", "bob"}
    for check in spec["checks"]:
        assert set(check) >= {"name", "as", "sql", "before", "after"}, check
        assert check["as"] in {*spec["users"], "anon"}
        for phase in ("before", "after"):
            assert check[phase] == "error" or isinstance(check[phase], int), check


@pytest.mark.parametrize("fixture", [f for f in FIX_GOLDENS if _migration(f)],
                         ids=lambda p: p.name)
def test_every_table_the_fix_touches_or_leaves_open_is_exercised(fixture):
    """Fixed tables must show the owner keeps access (locked shut) and others lose it
    (left open); NOT FIXED tables must show they behave as before."""
    fixes = json.loads((fixture / "expected_fix/fixes.json").read_text())
    spec = (fixture / "behaviour.yaml").read_text()
    tables = {f["table"] for f in fixes["findings"] if f["status"] in ("fixed", "gap")}
    for table in sorted(tables):
        schema, name = table.split(".", 1)
        quoted = f'{schema}."' + name.replace('"', '""') + '"'
        assert table in spec or quoted in spec or json.dumps(quoted)[1:-1] in spec, table
