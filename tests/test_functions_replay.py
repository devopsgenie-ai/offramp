"""The replay learns functions and EXECUTE grants. RFC-0004, build step 1.

Functions are tracked apart from tables: a function statement the replay does not
understand makes `Schema.functions` null and nothing else. It never makes the migration
unreplayable, so it never costs the RLS checks their answer.
"""

from pathlib import Path

from detect.migrations import detect_schema

ADD_POINTS = """
create or replace function public.add_points(p_user_id uuid, p_amount int)
returns void
language plpgsql
security definer
as $$
begin
  update public.user_points
     set points = points + p_amount
   where user_id = p_user_id;
end;
$$;
"""


def _migrations(root: Path, files: dict[str, str]) -> Path:
    directory = root / "supabase" / "migrations"
    directory.mkdir(parents=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return root


def _functions(root: Path) -> dict:
    schema = detect_schema(root)
    assert schema.functions is not None, schema.functions_unreplayable
    return {function.signature: function for function in schema.functions}


def _only(root: Path):
    functions = _functions(root)
    assert len(functions) == 1, list(functions)
    return next(iter(functions.values()))


# -- create function ---------------------------------------------------------------------

def test_a_definer_function_is_recorded_with_its_writes(tmp_path):
    root = _migrations(tmp_path, {"001_points.sql": ADD_POINTS})
    function = _only(root)
    assert function.name == "public.add_points"
    assert function.signature == "public.add_points(uuid, integer)"
    assert function.params == ["p_user_id", "p_amount"]
    assert function.definer is True
    assert function.trigger is False
    assert function.owner is None
    assert function.grants == ["anon", "authenticated", "public"]
    assert function.executable_by == ["anon", "authenticated"]
    assert function.compares_caller == []
    assert function.role_check is False
    assert function.evidence == "supabase/migrations/001_points.sql:2"
    [write] = function.writes
    assert (write.verb, write.table, write.uses) == ("update", "public.user_points",
                                                     ["p_user_id", "p_amount"])
    assert write.line == 8


def test_clause_order_quoting_and_an_unqualified_name(tmp_path):
    """pg_dump quotes every name and type and writes the clauses in its own order."""
    root = _migrations(tmp_path, {"001.sql": '''
        CREATE OR REPLACE FUNCTION "add_points"("p_user_id" "uuid", "p_amount" "int4")
        RETURNS "void" SECURITY DEFINER SET "search_path" TO '' LANGUAGE "plpgsql" AS $fn$
        BEGIN
          DELETE FROM "public"."user_points" WHERE "user_id" = "p_user_id";
        END;
        $fn$;'''})
    function = _only(root)
    assert function.signature == "public.add_points(uuid, integer)"
    assert function.definer is True
    [write] = function.writes
    assert (write.verb, write.table, write.uses) == ("delete", "public.user_points",
                                                     ["p_user_id"])


def test_a_single_quoted_body_is_read(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.grant_admin(target_user_id uuid) returns void
        language sql security definer
        as 'insert into public.user_roles (user_id, role) values (target_user_id, ''admin'')';
    """})
    [write] = _only(root).writes
    assert (write.verb, write.table, write.uses) == ("insert", "public.user_roles",
                                                     ["target_user_id"])


def test_positional_parameters_count_as_uses(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.wipe(uuid, text) returns void language sql security definer
        as $$ delete from public.notes where owner = $1 $$;
        create function public.rename(p_user_id uuid, p_name text) returns void
        language sql security definer
        as $$ update public.profiles set name = $2 where id = $1 $$;
    """})
    functions = _functions(root)
    wipe = functions["public.wipe(uuid, text)"]
    assert wipe.params == ["", ""]
    assert wipe.writes[0].uses == ["$1"]
    rename = functions["public.rename(uuid, text)"]
    assert rename.writes[0].uses == ["p_user_id", "p_name"]


def test_out_parameters_are_not_part_of_the_signature(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.total(p_user_id uuid, out total int, inout n bigint)
        returns record language sql as $$ select 1, 2 $$;"""})
    function = _only(root)
    assert function.signature == "public.total(uuid, bigint)"
    assert function.params == ["p_user_id", "n"]


def test_multiword_and_aliased_types_normalise(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.f(a double precision, b varchar(20), timestamptz, d bool[])
        returns void language sql as $$ select 1 $$;"""})
    assert _only(root).signature == (
        "public.f(double precision, character varying, timestamp with time zone, "
        "boolean[])")


