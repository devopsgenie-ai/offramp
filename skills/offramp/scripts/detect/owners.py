"""Owner detection: which column says whose row it is. RFC-0003.

A fix that scopes writes to the wrong column is confidently wrong in the worst
direction. Either every write from the app fails, or one user edits another's rows, and
either way the audit reports clean. So `write_scope` is detected as `owner` only when
exactly one column is supported by evidence, and the evidence agrees:

  policy basis        an existing policy on the table compares the column, and nothing
                      else, to `auth.uid()`. The app's author already declared it the
                      owner for some operation.
  foreign-key basis   the column references `auth.users.id`; or its table's primary key
                      does (`profiles.id` owns itself); or, one hop, it references a
                      table whose primary key references `auth.users.id`.

Two candidates on either basis, bases that disagree, a policy whose column was renamed
after it was written, or unknown columns: nothing is detected. A column's *name* is
never evidence. The corpus has `assigned_to`, `approved_by` and `invited_by` referencing
`auth.users` exactly as `user_id` does (docs/research/rls-fix-patterns.md). A name only
becomes the gap's `proposed` answer, for a human to accept.

A gap is asked only for a table whose writes are open today (`rls.leaves_writes_open`,
the predicate the checks use). Asking about every table would add a question per table
to every scan, most of them with no consequence.
"""

from __future__ import annotations

from dataclasses import replace

from rls import leaves_writes_open, owner_column
from spec import Gap, Schema, Table, WriteScope, gap_id

#: Names that suggest an owner. Proposal material only; see the module docstring.
OWNER_NAMES = ("user_id", "owner_id", "created_by", "author_id", "creator_id",
               "profile_id")


def detect_write_scopes(schema: Schema | None, pointer: str) -> tuple[Schema | None, list[Gap]]:
    """`schema` with `write_scope` set where proven, and a blocking gap per table that
    needs an owner and has none. `pointer` is the schema's JSON pointer in the AppSpec,
    e.g. `/datastores/0/schema`."""
    if schema is None or schema.tables is None:
        return schema, []
    owned = {table.name: key for table in schema.tables
             if (key := _key_referencing_users(table)) is not None}
    tables: list[Table] = []
    gaps: list[Gap] = []
    for index, table in enumerate(schema.tables):
        if not table.name.startswith("public."):
            tables.append(table)
            continue
        scope, candidates, stale = _evidence(table, owned)
        tables.append(replace(table, write_scope=scope))
        if scope is None and leaves_writes_open(table):
            gaps.append(_gap(table, candidates, stale, f"{pointer}/tables/{index}/write_scope"))
    return replace(schema, tables=tables), gaps


def _key_referencing_users(table: Table) -> str | None:
    """The primary-key column, when the table's single-column key references
    auth.users.id: the `profiles` shape, the far end of the one-hop rule."""
    key = [column for column in table.columns or [] if column.primary_key]
    if len(key) == 1 and key[0].references == "auth.users.id":
        return key[0].name
    return None


def _evidence(table: Table, owned: dict[str, str]):
    """(scope or None, {column: [basis]}, [stale policy descriptions])."""
    if table.columns is None:
        return None, {}, []
    names = {column.name for column in table.columns}
    by_policy: dict[str, list[str]] = {}
    stale: list[str] = []
    for policy in table.policies:
        for expression in (policy.using, policy.with_check):
            column = owner_column(expression, table.name)
            if column is None:
                continue
            if column in policy.renamed_since:
                stale.append(f'policy "{policy.name}" names `{column}` ({policy.evidence})')
            elif column in names:
                basis = f'policy "{policy.name}" compares it to auth.uid() ({policy.evidence})'
                if basis not in by_policy.setdefault(column, []):
                    by_policy[column].append(basis)
    by_key: dict[str, list[str]] = {}
    for column in table.columns:
        if column.references == "auth.users.id":
            by_key[column.name] = ["it references auth.users.id"]
            continue
        target = (column.references or "").rsplit(".", 1)
        if (len(target) == 2 and target[0] != table.name
                and owned.get(target[0]) == target[1]):
            by_key[column.name] = [f"it references {column.references}, whose primary key "
                                   f"references auth.users.id"]
    candidates = {name: by_policy.get(name, []) + by_key.get(name, [])
                  for name in sorted(set(by_policy) | set(by_key))}
    if stale or len(by_policy) > 1 or len(by_key) > 1:
        return None, candidates, stale
    if by_policy and by_key and set(by_policy) != set(by_key):
        return None, candidates, stale
    if len(candidates) != 1:
        return None, candidates, stale
    (column, basis), = candidates.items()
    return WriteScope(kind="owner", column=column, basis=basis), candidates, stale


def _gap(table: Table, candidates: dict[str, list[str]], stale: list[str],
         pointer: str) -> Gap:
    named = [column.name for column in table.columns or [] if column.name in OWNER_NAMES]
    proposed = {"kind": "owner", "column": named[0]} if len(named) == 1 else None
    if stale:
        why = ("The policy that would prove the owner was written before a column it names "
               "was renamed, so the migrations no longer say which column it means: "
               + "; ".join(stale) + ".")
    elif len(candidates) > 1:
        why = ("More than one column could be the owner, and nothing says which: "
               + "; ".join(f"`{name}` ({', '.join(basis)})"
                           for name, basis in candidates.items()) + ".")
    elif table.columns is None:
        why = ("The migrations change this table's columns in a way the replay does not "
               "follow, so no column can be proven to identify a row's owner.")
    else:
        why = "No column is proven to identify a row's owner."
    if proposed:
        why += (f" `{proposed['column']}` is proposed from its name alone, which is not "
                f"evidence.")
    evidence = sorted({table.evidence, *(p.evidence for p in table.policies)} - {None})
    return Gap(
        id=gap_id("datastore", "supabase", "table", table.name, "write_scope"),
        kind="value",
        pointers=[pointer],
        question=(
            f"Who may change rows in `{table.name}`? {why} No policy can be written "
            f"without guessing. Answer with the owner column, or `server_only` if only "
            f"your server should write."
        ),
        proposed=proposed,
        confidence="medium" if proposed else "low",
        severity="blocking",
        evidence=evidence,
    )
