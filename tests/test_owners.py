"""Owner detection: `Table.write_scope`, and the gap when it cannot be proven.
RFC-0003, "Owner detection: evidence, never names".

A wrong owner column is a lockout, or one user editing another's rows, with a clean
audit. So an owner is detected only when exactly one column is supported by evidence and
the evidence agrees. A column's *name* is at most the gap's proposed answer.
"""

from pathlib import Path

import pytest

from detect.migrations import detect_schema
from detect.owners import detect_write_scopes, owner_column
from gaps import answerable_set
from scan import scan_repo

LOVABLE = Path("fixtures/lovable-vite-supabase")
PREFIX = "/datastores/0/schema"


def _migrations(root: Path, *statements: str) -> Path:
    directory = root / "supabase" / "migrations"
    directory.mkdir(parents=True)
    for index, text in enumerate(statements):
        (directory / f"2026010100000{index}_m.sql").write_text(text, encoding="utf-8")
    return root


def _scopes(root: Path):
    schema, gaps = detect_write_scopes(detect_schema(root), PREFIX)
    scopes = {table.name: table.write_scope for table in schema.tables}
    return scopes, {gap.id: gap for gap in gaps}


def _gap_id(table: str) -> str:
    return f"datastore.supabase.table.{table}.write_scope"


@pytest.mark.parametrize("expression, column", [
    ("auth.uid() = user_id", "user_id"),
    ("user_id = auth.uid()", "user_id"),
    ("(select auth.uid()) = owner_id", "owner_id"),
    ("( select auth.uid() ) = owner_id", "owner_id"),
    ("owner_id = (select auth.uid())", "owner_id"),
    ("tasks.owner_id = auth.uid()", "owner_id"),
    ('auth.uid() = "user_id"', "user_id"),
    ("auth.uid() = user_id or is_admin()", None),
    ("auth.uid()::text = user_id", None),
    ("auth.uid() = user_id and done", None),
    ("auth.uid() is not null", None),
    ("true", None),
    (None, None),
])
def test_only_an_exact_owner_comparison_names_a_column(expression, column):
    assert owner_column(expression, "public.tasks") == column


def test_policy_basis(tmp_path):
    scopes, gaps = _scopes(_migrations(tmp_path, """
        create table tasks (id uuid primary key, owner_id uuid not null, title text);
        alter table tasks enable row level security;
        create policy "own" on tasks for select to authenticated
          using (auth.uid() = owner_id);"""))
    scope = scopes["public.tasks"]
    assert (scope.kind, scope.column) == ("owner", "owner_id")
    assert scope.basis == [
        'policy "own" compares it to auth.uid() '
        '(supabase/migrations/20260101000000_m.sql:4)']
    assert gaps == {}


def test_foreign_key_basis(tmp_path):
    scopes, _ = _scopes(_migrations(tmp_path, """
        create table journal (id uuid primary key,
          user_id uuid not null references auth.users(id), entry text);"""))
    assert scopes["public.journal"].column == "user_id"
    assert scopes["public.journal"].basis == ["it references auth.users.id"]


def test_a_primary_key_referencing_auth_users_is_its_own_owner(tmp_path):
    scopes, _ = _scopes(_migrations(tmp_path, """
        create table profiles (id uuid primary key references auth.users(id), name text);"""))
    assert scopes["public.profiles"].column == "id"


def test_one_hop_through_a_profiles_style_table(tmp_path):
    scopes, _ = _scopes(_migrations(tmp_path, """
        create table profiles (id uuid primary key references auth.users(id));
        create table comments (id uuid primary key,
          profile_id uuid not null references profiles(id), body text);"""))
    scope = scopes["public.comments"]
    assert scope.column == "profile_id"
    assert scope.basis == [
        "it references public.profiles.id, whose primary key references auth.users.id"]


def test_two_hops_is_not_evidence(tmp_path):
    scopes, _ = _scopes(_migrations(tmp_path, """
        create table profiles (id uuid primary key references auth.users(id));
        create table teams (id uuid primary key, owner uuid references profiles(id));
        create table docs (id uuid primary key, team_id uuid references teams(id));"""))
    assert scopes["public.docs"] is None


def test_two_foreign_key_candidates_are_a_gap_listing_both(tmp_path):
    scopes, gaps = _scopes(_migrations(tmp_path, """
        create table assignments (id uuid primary key,
          created_by uuid not null references auth.users(id),
          assignee_id uuid references auth.users(id), title text);"""))
    assert scopes["public.assignments"] is None
    gap = gaps[_gap_id("public.assignments")]
    assert gap.severity == "blocking"
    assert "`assignee_id`" in gap.question and "`created_by`" in gap.question
    # created_by is an owner-sounding name: proposed for a human, never rendered
    assert gap.proposed == {"kind": "owner", "column": "created_by"}
    assert gap.confidence == "medium"