def test_a_trigger_function_is_marked(tmp_path):
    """The Supabase `handle_new_user` shape, from fixtures/lovable-vite-supabase."""
    root = _migrations(tmp_path, {"001.sql": """
        CREATE OR REPLACE FUNCTION public.handle_new_user()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = ''
        AS $$
        BEGIN
          INSERT INTO public.profiles (id, display_name)
          VALUES (new.id, new.raw_user_meta_data ->> 'display_name');
          RETURN new;
        END;
        $$;"""})
    function = _only(root)
    assert (function.trigger, function.definer, function.params) == (True, True, [])
    assert [(w.verb, w.table, w.uses) for w in function.writes] == [
        ("insert", "public.profiles", [])]


def test_an_invoker_function_is_not_definer(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.f(p_user_id uuid) returns void language sql
        security invoker as $$ delete from t where id = p_user_id $$;"""})
    assert _only(root).definer is False


def test_dynamic_sql_or_another_language_means_the_body_is_unread(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.dyn(p_user_id uuid) returns void language plpgsql
        security definer as $$ begin
          execute format('delete from %I where id = $1', 'notes') using p_user_id;
        end $$;
        create function public.js(p_user_id uuid) returns void language plv8
        security definer as $$ plv8.execute('delete from notes'); $$;
        create function public.atomic(p_user_id uuid) returns void language sql
        security definer begin atomic delete from public.notes where id = p_user_id; end;
        create table public.after_atomic (id int);
    """})
    functions = _functions(root)
    assert functions["public.dyn(uuid)"].writes is None
    assert functions["public.js(uuid)"].writes is None
    assert functions["public.atomic(uuid)"].writes is None
    # the splitter kept `begin atomic ... end` whole, so what follows still replays
    schema = detect_schema(root)
    assert "public.after_atomic" in {table.name for table in schema.tables}


def test_functions_in_any_schema_are_recorded(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create schema private;
        create function private.f() returns void language sql as $$ select 1 $$;"""})
    assert _only(root).name == "private.f"


# -- asking who the caller is ------------------------------------------------------------

def test_comparing_the_parameter_with_the_caller(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.a(p_user_id uuid) returns void language plpgsql
        security definer as $$ begin
          if p_user_id <> auth.uid() then raise exception 'no'; end if;
          delete from public.notes where owner = p_user_id;
        end $$;
        create function public.b(p_user_id uuid) returns void language plpgsql
        security definer as $$ begin
          if (select auth.uid()) is distinct from p_user_id then raise exception 'no'; end if;
        end $$;
        create function public.c(p_user_id uuid) returns void language plpgsql
        security definer as $$ declare v_uid uuid := auth.uid(); begin
          if p_user_id != v_uid then raise exception 'no'; end if;
        end $$;
        create function public.d(p_user_id uuid) returns void language plpgsql
        security definer as $$ declare v uuid; begin
          select auth.uid() into v;
          if v = p_user_id then delete from public.notes where owner = p_user_id; end if;
        end $$;
        create function public.e("p_user_id" "uuid") returns void language plpgsql
        security definer as $$ begin
          if "p_user_id" <> "auth"."uid"() then raise exception 'no'; end if;
        end $$;
    """})
    for function in _functions(root).values():
        assert function.compares_caller == ["p_user_id"], function.name
        assert function.role_check is False, function.name


def test_being_signed_in_or_logging_the_caller_clears_nothing(tmp_path):
    """The commonest way these functions go wrong (RFC-0004)."""
    root = _migrations(tmp_path, {"001.sql": """
        create function public.a(p_user_id uuid, n int) returns void language plpgsql
        security definer as $$ begin
          if auth.uid() is null then raise exception 'sign in'; end if;
          insert into public.audit_log (actor, target) values (auth.uid(), p_user_id);
          update public.balances set amount = amount + n where user_id = p_user_id;
        end $$;"""})
    function = _only(root)
    assert function.compares_caller == []
    assert function.role_check is False


