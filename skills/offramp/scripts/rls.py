"""Row-level-security predicates over the replayed schema, in one place. RFC-0003.

The audit's checks, the owner detector and the fix renderer all ask the same questions
of a table: is this policy an open write, does this table leave writes open, does this
expression name the row's owner. RFC-0003 requires the answers to come from one
function each ("the predicate is one function, shared with the check"). If the detector
asked about a slightly different set of tables than the check reports, a finding could
have no gap behind it, and the fix would have nothing to say about it.

Pure functions of `spec` dataclasses. No I/O.
"""

from __future__ import annotations

import re

from spec import Policy, Schema, Table

WRITE_COMMANDS = ("all", "insert", "update", "delete")
#: Commands whose `true` policy lets someone change or delete existing rows: a high
#: finding, and what the fix replaces. An INSERT-only `true` is a medium decision.
CHANGE_COMMANDS = ("all", "update", "delete")
ANYONE = ("anon", "public")
SIGNED_IN = ("authenticated",)

_UID = r"(?:auth\.uid\(\s*\)|\(\s*select\s+auth\.uid\(\s*\)\s*\))"
_NAME = r'"(?:[^"]|"")+"|[a-z_][a-z0-9_$]*'
_COLUMN = rf"(?:(?P<qualifier>{_NAME})\s*\.\s*)?(?P<column>{_NAME})"
_OWNER = (re.compile(rf"^{_UID}\s*=\s*{_COLUMN}$"), re.compile(rf"^{_COLUMN}\s*=\s*{_UID}$"))


def public_tables(schema: Schema) -> list[Table]:
    """Tables in `public`, the schema Supabase exposes through its API by default."""
    return [table for table in schema.tables or [] if table.name.startswith("public.")]


def anyone(policy: Policy) -> bool:
    return any(role in ANYONE for role in policy.roles)


def is_permissive_write(policy: Policy) -> bool:
    """`supabase.rls.permissive_write`: a write command, open roles, a `true` condition."""
    return (policy.permissive
            and policy.command in WRITE_COMMANDS
            and any(role in ANYONE + SIGNED_IN for role in policy.roles)
            and "true" in (policy.using, policy.with_check))


def is_open_change(policy: Policy) -> bool:
    """A permissive write that can change or delete existing rows (high, not medium)."""
    return is_permissive_write(policy) and policy.command in CHANGE_COMMANDS


def is_public_read(policy: Policy) -> bool:
    return (policy.permissive
            and policy.command == "select"
            and anyone(policy)
            and policy.using == "true")


def rls_never_enabled(table: Table) -> bool:
    """`supabase.rls.disabled`: created in these migrations, RLS never enabled."""
    return table.rls is False and table.evidence is not None


def leaves_writes_open(table: Table) -> bool:
    """The tables the fix needs an owner for: RLS off, or a permissive UPDATE, DELETE
    or ALL `true`. Exactly the tables with a critical or high RLS finding."""
    if rls_never_enabled(table):
        return True
    return table.rls is not False and any(is_open_change(p) for p in table.policies)


def owner_column(expression: str | None, table: str) -> str | None:
    """The column an expression compares to `auth.uid()`, when that is all it does.

    Exactly `auth.uid() = col`, `col = auth.uid()`, or either with `(select
    auth.uid())`, optionally qualified by the table's own name. A compound expression
    (`... or is_admin()`), a cast, or anything else is not evidence of an owner.
    Expressions arrive normalised: lower-cased, outer parentheses and runs of spaces
    removed.
    """
    if expression is None:
        return None
    text = re.sub(r"\s+", " ", expression).strip()
    found = _OWNER[0].match(text) or _OWNER[1].match(text)
    if found is None:
        return None
    qualifier = found.group("qualifier")
    if qualifier is not None and _unquote(qualifier) != table.split(".", 1)[1]:
        return None
    return _unquote(found.group("column"))


def _unquote(name: str) -> str:
    return name[1:-1].replace('""', '"') if name.startswith('"') else name
