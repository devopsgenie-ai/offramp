"""The fix renderer, as a pure function. RFC-0003, "What the renderer emits".

The rule behind every case: a fix closes exactly the exposure its finding names,
preserves every other behaviour, and turns each remaining decision into a gap.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from audit import audit_repo
from detect.migrations import split_statements
from fix_render import MIGRATION_SUFFIX, default_version, render_fix
from fix_sql import quote_ident, quote_table, sql_comment
from scan import scan_repo
from spec import WriteScope

LOVABLE = Path("fixtures/lovable-vite-supabase")
OWNERS = Path("fixtures/lovable-rls-owners")


def _render(root: Path, version: str | None = None, appspec=None):
    scan = scan_repo(root)
    appspec = appspec or scan.appspec
    return render_fix(appspec, audit_repo(root), version or default_version(appspec))


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _app(root: Path, *migrations: str) -> Path:
    _write(root, "package.json", '{"dependencies": {"@supabase/supabase-js": "2"}}')
    _write(root, "src/client.ts", "import { createClient } from '@supabase/supabase-js';\n")
    for index, text in enumerate(migrations):
        _write(root, f"supabase/migrations/2026010100000{index}_m.sql", text)
    return root


def _migration(rendering) -> str:
    (path,) = [p for p in rendering.files if p.endswith(MIGRATION_SUFFIX)]
    return rendering.files[path]


def _statements(sql: str) -> list[str]:
    return [" ".join(original.split()) for original, _, _ in split_statements(sql)]


def _status(rendering) -> dict:
    return {o.id: o.status for o in rendering.outcomes}


# -- identifiers ------------------------------------------------------------------------

@pytest.mark.parametrize("name, quoted", [
    ("tasks", "tasks"),
    ("owner_id", "owner_id"),
    ("Tasks", '"Tasks"'),
    ("user", '"user"'),              # reserved
    ("order", '"order"'),
    ("2fa", '"2fa"'),
    ('a"b', '"a""b"'),
    ("x; drop table y; --", '"x; drop table y; --"'),
    ("new\nline", '"new\nline"'),
])
def test_identifiers_are_quoted_by_postgresql_rules(name, quoted):
    assert quote_ident(name) == quoted


def test_a_table_name_quotes_each_part():
    assert quote_table("public.tasks") == "public.tasks"
    assert quote_table('public.odd "name".x') == 'public."odd ""name"".x"'


def test_comment_text_can_never_end_the_comment_line():
    assert sql_comment("fixed: public.two\nlines\r\nhere") == "-- fixed: public.two\\nlines\\r\\nhere"


# -- versions ---------------------------------------------------------------------------

def test_the_default_version_is_the_latest_plus_one_without_a_clock():
    appspec = scan_repo(LOVABLE).appspec
    assert default_version(appspec) == "20260115110001"


def test_a_short_version_keeps_its_width(tmp_path):
    root = _app(tmp_path, "create table t (id int);")
    (root / "supabase/migrations/20260101000000_m.sql").rename(root / "supabase/migrations/009_m.sql")
    assert default_version(scan_repo(root).appspec) == "010"


# -- the lovable fixture ------------------------------------------------------------------

def test_the_permissive_update_is_dropped_and_replaced_for_its_owner():
    rendering = _render(LOVABLE)
    sql = _migration(rendering)
    statements = _statements(sql)
    assert statements == [
        'drop policy if exists "Authenticated users can update tasks" on public.tasks',
        'create policy "offramp: owner can update" on public.tasks as permissive '
        'for update to authenticated using ((select auth.uid()) = owner_id) '
        'with check ((select auth.uid()) = owner_id)',
    ]
    assert ("-- Owner: owner_id, because:\n"
            "--   policy \"Owners can view their tasks\" compares it to auth.uid() "
            "(supabase/migrations/20260110093000_init.sql:28)\n") in sql


def test_an_undetermined_table_gets_an_inert_block_and_its_gap():
    sql = _migration(_render(LOVABLE))
    assert "-- NOT FIXED: supabase.rls.disabled.public.notes" in sql
    assert "-- Gap: datastore.supabase.table.public.notes.write_scope" in sql
    assert "--   alter table public.notes enable row level security;" in sql
    assert not any("public.notes" in s for s in _statements(sql))


def test_every_finding_gets_an_outcome():
    rendering = _render(LOVABLE)
    assert _status(rendering) == {
        "supabase.rls.permissive_write.public.tasks.authenticated-users-can-update-tasks": "fixed",
        "supabase.rls.disabled.public.notes": "gap",
        "supabase.rls.public_read.public.profiles.profiles-are-viewable-by-everyone":
            "not_in_scope",
    }
    notes = next(o for o in rendering.outcomes if o.table == "public.notes")
    assert notes.gap == "datastore.supabase.table.public.notes.write_scope"


def test_the_output_tree_names_the_version():
    rendering = _render(LOVABLE)
    assert sorted(rendering.files) == [
        "FIXES.md", "fixes.json", "supabase/migrations/20260115110001_offramp_rls.sql"]
    document = json.loads(rendering.files["fixes.json"])
    assert document["version"] == "20260115110001"
    assert document["migration"] == "supabase/migrations/20260115110001_offramp_rls.sql"


def test_rendering_is_deterministic():
    assert _render(LOVABLE).files == _render(LOVABLE).files


# -- the awkward cases ----------------------------------------------------------------------

def _table_statements(rendering, table: str) -> list[str]:
    return [s for s in _statements(_migration(rendering)) if f" {table}" in s]


def test_rls_off_with_an_owner_enables_scopes_writes_and_preserves_reads():
    rendering = _render(OWNERS)
    assert _table_statements(rendering, "public.journal") == [
        "alter table public.journal enable row level security",
        'create policy "offramp: owner can insert" on public.journal as permissive '
        "for insert to authenticated with check ((select auth.uid()) = user_id)",
        'create policy "offramp: owner can update" on public.journal as permissive '
        "for update to authenticated using ((select auth.uid()) = user_id) "
        "with check ((select auth.uid()) = user_id)",
        'create policy "offramp: owner can delete" on public.journal as permissive '
        "for delete to authenticated using ((select auth.uid()) = user_id)",
        'create policy "offramp: anyone can read" on public.journal as permissive '
        "for select to anon, authenticated using (true)",
    ]
    assert ("supabase.rls.public_read.public.journal.offramp-anyone-can-read"
            in rendering.expected_after)


def test_an_all_true_policy_is_replaced_and_its_read_half_kept():
    rendering = _render(OWNERS)
    assert _table_statements(rendering, "public.comments") == [
        'drop policy if exists "Anyone can do anything with comments" on public.comments',
        'create policy "offramp: owner can insert" on public.comments as permissive '
        "for insert to authenticated with check ((select auth.uid()) = profile_id)",
        'create policy "offramp: owner can update" on public.comments as permissive '
        "for update to authenticated using ((select auth.uid()) = profile_id) "
        "with check ((select auth.uid()) = profile_id)",
        'create policy "offramp: owner can delete" on public.comments as permissive '
        "for delete to authenticated using ((select auth.uid()) = profile_id)",
        'create policy "offramp: anyone can read" on public.comments as permissive '
        "for select to public using (true)",
    ]


def test_a_replacement_for_an_anonymous_policy_says_why_it_is_for_authenticated_once():
    sql = _migration(_render(OWNERS))
    block = sql[sql.index("-- fixed: supabase.rls.permissive_write.public.comments"):]
    block = block[:block.index("\n\n")]
    assert block.count("auth.uid() is null for a request that is not signed in") == 1


def test_an_existing_owner_policy_is_not_duplicated():
    rendering = _render(OWNERS)
    assert _table_statements(rendering, "public.projects") == [
        'drop policy if exists "Members can update projects" on public.projects']
    sql = _migration(rendering)
    assert 'already covered by "Owners can update projects"' in sql


def test_an_anonymous_insert_and_the_undetermined_tables_are_untouched():
    rendering = _render(OWNERS)
    for table in ("public.waitlist", "public.notes", "public.assignments",
                  "public.invoices", "public.drafts"):
        assert _table_statements(rendering, table) == []
    status = _status(rendering)
    assert status["supabase.rls.permissive_write.public.waitlist.anyone-can-join-the-waitlist"] \
        == "not_in_scope"
    assert status["supabase.rls.permissive_write.public.drafts.anyone-can-delete-drafts"] == "gap"


def test_fixes_md_names_nullable_owners_and_missing_defaults():
    fixes = _render(OWNERS).files["FIXES.md"]
    bookmarks = fixes.split("### `public.bookmarks`")[1].split("###")[0]
    assert "allows NULL" in bookmarks
    assert "no default" in bookmarks
    journal = fixes.split("### `public.journal`")[1].split("###")[0]
    assert "allows NULL" not in journal
    assert "no default" not in journal


def test_fixes_md_lists_gaps_and_decisions():
    fixes = _render(OWNERS).files["FIXES.md"]
    assert "datastore.supabase.table.public.assignments.write_scope" in fixes
    assert "supabase.rls.permissive_write.public.waitlist.anyone-can-join-the-waitlist" in fixes
    assert "Re-run `audit`" in fixes


# -- answers that v1 cannot produce yet, but the renderer must honour -----------------------

def _answered(root: Path, table: str, scope: WriteScope):
    appspec = scan_repo(root).appspec
    stores = []
    for store in appspec.datastores:
        if store.schema is not None and store.schema.tables:
            tables = [replace(t, write_scope=scope) if t.name == table else t
                      for t in store.schema.tables]
            store = replace(store, schema=replace(store.schema, tables=tables))
        stores.append(store)
    return replace(appspec, datastores=stores)


def test_server_only_enables_rls_with_no_policy():
    appspec = _answered(LOVABLE, "public.notes", WriteScope(kind="server_only", column=None))
    rendering = _render(LOVABLE, appspec=appspec)
    assert _table_statements(rendering, "public.notes") == [
        "alter table public.notes enable row level security"]
    assert _status(rendering)["supabase.rls.disabled.public.notes"] == "fixed"


def test_server_only_drops_an_open_write_without_replacing_it():
    appspec = _answered(LOVABLE, "public.tasks", WriteScope(kind="server_only", column=None))
    rendering = _render(LOVABLE, appspec=appspec)
    assert _table_statements(rendering, "public.tasks") == [
        'drop policy if exists "Authenticated users can update tasks" on public.tasks']


# -- nothing to do ------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "emergent-fastapi-mongo", "lovable-no-migrations", "lovable-dynamic-sql"])
def test_no_rls_finding_means_no_output(fixture):
    assert _render(Path("fixtures") / fixture).files == {}


def test_only_gaps_means_no_migration_but_an_explanation(tmp_path):
    root = _app(tmp_path, "create table notes (id int, body text);")
    rendering = _render(root)
    assert sorted(rendering.files) == ["FIXES.md", "fixes.json"]
    assert json.loads(rendering.files["fixes.json"])["migration"] is None


def test_a_read_condition_the_replay_cannot_reproduce_is_not_rewritten(tmp_path):
    """The replay masks string literals, so `status = 'open'` is stored as `status = ''`.
    Rewriting it would change what anyone can read, so the table is left alone."""
    root = _app(tmp_path, """
        create table t (id int, user_id uuid references auth.users(id), status text);
        alter table t enable row level security;
        create policy "all" on t for all using (status = 'open') with check (true);""")
    rendering = _render(root)
    assert _status(rendering) == {"supabase.rls.permissive_write.public.t.all": "not_in_scope"}
    assert not any(path.endswith(".sql") for path in rendering.files)
    assert json.loads(rendering.files["fixes.json"])["migration"] is None


HOSTILE = Path("fixtures/lovable-hostile-identifiers")


def test_hostile_identifiers_replay_to_exactly_the_intended_statements():
    """RFC-0003, Testing. A name built to smuggle a statement renders as an odd name."""
    from detect.migrations import detect_schema

    rendering = _render(HOSTILE)
    sql = _migration(rendering)
    statements = [masked for _, masked, _ in split_statements(sql)]
    assert len(statements) == rendering.statements == 12
    assert all(s.lower().startswith(("alter table public.", "create policy \"offramp: ",
                                      "drop policy if exists \"anyone can edit"))
               for s in statements)
    before = detect_schema(HOSTILE)
    after = detect_schema(HOSTILE, extra=[(rendering.migration, sql)])
    assert [t.name for t in after.tables] == [t.name for t in before.tables]
    assert all(t.rls for t in after.tables)


def test_the_owner_comment_quotes_the_column():
    sql = _migration(_render(HOSTILE))
    assert '-- Owner: "Owner ID", because:' in sql
    assert '-- Owner: "user", because:' in sql
