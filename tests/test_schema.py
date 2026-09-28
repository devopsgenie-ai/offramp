"""The replay as a detector: `Datastore.schema`. RFC-0003, "What the renderer must know".

Columns are tracked separately from RLS. A column change the replay does not understand
costs that table its column list (`columns: null`), never the RLS checks their answer.
"""

import shutil
from pathlib import Path

import pytest

from checks import run_checks
from detect.migrations import detect_schema, replay
from scan import scan_repo

LOVABLE = Path("fixtures/lovable-vite-supabase")


def _migrations(root: Path, files: dict[str, str]) -> Path:
    directory = root / "supabase" / "migrations"
    directory.mkdir(parents=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return root


def _table(root: Path, name: str):
    schema = detect_schema(root)
    return next(table for table in schema.tables if table.name == name)


def _columns(table) -> dict:
    return {column.name: column for column in table.columns}


def test_create_table_columns_with_inline_constraints(tmp_path):
    root = _migrations(tmp_path, {"001_init.sql": """
        create table public.tasks (
          id uuid primary key default gen_random_uuid(),
          owner_id uuid not null references auth.users(id) on delete cascade,
          assignee uuid references auth.users,
          "Title" text not null default 'x, y',
          user_id uuid default auth.uid(),
          done boolean check (done is not null)
        );"""})
    columns = _columns(_table(root, "public.tasks"))
    assert list(columns) == ["id", "owner_id", "assignee", "Title", "user_id", "done"]
    assert columns["id"].primary_key and not columns["id"].nullable
    assert columns["owner_id"].references == "auth.users.id"
    assert columns["owner_id"].nullable is False
    # auth.users' primary key is id: a bare `references auth.users` means auth.users.id
    assert columns["assignee"].references == "auth.users.id"
    assert columns["assignee"].nullable is True
    assert columns["user_id"].default == "auth.uid()"
    # `not null` inside a CHECK is not a NOT NULL constraint
    assert columns["done"].nullable is True
    assert columns["Title"].references is None


def test_table_level_primary_and_foreign_keys(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table profiles (id uuid, name text, primary key (id),
          constraint profiles_id_fkey foreign key (id) references auth.users (id));
        create table posts (id bigint generated always as identity primary key,
          author uuid, foreign key (author) references public.profiles);"""})
    profiles = _columns(_table(root, "public.profiles"))
    assert profiles["id"].primary_key and profiles["id"].references == "auth.users.id"
    # A bare reference resolves to the referenced table's primary key when it is known.
    assert _columns(_table(root, "public.posts"))["author"].references == "public.profiles.id"


def test_alter_table_column_changes(tmp_path):
    root = _migrations(tmp_path, {
        "001.sql": "create table public.notes (id uuid primary key, body text, tmp int);",
        "002.sql": """
            alter table public.notes add column user_id uuid;
            alter table public.notes add constraint notes_user_fk
              foreign key (user_id) references auth.users(id);
            alter table public.notes alter column user_id set not null,
              alter column user_id set default auth.uid();
            alter table public.notes drop column if exists tmp;
            alter table only public.notes rename column body to content;
            alter table public.notes add column if not exists extra text,
              add column more text not null;""",
    })
    table = _table(root, "public.notes")
    columns = _columns(table)
    assert list(columns) == ["id", "content", "user_id", "extra", "more"]
    assert columns["user_id"].references == "auth.users.id"
    assert columns["user_id"].nullable is False
    assert columns["user_id"].default == "auth.uid()"
    assert columns["more"].nullable is False


def test_drop_not_null_makes_a_column_nullable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "create table t (id int, o uuid not null);"
                                             "alter table t alter column o drop not null;"})
    assert _columns(_table(root, "public.t"))["o"].nullable is True


def test_dropping_a_known_foreign_key_constraint_clears_the_reference(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int primary key, user_id uuid references auth.users(id));
        alter table t drop constraint t_user_id_fkey;"""})
    assert _columns(_table(root, "public.t"))["user_id"].references is None


def test_dropping_an_unknown_constraint_on_a_table_without_foreign_keys_is_harmless(tmp_path):
    """The corpus drops a constraint `if exists` right before adding it. Nothing an
    unknown constraint could have held was ever recorded for a table with no foreign
    key, so there is nothing to lose."""
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int primary key, about text);
        alter table t drop constraint if exists t_about_length;
        alter table t add constraint t_about_length check (length(about) < 500);"""})
    assert [c.name for c in _table(root, "public.t").columns] == ["id", "about"]


def test_dropping_an_unknown_constraint_makes_columns_unknown_not_rls(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int primary key, user_id uuid references auth.users(id));
        alter table t enable row level security;
        alter table t drop constraint some_name_we_never_saw;"""})
    schema = detect_schema(root)
    assert schema.unreplayable == []
    table = _table(root, "public.t")
    assert table.columns is None
    assert table.rls is True