def test_policy_and_foreign_key_bases_that_disagree_are_a_gap(tmp_path):
    scopes, gaps = _scopes(_migrations(tmp_path, """
        create table invoices (id uuid primary key,
          issuer_id uuid not null references auth.users(id), customer_id uuid not null);
        alter table invoices enable row level security;
        create policy "view" on invoices for select to authenticated
          using (auth.uid() = customer_id);
        create policy "open" on invoices for delete to authenticated using (true);"""))
    assert scopes["public.invoices"] is None
    gap = gaps[_gap_id("public.invoices")]
    assert gap.proposed is None and gap.confidence == "low"


def test_policy_and_foreign_key_bases_that_agree_are_both_cited(tmp_path):
    scopes, _ = _scopes(_migrations(tmp_path, """
        create table notes (id uuid primary key, user_id uuid references auth.users(id));
        alter table notes enable row level security;
        create policy "mine" on notes for select using ((select auth.uid()) = user_id);"""))
    assert scopes["public.notes"].column == "user_id"
    assert len(scopes["public.notes"].basis) == 2


def test_a_column_renamed_after_the_policy_that_named_it_is_a_gap(tmp_path):
    scopes, gaps = _scopes(_migrations(
        tmp_path,
        """create table drafts (id uuid primary key, author uuid not null, body text);
           alter table drafts enable row level security;
           create policy "read" on drafts for select using (auth.uid() = author);
           create policy "open" on drafts for delete using (true);""",
        "alter table drafts rename column author to author_id;",
    ))
    assert scopes["public.drafts"] is None
    gap = gaps[_gap_id("public.drafts")]
    assert "renamed" in gap.question
    assert gap.proposed == {"kind": "owner", "column": "author_id"}


def test_a_name_alone_is_never_detected(tmp_path):
    scopes, gaps = _scopes(_migrations(tmp_path, """
        create table notes (id uuid primary key, user_id uuid not null, body text);"""))
    assert scopes["public.notes"] is None
    gap = gaps[_gap_id("public.notes")]
    assert gap.proposed == {"kind": "owner", "column": "user_id"}
    assert gap.confidence == "medium"
    assert gap.pointers == [f"{PREFIX}/tables/0/write_scope"]
    assert gap.evidence == ["supabase/migrations/20260101000000_m.sql:2"]


def test_unknown_columns_are_never_evidence(tmp_path):
    scopes, gaps = _scopes(_migrations(tmp_path, """
        create table t (id int, o uuid references auth.users(id));
        alter table t inherit other;"""))
    assert scopes["public.t"] is None
    assert _gap_id("public.t") in gaps


def test_a_gap_is_asked_only_where_writes_are_open(tmp_path):
    """Asking about every table adds a question per table and most have no
    consequence. Only RLS off, or a permissive UPDATE/DELETE/ALL `true`, needs one."""
    _, gaps = _scopes(_migrations(tmp_path, """
        create table closed (id int, body text);
        alter table closed enable row level security;
        create table form (id int, email text);
        alter table form enable row level security;
        create policy "join" on form for insert to anon with check (true);
        create table readable (id int);
        alter table readable enable row level security;
        create policy "r" on readable for select using (true);
        create table open_update (id int);
        alter table open_update enable row level security;
        create policy "u" on open_update for update to authenticated using (true);
        create table private.hidden (id int);"""))
    assert sorted(gaps) == [_gap_id("public.open_update")]


def test_the_write_scope_pointer_is_answerable_and_the_name_is_not():
    result = scan_repo(LOVABLE)
    declared, problems = answerable_set(result.gaps, result.appspec)
    assert problems == []
    notes = declared[_gap_id("public.notes")]
    store = next(i for i, d in enumerate(result.appspec.datastores) if d.name == "supabase")
    tables = [t.name for t in result.appspec.datastores[store].schema.tables]
    assert notes["pointers"] == [
        f"/datastores/{store}/schema/tables/{tables.index('public.notes')}/write_scope"]


def test_the_lovable_fixture_detects_tasks_through_its_select_policy():
    result = scan_repo(LOVABLE)
    tables = {t.name: t for t in result.schema.tables}
    assert tables["public.tasks"].write_scope.column == "owner_id"
    assert tables["public.notes"].write_scope is None
    assert _gap_id("public.notes") in {gap.id for gap in result.gaps}
    assert _gap_id("public.tasks") not in {gap.id for gap in result.gaps}


def test_the_enum_check_rejects_an_owner_that_is_not_a_column():
    from dataclasses import replace

    from spec import WriteScope, check_enums

    result = scan_repo(LOVABLE)
    store = next(d for d in result.appspec.datastores if d.name == "supabase")
    tables = [replace(t, write_scope=WriteScope(kind="owner", column="nope"))
              if t.name == "public.tasks" else t for t in store.schema.tables]
    broken = replace(store, schema=replace(store.schema, tables=tables))
    appspec = replace(result.appspec, datastores=[broken])
    assert check_enums(appspec, []) == [
        "table public.tasks: owner 'nope' is not a column"]
