"""SQL text for the fix renderer: identifiers, comments, policies. RFC-0003.

Identifiers come from the repository and are data. Every identifier and policy name is
quoted by PostgreSQL's rules and never interpolated raw, so a table named to smuggle a
statement into the migration renders as an odd name, not as a second statement. Comment
text is escaped so that no identifier can end a `--` comment line early: a table name
may legally contain a newline.
"""

from __future__ import annotations

import re

#: PostgreSQL's reserved key words, including those that may be a function or type
#: name, per the "SQL Key Words" appendix. Quoting one that did not need it is
#: harmless; failing to quote one that did is a syntax error in the user's migration.
RESERVED = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both case cast
check collate collation column concurrently constraint create cross current_catalog
current_date current_role current_schema current_time current_timestamp current_user
default deferrable desc distinct do else end except false fetch for foreign freeze from
full grant group having ilike in initially inner intersect into is isnull join lateral
leading left like limit localtime localtimestamp natural not notnull null offset on only
or order outer overlaps placing primary references returning right select session_user
similar some symmetric system_user table tablesample then to trailing true union unique
user using variadic verbose when where window with
""".split())

_PLAIN = re.compile(r"^[a-z_][a-z0-9_$]*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]")

#: `(select auth.uid())`: evaluated once per statement, not once per row. Supabase's
#: recommended form, and what its `auth_rls_initplan` lint asks for.
UID = "(select auth.uid())"


def quote_ident(name: str) -> str:
    """`name` as PostgreSQL would need it written: bare when it is a plain lower-case
    identifier that is not reserved, otherwise double-quoted with quotes doubled."""
    if _PLAIN.match(name) and name not in RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def quote_table(name: str) -> str:
    """ "public.tasks" -> public.tasks. The schema is everything before the first dot;
    the table name may itself contain dots."""
    schema, table = name.split(".", 1)
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def quote_literal_name(name: str) -> str:
    """A policy name is an identifier too, but always quoted: it is prose."""
    return '"' + name.replace('"', '""') + '"'


def sql_comment(text: str) -> str:
    """One `--` comment line. Control characters are escaped visibly, so nothing in
    `text` can start a new line of SQL."""
    escaped = _CONTROL.sub(lambda match: {"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(
        match.group(0), f"\\x{ord(match.group(0)):02x}"), text)
    return f"-- {escaped}".rstrip()


def owner_expression(column: str) -> str:
    return f"{UID} = {quote_ident(column)}"


def enable_rls(table: str) -> str:
    return f"alter table {quote_table(table)} enable row level security;"


def drop_policy(name: str, table: str) -> str:
    return f"drop policy if exists {quote_literal_name(name)} on {quote_table(table)};"


def create_policy(name: str, table: str, command: str, roles, using: str | None,
                  with_check: str | None) -> str:
    lines = [f"create policy {quote_literal_name(name)} on {quote_table(table)}",
             f"  as permissive for {command} to {', '.join(quote_ident(r) for r in roles)}"]
    if using is not None:
        lines.append(f"  using ({using})")
    if with_check is not None:
        lines.append(f"  with check ({with_check})")
    return "\n".join(lines) + ";"