def test_a_role_check_is_recorded(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.a(p_user_id uuid) returns void language plpgsql
        security definer as $$ begin
          if not has_role(auth.uid(), 'admin') then raise exception 'no'; end if;
        end $$;
        create function public.b(p_user_id uuid) returns void language plpgsql
        security definer as $$ begin
          if (auth.jwt() ->> 'role') <> 'admin' then raise exception 'no'; end if;
        end $$;
        create function public.c(p_user_id uuid) returns void language plpgsql
        security definer as $$ declare me uuid := auth.uid(); begin
          if not public.is_admin(me) then raise exception 'no'; end if;
        end $$;
    """})
    for function in _functions(root).values():
        assert function.role_check is True, function.name


# -- grants ------------------------------------------------------------------------------

def _grants(sql: str, tmp_path) -> dict:
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": sql})
    function = _only(root)
    return {"grants": function.grants, "executable_by": function.executable_by}


def test_revoking_from_anon_alone_leaves_anon_through_public(tmp_path):
    assert _grants("revoke execute on function public.add_points(uuid, integer) from anon;",
                   tmp_path) == {"grants": ["authenticated", "public"],
                                 "executable_by": ["anon", "authenticated"]}


def test_revoking_from_public_and_anon_leaves_signed_in_users(tmp_path):
    assert _grants("revoke execute on function public.add_points(uuid, int) "
                   "from public, anon;", tmp_path) == {"grants": ["authenticated"],
                                                      "executable_by": ["authenticated"]}


def test_revoking_from_everyone_leaves_nobody(tmp_path):
    assert _grants("revoke all on function add_points from public, anon, authenticated;",
                   tmp_path) == {"grants": [], "executable_by": []}


def test_grant_and_other_roles(tmp_path):
    assert _grants("""
        revoke execute on function public.add_points(uuid, integer) from public, anon,
          authenticated;
        grant execute on function public.add_points(uuid, integer) to service_role;
        grant execute on function public.add_points(uuid, integer) to authenticated;""",
                   tmp_path) == {"grants": ["authenticated"],
                                 "executable_by": ["authenticated"]}


def test_revoke_on_all_functions_in_a_schema_reaches_only_existing_ones(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        revoke execute on all functions in schema public from anon, authenticated, public;
        create function public.later() returns void language sql as $$ select 1 $$;"""})
    functions = _functions(root)
    assert functions["public.add_points(uuid, integer)"].grants == []
    assert functions["public.later()"].grants == ["anon", "authenticated", "public"]


def test_default_privileges_apply_to_functions_created_after(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.before() returns void language sql as $$ select 1 $$;
        alter default privileges revoke execute on functions from public;
        alter default privileges in schema public revoke execute on functions from anon;
        create function public.after() returns void language sql as $$ select 1 $$;"""})
    functions = _functions(root)
    assert functions["public.before()"].grants == ["anon", "authenticated", "public"]
    assert functions["public.after()"].grants == ["authenticated"]
    assert functions["public.after()"].executable_by == ["authenticated"]


def test_a_per_schema_default_cannot_revoke_the_global_public_grant(tmp_path):
    """PostgreSQL: per-schema default privileges only add to the global defaults."""
    root = _migrations(tmp_path, {"001.sql": """
        alter default privileges in schema public revoke execute on functions from public;
        create function public.f() returns void language sql as $$ select 1 $$;"""})
    assert _only(root).grants == ["anon", "authenticated", "public"]


def test_default_privileges_for_another_role_do_not_apply(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        alter default privileges for role supabase_admin in schema public
          revoke execute on functions from anon, authenticated;
        alter default privileges for role "postgres" revoke execute on functions from public;
        create function public.f() returns void language sql as $$ select 1 $$;"""})
    assert _only(root).grants == ["anon", "authenticated"]


def test_create_or_replace_keeps_grants_and_drop_then_create_resets_them(tmp_path):
    lock = "revoke execute on function public.add_points(uuid, integer) from public, anon;"
    replaced = _migrations(tmp_path / "a", {"000.sql": ADD_POINTS, "001.sql": lock,
                                            "002.sql": ADD_POINTS})
    assert _only(replaced).grants == ["authenticated"]
    recreated = _migrations(tmp_path / "b", {
        "000.sql": ADD_POINTS, "001.sql": lock,
        "002.sql": "drop function public.add_points(uuid, integer);" + ADD_POINTS})
    assert _only(recreated).grants == ["anon", "authenticated", "public"]


# -- drop and alter ----------------------------------------------------------------------

def test_drop_function(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS,
                                  "001.sql": "drop function if exists add_points;"})
    assert _functions(root) == {}


def test_alter_function(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        alter function public.add_points(uuid, integer) security invoker;
        alter function public.add_points(uuid, integer) set search_path = public;
        alter function public.add_points(uuid, integer) rename to give_points;
        alter function public.give_points(uuid, integer) owner to postgres;"""})
    function = _only(root)
    assert function.signature == "public.give_points(uuid, integer)"
    assert function.definer is False
    assert function.owner is None


def test_alter_function_owner_and_schema(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        alter function public.add_points(uuid, integer) owner to app_writer;
        alter function public.add_points(uuid, integer) set schema private;"""})
    function = _only(root)
    assert (function.name, function.owner) == ("private.add_points", "app_writer")


# -- what is not understood costs functions only ------------------------------------------

OVERLOADS = """
create function public.f(a uuid) returns void language sql as $$ select 1 $$;
create function public.f(a text) returns void language sql as $$ select 1 $$;
create table public.t (id int);
alter table public.t enable row level security;
"""


