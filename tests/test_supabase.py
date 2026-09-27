"""Supabase: the detector (usage decides) and the three RLS checks. RFC-0002."""

from pathlib import Path

from checks import run_checks
from detect.supabase import detect_supabase
from scan import scan_repo

LOVABLE = Path("fixtures/lovable-vite-supabase")


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _app(root: Path, migrations: "dict[str, str] | None") -> Path:
    _write(root, "package.json", '{"dependencies": {"@supabase/supabase-js": "2", "vite": "5"}}')
    _write(root, "src/client.ts",
           "import { createClient } from '@supabase/supabase-js';\n"
           "export const s = createClient('https://abc.supabase.co', 'k');\n")
    for name, text in (migrations or {}).items():
        _write(root, f"supabase/migrations/{name}", text)
    return root


def _by_check(root: Path):
    findings, assessments = run_checks(root, scan_repo(root))
    status = {a.check: a for a in assessments}
    return findings, status


def test_supabase_is_detected_from_usage_with_mode_left_to_a_gap():
    result = scan_repo(LOVABLE)
    store = next(d for d in result.appspec.datastores if d.name == "supabase")
    assert (store.kind, store.mode, store.consumed_by) == ("postgres", None, ["web"])
    gap = next(g for g in result.gaps if g.id == "datastore.supabase.mode")
    assert gap.proposed == "external"
    assert gap.evidence == ["src/integrations/supabase/client.ts:2"]


def test_a_dependency_alone_is_not_usage(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"@supabase/supabase-js": "2"}}')
    _write(tmp_path, "src/app.ts", "export const x = 1;\n")
    services = scan_repo(tmp_path).appspec.services
    stores, gaps, _ = detect_supabase(tmp_path, services)
    assert stores == []
    assert [g.id for g in gaps] == ["datastore.supabase.disputed"]


def test_root_service_name_does_not_depend_on_the_checkout_directory():
    assert [s.name for s in scan_repo(LOVABLE).appspec.services] == ["web"]


def test_rls_checks_find_exactly_the_planted_problems():
    findings, status = _by_check(LOVABLE)
    ids = sorted(f.id for f in findings if f.check.startswith("supabase."))
    assert ids == [
        "supabase.rls.disabled.public.notes",
        "supabase.rls.permissive_write.public.tasks.authenticated-users-can-update-tasks",
        "supabase.rls.public_read.public.profiles.profiles-are-viewable-by-everyone",
    ]
    assert {c: status[c].status for c in status if c.startswith("supabase.")} == {
        "supabase.rls.disabled": "found",
        "supabase.rls.permissive_write": "found",
        "supabase.rls.public_read": "found",
    }


def test_rls_findings_cite_the_line_that_created_the_problem():
    findings, _ = _by_check(LOVABLE)
    notes = next(f for f in findings if f.id == "supabase.rls.disabled.public.notes")
    assert notes.severity == "critical"
    assert notes.evidence == ["supabase/migrations/20260110093000_init.sql:45"]


def test_clean_when_every_table_is_protected(tmp_path):
    _app(tmp_path, {"001.sql": "create table t (id int, o uuid);"
                               "alter table t enable row level security;"
                               "create policy p on t for all to authenticated "
                               "using (auth.uid() = o);"})
    _, status = _by_check(tmp_path)
    assert {status[c].status for c in status if c.startswith("supabase.")} == {"clean"}


def test_no_migrations_is_could_not_assess_never_clean(tmp_path):
    _app(tmp_path, None)
    _, status = _by_check(tmp_path)
    for check in ("supabase.rls.disabled", "supabase.rls.permissive_write",
                  "supabase.rls.public_read"):
        assert status[check].status == "could_not_assess"
        assert "no migrations" in status[check].reason


def test_unparseable_migration_is_could_not_assess_for_every_rls_check(tmp_path):
    _app(tmp_path, {"001.sql": "create table t (id int);",
                    "002.sql": "do $$ begin execute 'alter table t disable row level "
                               "security'; end $$;"})
    findings, status = _by_check(tmp_path)
    assert [f for f in findings if f.check.startswith("supabase.")] == []
    assert status["supabase.rls.disabled"].status == "could_not_assess"
    assert "supabase/migrations/002.sql:1" in status["supabase.rls.disabled"].reason


def test_not_applicable_without_supabase(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _, status = _by_check(tmp_path)
    assert status["supabase.rls.disabled"].status == "not_applicable"


def test_anon_insert_with_check_true_is_permissive_write(tmp_path):
    _app(tmp_path, {"001.sql": "create table leads (e text);"
                               "alter table leads enable row level security;"
                               "create policy \"Anyone can submit\" on leads for insert "
                               "to anon with check (true);"})
    findings, _ = _by_check(tmp_path)
    found = [f for f in findings if f.check == "supabase.rls.permissive_write"]
    # medium, not high: an anonymous INSERT is usually a form (see test_corpus_hardening)
    assert [f.severity for f in found] == ["medium"]
    assert "without signing in" in found[0].detail


def test_restrictive_true_policy_is_not_a_finding(tmp_path):
    _app(tmp_path, {"001.sql": "create table t (id int);"
                               "alter table t enable row level security;"
                               "create policy r on t as restrictive for all using (true);"})
    findings, _ = _by_check(tmp_path)
    assert [f for f in findings if f.check.startswith("supabase.")] == []
