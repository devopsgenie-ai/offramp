"""fix: the entrypoint, the round trip, the manifest and the golden trees. RFC-0003.

Before writing anything, `fix` replays the repository's migrations plus the file it is
about to write, runs the RLS checks, and refuses (exit 2) unless every finding marked
`fixed` is gone, every finding marked `gap` is still present, and nothing new appears
except the findings the plan predicted. A renderer that adds a scoped policy beside a
`true` one -- the "left open" failure -- cannot get past it.
"""

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import fix
from audit import audit_repo
from fix_render import render_fix

FIXTURES = sorted(p for p in Path("fixtures").iterdir() if (p / "truth.yaml").is_file())
LOVABLE = Path("fixtures/lovable-vite-supabase")
OWNERS = Path("fixtures/lovable-rls-owners")
FIX = "skills/offramp/scripts/fix.py"
VERIFY = "skills/offramp/scripts/verify.py"
MIGRATION = "supabase/migrations/20260115110001_offramp_rls.sql"


def _run(script, *args):
    return subprocess.run([sys.executable, script, *map(str, args)],
                          capture_output=True, text=True)


def _tree(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*"))
            if p.is_file()}


# -- goldens ------------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_fix_output_matches_the_golden_tree(fixture, tmp_path):
    """An absent expected_fix/ is the golden for "nothing to fix": the output is empty."""
    result = _run(FIX, fixture, "--out", tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert _tree(tmp_path / "out") == _tree(fixture / "expected_fix")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_fix_is_byte_identical_across_runs(fixture, tmp_path):
    assert fix.build(fixture).files == fix.build(fixture).files


def test_every_fixture_with_rls_findings_has_a_fix_golden():
    with_rls = [f for f in FIXTURES if any(
        item["check"].startswith("supabase.rls.") for item in audit_repo(f)["findings"])]
    assert with_rls == [f for f in FIXTURES if (f / "expected_fix").is_dir()]
    assert len(with_rls) >= 3


# -- the round trip ---------------------------------------------------------------------

def _broken(transform):
    def renderer(appspec, document, version):
        rendering = render_fix(appspec, document, version)
        files = dict(rendering.files)
        files[rendering.migration] = transform(files[rendering.migration])
        return replace(rendering, files=files)
    return renderer


def test_a_renderer_that_adds_without_dropping_is_refused(tmp_path):
    """The left-open failure: an owner policy beside the `true` one fixes nothing."""
    renderer = _broken(lambda sql: sql.replace(
        'drop policy if exists "Authenticated users can update tasks" on public.tasks;\n', "")
        .replace("create policy", "select 1;\ncreate policy", 1))
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert refused.value.code == 2
    assert ("supabase.rls.permissive_write.public.tasks.authenticated-users-can-update-tasks"
            " is marked fixed but is still present") in str(refused.value)


def test_a_renderer_that_silences_a_gap_is_refused():
    renderer = _broken(lambda sql: sql + "alter table public.notes enable row level security;\n")
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert "supabase.rls.disabled.public.notes is marked gap but is gone" in str(refused.value)


def test_an_unpredicted_finding_is_refused():
    renderer = _broken(lambda sql: sql + (
        'create policy "surprise" on public.tasks for select to anon using (true);\n'))
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert ("supabase.rls.public_read.public.tasks.surprise is new and was not predicted"
            in str(refused.value))


def test_a_statement_count_that_does_not_match_is_refused():
    renderer = _broken(lambda sql: sql + "select 1;\n")
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert "3 statements, but the renderer wrote 2" in str(refused.value)


def test_output_the_replay_cannot_follow_is_refused():
    renderer = _broken(lambda sql: sql + "do $$ begin execute 'drop table public.tasks'; end $$;\n")
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert "could not be replayed" in str(refused.value)


def test_the_cli_exits_2_when_the_round_trip_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fix, "render_fix", _broken(lambda sql: sql + "select 1;\n"))
    assert fix.main([str(LOVABLE), "--out", str(tmp_path / "out")]) == 2
    assert "refusing to write" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# -- the manifest: hand edits are refused, not overwritten --------------------------------

def test_a_hand_edited_file_is_a_conflict(tmp_path):
    out = tmp_path / "out"
    assert _run(FIX, LOVABLE, "--out", out).returncode == 0
    (out / "FIXES.md").write_text("my notes\n")
    result = _run(FIX, LOVABLE, "--out", out)
    assert result.returncode == 1
    assert "FIXES.md" in result.stderr
    assert (out / "FIXES.md").read_text() == "my notes\n"


def test_a_file_offramp_did_not_write_is_never_overwritten(tmp_path):
    out = tmp_path / "out"
    (out / "supabase/migrations").mkdir(parents=True)
    (out / MIGRATION).write_text("-- mine\n")
    result = _run(FIX, LOVABLE, "--out", out)
    assert result.returncode == 1
    assert (out / MIGRATION).read_text() == "-- mine\n"


def test_rerunning_with_another_version_replaces_the_old_file(tmp_path):
    out = tmp_path / "out"
    assert _run(FIX, LOVABLE, "--out", out).returncode == 0
    assert _run(FIX, LOVABLE, "--out", out, "--version", "20260201000000").returncode == 0
    assert not (out / MIGRATION).exists()
    assert (out / "supabase/migrations/20260201000000_offramp_rls.sql").is_file()
    manifest = json.loads((out / ".offramp/manifest.json").read_text())
    assert manifest["version"] == "20260201000000"


@pytest.mark.parametrize("version, message", [
    ("abc", "digits"),
    ("20260115110000", "must sort after"),
    ("99", "must sort after"),
])
def test_a_version_that_would_not_run_last_is_refused(tmp_path, version, message):
    result = _run(FIX, LOVABLE, "--out", tmp_path / "out", "--version", version)
    assert result.returncode == 2
    assert message in result.stderr