def test_an_ambiguous_reference_makes_functions_unknown_and_nothing_else(tmp_path):
    root = _migrations(tmp_path, {"001.sql": OVERLOADS + "revoke execute on function f "
                                                       "from anon;"})
    schema = detect_schema(root)
    assert schema.functions is None
    assert schema.functions_unreplayable == [
        "supabase/migrations/001.sql:6: revoke names public.f, which has 2 overloads"]
    assert schema.unreplayable == []
    assert [table.name for table in schema.tables] == ["public.t"]


def test_a_signature_the_replay_cannot_match_makes_functions_unknown(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql":
                                  "revoke execute on function add_points(text) from anon;"})
    assert detect_schema(root).functions is None


def test_a_grant_on_a_function_created_elsewhere_is_ignored(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql":
                                  "revoke execute on function public.other(uuid) from anon;"})
    assert list(_functions(root)) == ["public.add_points(uuid, integer)"]


def test_an_alter_function_form_not_understood_makes_functions_unknown(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql":
                                  "alter function public.add_points(uuid, integer) "
                                  "frobnicate;"})
    assert detect_schema(root).functions is None


def test_references_with_type_modifiers_are_understood(tmp_path):
    root = _migrations(tmp_path, {"001.sql": """
        create function public.f(p_name varchar(20)) returns void language sql
        security definer as $$ update public.t set n = $1 $$;
        alter function public.f(varchar(20)) security invoker;
        revoke execute on function public.f(character varying(20)) from public, anon;"""})
    function = _only(root)
    assert (function.definer, function.grants) == (False, ["authenticated"])


def test_harmless_alter_function_actions_are_understood(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        alter function public.add_points(uuid, integer) stable;
        alter function public.add_points(uuid, integer) reset all;
        alter function public.add_points(uuid, integer) cost 10;"""})
    assert _only(root).definer is True


def test_unordered_migrations_mean_no_functions(tmp_path):
    root = _migrations(tmp_path, {"001_a.sql": ADD_POINTS, "zz.sql": "select 1;"})
    assert detect_schema(root).functions is None


def test_an_unreplayable_table_statement_does_not_cost_functions(tmp_path):
    root = _migrations(tmp_path, {"001.sql": ADD_POINTS + """
        create policy if not exists "p" on public.user_points for select using (true);"""})
    schema = detect_schema(root)
    assert schema.unreplayable and schema.tables is None
    assert [f.signature for f in schema.functions] == ["public.add_points(uuid, integer)"]


# -- DO blocks ---------------------------------------------------------------------------

def test_a_guarded_function_statement_in_a_do_block_is_read(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'add_points') THEN
            REVOKE EXECUTE ON FUNCTION public.add_points(uuid, integer) FROM anon, public;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'wipe') THEN
            CREATE FUNCTION public.wipe(p_user_id uuid) RETURNS void LANGUAGE sql
            SECURITY DEFINER AS $f$ delete from public.notes where owner = p_user_id $f$;
          END IF;
        END $$;"""})
    functions = _functions(root)
    assert functions["public.add_points(uuid, integer)"].grants == ["authenticated"]
    [write] = functions["public.wipe(uuid)"].writes
    assert (write.verb, write.table, write.uses) == ("delete", "public.notes", ["p_user_id"])


def test_a_function_statement_in_dynamic_sql_makes_functions_unknown(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        DO $$ BEGIN
          EXECUTE 'REVOKE EXECUTE ON FUNCTION public.add_points(uuid, integer) FROM anon';
        END $$;"""})
    schema = detect_schema(root)
    assert schema.functions is None
    assert schema.unreplayable == []


def test_a_function_statement_under_another_condition_makes_functions_unknown(tmp_path):
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS, "001.sql": """
        DO $$ BEGIN
          IF current_setting('app.env') = 'prod' THEN
            REVOKE EXECUTE ON FUNCTION public.add_points(uuid, integer) FROM anon;
          END IF;
        END $$;"""})
    assert detect_schema(root).functions is None


def test_function_statements_beside_a_policy_in_a_do_block(tmp_path):
    """A block the replay follows for its policy also carries a function statement."""
    root = _migrations(tmp_path, {"000.sql": ADD_POINTS + """
        create table public.user_points (user_id uuid, points int);""", "001.sql": """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'read') THEN
            CREATE POLICY "read" ON public.user_points FOR SELECT USING (true);
          END IF;
          REVOKE EXECUTE ON FUNCTION public.add_points(uuid, integer) FROM anon, public;
        END $$;"""})
    schema = detect_schema(root)
    assert schema.unreplayable == []
    assert [p.name for p in schema.tables[0].policies] == ["read"]
    assert schema.functions[0].grants == ["authenticated"]


def test_no_function_statements_means_an_empty_list(tmp_path):
    root = _migrations(tmp_path, {"001.sql": "create table public.t (id int);"})
    assert detect_schema(root).functions == []