def test_a_column_statement_not_understood_costs_columns_only(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int);
        alter table t enable row level security;
        alter table t inherit other_table;"""})
    assert detect_schema(root).unreplayable == []
    table = _table(root, "public.t")
    assert (table.columns, table.rls) == (None, True)


@pytest.mark.parametrize("statement", [
    "create table t (like other including all);",
    "create table t as select 1 as x;",
    "create table t partition of other for values in (1);",
])
def test_create_table_without_a_column_list_has_unknown_columns(tmp_path, statement):
    root = _migrations(tmp_path, {"001.sql": statement})
    assert _table(root, "public.t").columns is None


def test_harmless_alter_table_actions_keep_columns(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int);
        alter table t owner to postgres;
        alter table t replica identity full;
        alter table t alter column id type bigint;
        alter table t alter column "id" add generated by default as identity (
          sequence name public.t_id_seq start with 1 increment by 1);"""})
    assert [c.name for c in _table(root, "public.t").columns] == ["id"]


def test_dropping_a_check_constraint_by_its_default_name_keeps_columns(tmp_path):
    """PostgreSQL names an unnamed CHECK `<table>_<column>_check`. The corpus drops
    them constantly to widen an enum-like column; that changes no owner evidence."""
    root = _migrations(tmp_path, {"001.sql": """
        create table t (id int, status text check (status in ('a', 'b')));
        alter table t drop constraint if exists t_status_check;
        alter table t drop constraint if exists t_kind_check;"""})
    assert [c.name for c in _table(root, "public.t").columns] == ["id", "status"]


def test_a_policy_records_columns_renamed_after_it(tmp_path):
    root = _migrations(tmp_path, {
        "001.sql": "create table d (id int, author uuid);"
                   "alter table d enable row level security;"
                   "create policy \"own\" on d for select using (auth.uid() = author);",
        "002.sql": "alter table d rename column author to author_id;"
                   "create policy \"later\" on d for delete using (auth.uid() = author_id);",
    })
    policies = {p.name: p for p in _table(root, "public.d").policies}
    assert policies["own"].renamed_since == ["author", "author_id"]
    assert policies["later"].renamed_since == []


def test_renaming_a_table_updates_references_to_it(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create table people (id uuid primary key references auth.users(id));
        create table notes (id int, person uuid references people(id));
        alter table people rename to profiles;"""})
    assert _columns(_table(root, "public.notes"))["person"].references == "public.profiles.id"


def test_a_table_created_elsewhere_has_unknown_columns(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "alter table public.ext enable row level security;"})
    table = _table(root, "public.ext")
    assert (table.rls, table.columns, table.evidence) == (True, None, None)


def test_schema_records_migrations_latest_and_policies(tmp_path):
    root = _migrations(tmp_path, {
        "20260101000000_a.sql": "create table t (id int, o uuid);"
                                "alter table t enable row level security;"
                                "create policy \"P\" on t for update to authenticated "
                                "using (auth.uid() = o) with check (true);",
        "20260102000000-uuid.sql": "select 1;",
    })
    schema = detect_schema(root)
    assert schema.migrations == ["supabase/migrations/20260101000000_a.sql",
                                 "supabase/migrations/20260102000000-uuid.sql"]
    assert schema.latest == "20260102000000"
    (policy,) = _table(root, "public.t").policies
    assert (policy.name, policy.command, policy.roles) == ("P", "update", ["authenticated"])
    assert (policy.using, policy.with_check) == ("auth.uid() = o", "true")
    assert policy.evidence == "supabase/migrations/20260101000000_a.sql:1"


def test_no_sql_files_means_no_schema(tmp_path):
    (tmp_path / "supabase").mkdir()
    assert detect_schema(tmp_path) is None


def test_unreplayable_or_unordered_means_no_tables(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "create table t (id int);",
                                  "002.sql": "do $$ begin execute 'drop table t'; end $$;"})
    schema = detect_schema(root)
    assert schema.tables is None
    assert schema.unreplayable == [
        "supabase/migrations/002.sql:1: do block with dynamic SQL touching tables or RLS"]

    other = tmp_path / "other"
    _migrations(other, {"001.sql": "create table t (id int);", "seed.sql": "select 1;"})
    schema = detect_schema(other)
    assert (schema.tables, schema.unordered) == (None, ["supabase/migrations/seed.sql"])


def test_scan_puts_the_schema_on_the_supabase_datastore():
    result = scan_repo(LOVABLE)
    store = next(d for d in result.appspec.datastores if d.name == "supabase")
    assert store.schema is result.schema
    names = [table.name for table in store.schema.tables]
    assert names == ["private.audit_log", "public.notes", "public.orders",
                     "public.profiles", "public.tasks"]
    assert store.schema.latest == "20260115110000"


def test_other_datastores_have_no_schema():
    result = scan_repo(Path("fixtures/emergent-fastapi-mongo"))
    assert [d.schema for d in result.appspec.datastores] == [None]


def test_the_rls_checks_read_the_scanned_schema_and_do_not_replay(tmp_path):
    """One replay, two consumers: the audit and the fix can never disagree. Removing
    the migrations after the scan proves the checks answer from the scan alone."""
    clone = tmp_path / "app"
    shutil.copytree(LOVABLE, clone)
    result = scan_repo(clone)
    shutil.rmtree(clone / "supabase" / "migrations")
    findings, _ = run_checks(clone, result)
    assert "supabase.rls.disabled.public.notes" in {f.id for f in findings}


def test_replay_still_answers_for_callers_that_want_the_raw_state(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "create table t (id int);"})
    assert replay(root).tables["public.t"].rls is False