def test_the_output_directory_may_not_be_the_repository(tmp_path):
    clone = tmp_path / "app"
    shutil.copytree(LOVABLE, clone)
    result = _run(FIX, clone, "--out", clone)
    assert result.returncode == 2
    assert "staging directory" in result.stderr


# -- secrets and convergence ------------------------------------------------------------

def test_no_secret_value_reaches_any_output_file(tmp_path):
    """AGENTS.md §4. The fix consumes schema identifiers only."""
    clone = tmp_path / "app"
    shutil.copytree(OWNERS, clone)
    service_role = ("eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0."
                    "ZmFrZS1zZXJ2aWNlLXJvbGUta2V5LWZvci10ZXN0cw")
    openai = "sk-proj-fakefakefakefakefakefakefakefake0123"
    (clone / ".env").write_text(f'SUPABASE_SERVICE_ROLE_KEY="{service_role}"\n'
                                f'VITE_OPENAI_API_KEY="{openai}"\n')
    (clone / "src/ai.ts").write_text("export const k = import.meta.env.VITE_OPENAI_API_KEY;\n")
    assert _run(FIX, clone, "--out", tmp_path / "out").returncode == 0
    anon = (OWNERS / "src/integrations/supabase/client.ts").read_text().split('"')[3]
    for path, body in _tree(tmp_path / "out").items():
        for secret in (service_role, openai, anon, "fixtureprojectref01"):
            assert secret not in body, f"{path} leaked a value"


def test_applying_the_fix_and_rerunning_converges(tmp_path):
    """After the migration is committed the fixed findings are gone, and a re-run writes
    no migration: only gaps remain, and those need an answer, not another file."""
    clone = tmp_path / "app"
    shutil.copytree(OWNERS, clone)
    first = fix.build(clone)
    migration = first.rendering.migration
    (clone / migration).write_text(first.files[migration])
    after = {f["id"] for f in audit_repo(clone)["findings"]}
    fixed = {o.id for o in first.rendering.outcomes if o.status == "fixed"}
    assert fixed and not fixed & after
    second = fix.build(clone)
    assert second.rendering.migration is None
    assert {o.status for o in second.rendering.outcomes} == {"gap", "not_in_scope"}


# -- verify -----------------------------------------------------------------------------

def test_verify_passes_on_untouched_output(tmp_path):
    assert _run(FIX, OWNERS, "--out", tmp_path / "out").returncode == 0
    result = _run(VERIFY, OWNERS, "--out", tmp_path / "out")
    assert result.returncode == 0, result.stdout + result.stderr


def test_verify_names_a_tampered_file(tmp_path):
    """RFC-0001, Testing: a deliberately tampered tree, a non-zero exit, a diff that
    names the file."""
    out = tmp_path / "out"
    assert _run(FIX, OWNERS, "--out", out).returncode == 0
    target = next((out / "supabase/migrations").iterdir())
    target.write_text(target.read_text().replace("to authenticated", "to anon", 1))
    result = _run(VERIFY, OWNERS, "--out", out)
    assert result.returncode == 1
    assert target.name in result.stdout
    assert "to anon" in result.stdout


def test_verify_without_a_manifest_fails(tmp_path):
    result = _run(VERIFY, OWNERS, "--out", tmp_path / "empty")
    assert result.returncode == 1
    assert "manifest" in result.stdout


# -- versions in repositories that mix version widths (found by the corpus run) ----------

OPEN = ("create table public.notes (id uuid primary key, "
        "user_id uuid default auth.uid() references auth.users(id), body text);")


def _mixed(tmp_path: Path, names: dict[str, str]) -> Path:
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"dependencies": {"@supabase/supabase-js": "2"}}')
    (root / "src/client.ts").write_text("import { createClient } from '@supabase/supabase-js';\n")
    (root / "supabase/migrations").mkdir(parents=True)
    for name, text in names.items():
        (root / "supabase/migrations" / name).write_text(text)
    return root


def test_the_default_version_sorts_last_by_name_when_widths_are_mixed(tmp_path):
    """`20260127_b.sql` runs after `20260118172347_a.sql`: files run in name order. The
    default is the smallest version, as wide as the latest, that sorts after both."""
    root = _mixed(tmp_path, {"20260118172347_a.sql": OPEN, "20260127_b.sql": "select 1;"})
    built = fix.build(root)
    assert built.rendering.migration == "supabase/migrations/20260128000000_offramp_rls.sql"


def test_no_version_can_sort_last_is_refused_with_a_way_out(tmp_path):
    root = _mixed(tmp_path, {"20260101000000_a.sql": OPEN, "99999_b.sql": "select 1;"})
    with pytest.raises(fix.FixError) as refused:
        fix.build(root)
    assert refused.value.code == 2
    assert "99999_b.sql" in str(refused.value)


def test_the_version_does_not_matter_when_nothing_will_be_written(tmp_path):
    root = _mixed(tmp_path, {"20260101000000_a.sql": "select 1;", "99999_b.sql": "select 1;"})
    assert fix.build(root).files == {}


def test_a_renderer_that_removes_a_read_is_refused():
    """The fix preserves every behaviour its finding did not name, reads above all: a
    table nobody can read is the locked-shut failure, and no audit finding reports it."""
    renderer = _broken(lambda sql: sql + (
        'drop policy if exists "Owners can view their tasks" on public.tasks;\n'))
    with pytest.raises(fix.FixError) as refused:
        fix.build(LOVABLE, renderer=renderer)
    assert 'the read granted by "Owners can view their tasks" on public.tasks is gone' \
        in str(refused.value)
