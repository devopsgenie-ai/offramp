#!/usr/bin/env python3
"""Apply each fixture's migrations and then its generated fix to a disposable PostgreSQL,
and check what users can do before and after. RFC-0003, Testing.

    PGHOST=... PGPORT=... PGUSER=... python scripts/check_fix_behaviour.py [fixture ...]

This is the only test that catches the "locked shut" failure -- an owner who can no
longer change their own row -- and the only one that proves PostgreSQL, not just the
replay, accepts the generated file. The round trip in `fix` proves the file closes what
it claims by the audit's own reading; this proves it by running it.

It is validation of generated output in a throwaway database, the same class as the
`docker build` checks RFC-0001 permits. It runs in CI only, never inside `fix`, and
`fix` has no mode that applies anything. It needs `psql` and a server it may create and
drop databases on; it touches nothing else.

Each fixture with an `expected_fix/` migration has a `behaviour.yaml`:

    users: {alice: <uuid>, bob: <uuid>}   # inserted into auth.users
    seed: |                               # run as the superuser after the migrations
      insert into public.tasks (id, owner_id) values ('{task}', '{alice}');
    checks:
      - name: the owner can update their task
        as: alice                         # a user, or anon
        sql: update public.tasks set title = 'x' where id = '{task}'
        before: 1                         # rows, or "error"
        after: 1

A DML check counts the rows it affected; a SELECT check must return one count. Every
check runs in its own transaction and is rolled back, so checks do not interact.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STUB = ROOT / "tests" / "db" / "supabase_stub.sql"
_ROWS = re.compile(r"NOTICE:\s+offramp_rows=(\d+)")


def psql(database: str, *args: str, sql: str | None = None) -> subprocess.CompletedProcess:
    command = ["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", database,
               *args]
    return subprocess.run(command, input=sql, capture_output=True, text=True,
                          env={**os.environ, "PGOPTIONS": "-c client_min_messages=notice"})


def _must(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{what} failed:\n{result.stderr.strip()}")


def fresh_database(name: str) -> None:
    _must(psql("postgres", "-c", f'drop database if exists "{name}"'), "drop database")
    _must(psql("postgres", "-c", f'create database "{name}"'), "create database")
    _must(psql(name, "-f", str(STUB)), "the Supabase stub")


def apply(database: str, path: Path) -> None:
    _must(psql(database, "-1", "-f", str(path)), f"applying {path.name}")


def _format(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def run_check(database: str, check: dict, users: dict[str, str], values: dict) -> str:
    """"error", or the number of rows affected or counted, as a string."""
    sql = _format(check["sql"], values).strip().rstrip(";")
    who = check["as"]
    session = ["begin;"]
    if who == "anon":
        session.append("set local role anon;")
    else:
        session.append("set local role authenticated;")
        session.append(f"set local request.jwt.claim.sub = '{users[who]}';")
    if sql.lower().startswith("select"):
        session.append(sql + ";")
    else:
        # GET DIAGNOSTICS, not RETURNING: RETURNING is itself subject to SELECT policies
        # and would change what is being measured.
        session.append("do $check$ declare n int; begin "
                       f"{sql}; get diagnostics n = row_count; "
                       "raise notice 'offramp_rows=%', n; end $check$;")
    session.append("rollback;")
    result = psql(database, sql="\n".join(session))
    if result.returncode != 0:
        return "error"
    if found := _ROWS.search(result.stderr):
        return found.group(1)
    return result.stdout.strip()


def check_fixture(fixture: Path) -> list[str]:
    spec = yaml.safe_load((fixture / "behaviour.yaml").read_text(encoding="utf-8"))
    migrations = sorted((fixture / "supabase" / "migrations").glob("*.sql"))
    generated = sorted((fixture / "expected_fix" / "supabase" / "migrations").glob("*.sql"))
    if len(generated) != 1:
        return [f"{fixture.name}: expected exactly one generated migration"]
    users = spec["users"]
    values = {**users, **(spec.get("values") or {})}
    database = "offramp_fix_" + re.sub(r"[^a-z0-9]+", "_", fixture.name)

    fresh_database(database)
    for path in migrations:
        apply(database, path)
    rows = ", ".join(f"('{uuid}', '{name}@example.invalid')" for name, uuid in users.items())
    _must(psql(database, "-c", f"insert into auth.users (id, email) values {rows}"), "users")
    _must(psql(database, sql=_format(spec.get("seed") or "", values)), "the seed")

    before = {c["name"]: run_check(database, c, users, values) for c in spec["checks"]}
    apply(database, generated[0])
    after = {c["name"]: run_check(database, c, users, values) for c in spec["checks"]}

    problems = []
    for check in spec["checks"]:
        name = check["name"]
        for phase, actual in (("before", before[name]), ("after", after[name])):
            if str(check[phase]) != actual:
                problems.append(f"{fixture.name}: {name}: {phase} the fix, expected "
                                f"{check[phase]}, got {actual}")
    return problems


def main(argv: list[str]) -> int:
    fixtures = [Path(arg) for arg in argv] or sorted(
        path.parent for path in (ROOT / "fixtures").glob("*/behaviour.yaml"))
    failures = 0
    for fixture in fixtures:
        try:
            problems = check_fixture(fixture)
        except RuntimeError as error:
            problems = [f"{fixture.name}: {error}"]
        checks = len(yaml.safe_load((fixture / "behaviour.yaml").read_text())["checks"])
        print(f"{fixture.name}: {'ok' if not problems else f'{len(problems)} problem(s)'}"
              f"  ({checks} checks, before and after)")
        for problem in problems:
            print(f"  - {problem}")
        failures += len(problems)
    print(f"checked {len(fixtures)} fixture(s), {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
