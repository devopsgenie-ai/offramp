"""The migration replay behind the RLS checks. RFC-0002: replayed in filename order,
six statement forms understood, and anything that touches tables, policies or RLS in
a form it does not understand makes the result unassessable rather than partial."""

from pathlib import Path

from detect.migrations import replay, split_statements


def _migrations(root: Path, files: dict[str, str]) -> Path:
    directory = root / "supabase" / "migrations"
    directory.mkdir(parents=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return root


def test_split_ignores_semicolons_in_comments_strings_and_dollar_bodies():
    text = (
        "-- a; comment\n"
        "create table a (x text default 'semi;colon');\n"
        "/* block; */ create function f() returns void as $$ begin select 1; end; $$ "
        "language plpgsql;\n"
        "create table \"B;\" (y int);\n"
    )
    statements = split_statements(text)
    assert len(statements) == 3
    assert [line for _, _, line in statements] == [2, 3, 4]


def test_rls_enabled_in_a_later_migration_is_enabled(tmp_path):
    root = _migrations(tmp_path, {
        "001_init.sql": "create table public.orders (id int);",
        "002_rls.sql": "alter table public.orders enable row level security;",
    })
    result = replay(root)
    assert result.unparseable == []
    assert result.tables["public.orders"].rls is True


def test_filename_order_not_directory_order(tmp_path):
    root = _migrations(tmp_path, {
        "20260102_b.sql": "alter table orders disable row level security;",
        "20260101_a.sql": "create table orders (id int); "
                          "alter table orders enable row level security;",
    })
    assert replay(root).tables["public.orders"].rls is False


def test_dropped_and_renamed_tables(tmp_path):
    root = _migrations(tmp_path, {
        "001.sql": "create table public.tmp (x text); create table public.old (x text);"
                   "alter table public.old enable row level security;",
        "002.sql": "drop table if exists public.tmp cascade;"
                   "alter table public.old rename to new;",
    })
    tables = replay(root).tables
    assert "public.tmp" not in tables
    assert "public.old" not in tables
    assert tables["public.new"].rls is True


def test_unqualified_names_default_to_public_and_quotes_are_stripped(tmp_path):
    root = _migrations(tmp_path, {"001.sql": 'CREATE TABLE "Profiles" (id int);'})
    assert "public.Profiles" in replay(root).tables


def test_create_if_not_exists_does_not_reset_rls(tmp_path):
    root = _migrations(tmp_path, {
        "001.sql": "create table t (id int); alter table t enable row level security;",
        "002.sql": "create table if not exists t (id int);",
    })
    assert replay(root).tables["public.t"].rls is True


def test_policies_are_parsed_with_command_roles_and_expressions(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create table t (id int, owner uuid);\n"
        "create policy \"Anyone reads\" on t for select using (true);\n"
        "create policy \"Owners write\" on public.t as permissive for update "
        "to authenticated using (auth.uid() = owner) with check ( ( true ) );\n"
        "create policy \"Admins\" on t as restrictive for all to anon, authenticated "
        "using (auth.jwt() ->> 'role' = 'admin');\n"
    )})
    policies = replay(root).tables["public.t"].policies
    read = policies["Anyone reads"]
    assert (read.command, read.roles, read.using, read.permissive) == (
        "select", ("public",), "true", True)
    write = policies["Owners write"]
    assert (write.command, write.roles, write.with_check) == (
        "update", ("authenticated",), "true")
    assert policies["Admins"].permissive is False
    assert policies["Admins"].line == 4


def test_drop_policy_removes_it(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create table t (id int);"
        "create policy \"p\" on t for delete using (true);"
        "drop policy if exists \"p\" on public.t;"
    )})
    assert replay(root).tables["public.t"].policies == {}


def test_a_do_block_that_creates_tables_is_unparseable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "do $$ begin execute 'create table ' || 'x (id int)'; end $$;"
    )})
    result = replay(root)
    assert len(result.unparseable) == 1
    assert result.unparseable[0].startswith("supabase/migrations/001.sql:1")


def test_function_bodies_are_not_replayed(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create or replace function f() returns trigger language plpgsql as $$ "
        "begin insert into public.profiles values (new.id); return new; end; $$;"
    )})
    assert replay(root).unparseable == []


def test_an_unrecognised_statement_touching_rls_is_unparseable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create table t (id int);\n"
        "alter table t add column x int, enable row level security;\n"
    )})
    assert replay(root).unparseable == [
        "supabase/migrations/001.sql:2: alter table statement not understood"]


def test_alter_policy_is_unparseable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "alter policy p on t using (true);"})
    assert len(replay(root).unparseable) == 1


def test_irrelevant_statements_are_ignored(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create extension if not exists pgcrypto;\n"
        "create index idx on t (id);\n"
        "insert into t values (1);\n"
        "grant select on t to anon;\n"
        "create trigger tr after insert on auth.users for each row execute function f();\n"
        "alter table t add column y text;\n"
        "create temporary table scratch (x int);\n"
    )})
    result = replay(root)
    assert result.unparseable == []
    assert result.tables == {}


def test_idempotent_do_block_is_replayed(tmp_path):
    """The shape the corpus showed most often: a DO block whose only control flow is
    an existence guard. Its end state does not depend on the guard, so it is replayed."""
    root = _migrations(tmp_path, {"001.sql": (
        "create table public.t (id int);\n"
        "DO $$\nBEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'open') THEN\n"
        "    CREATE POLICY \"open\" ON public.t FOR ALL TO anon USING (true) WITH CHECK (true);\n"
        "  END IF;\n"
        "  ALTER TABLE public.t ENABLE ROW LEVEL SECURITY;\n"
        "END $$;\n"
    )})
    result = replay(root)
    assert result.unparseable == []
    table = result.tables["public.t"]
    assert table.rls is True
    assert table.policies["open"].using == "true"
    assert table.policies["open"].evidence == "supabase/migrations/001.sql:5"


def test_do_block_with_exception_handler_is_replayed(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "create table t (id int);"
        "do $$ begin create policy p on t for select using (true); "
        "exception when duplicate_object then null; end $$;"
    )})
    result = replay(root)
    assert result.unparseable == []
    assert "p" in result.tables["public.t"].policies


def test_do_block_that_only_adds_a_constraint_is_ignored(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "do $$ begin alter table t add constraint c check (x > 0); "
        "exception when duplicate_object then null; end $$;"
    )})
    assert replay(root).unparseable == []


def test_do_block_with_dynamic_sql_stays_unparseable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "do $$ declare pol record; begin for pol in select policyname from pg_policies "
        "loop execute format('drop policy %I on t', pol.policyname); end loop; end $$;"
    )})
    assert len(replay(root).unparseable) == 1


def test_do_block_with_other_conditions_stays_unparseable(tmp_path):
    root = _migrations(tmp_path, {"001.sql": (
        "do $$ begin if current_setting('app.env') = 'prod' then "
        "alter table t enable row level security; end if; end $$;"
    )})
    assert len(replay(root).unparseable) == 1
