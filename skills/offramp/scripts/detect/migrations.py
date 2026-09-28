"""Replay `supabase/migrations/*.sql` into the final state of tables, RLS and policies.

RFC-0003 moved this module from `checks/` to `detect/`: its result is `Datastore.schema`
in the AppSpec, which the RLS checks and the fix renderer both read. It also tracks
columns now -- names, nullability, defaults, primary keys and foreign keys -- because the
fix needs to know which column identifies a row's owner. Columns are tracked apart from
RLS. A column change this module does not understand sets that table's columns to
unknown and nothing else; it never makes the replay unparseable, so the audit's answers
are exactly what they were before columns were tracked.

RFC-0002: migrations are replayed in filename order, and the replay understands six
statement forms -- create table, enable/disable RLS, rename, drop table, create policy,
drop policy. A statement that touches tables, policies or RLS in any other form makes
the result unparseable, and the RLS checks then report could_not_assess for the whole
repository. A partial answer from a replay that skipped a statement it did not
understand is the "confident and wrong" failure RFC-0001 exists to prevent.

Stdlib only: a hand-written statement matcher over a deliberately narrow grammar.
Function bodies are not replayed -- they run later, not at migration time -- but a `DO`
block runs at migration time, so one that touches tables or RLS is unparseable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from spec import Column
from spec import Policy as SchemaPolicy
from spec import Schema
from spec import Table as SchemaTable
from walk import rel

MIGRATIONS_DIR = Path("supabase") / "migrations"

#: A migration's run order comes from the version number its name starts with: the
#: Supabase CLI writes `<version>_<name>.sql`, Lovable writes `<version>-<uuid>.sql`. A
#: file with no leading version has no knowable place in that order.
_TIMESTAMPED = re.compile(r"^\d+(?:[_-][^/]*)?\.sql$")
_VERSION = re.compile(r"^(\d+)")

_IDENT = r'(?:"[^"]+"|[a-z_][a-z0-9_$]*)'
_QNAME = rf"({_IDENT}(?:\s*\.\s*{_IDENT})?)"

_CREATE_TABLE = re.compile(
    rf"^create\s+(?:unlogged\s+)?table\s+(if\s+not\s+exists\s+)?{_QNAME}", re.S)
_TEMP_TABLE = re.compile(r"^create\s+(?:(?:global|local)\s+)?(?:temp|temporary)\s+table\b")
_ALTER_RLS = re.compile(
    rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_QNAME}\s+"
    r"(enable|disable|force|no\s+force)\s+row\s+level\s+security$", re.S)
_ALTER_RENAME = re.compile(
    rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_QNAME}\s+rename\s+to\s+({_IDENT})$",
    re.S)
_ALTER_TABLE = re.compile(r"^alter\s+table\b")
_DROP_TABLE = re.compile(
    rf"^drop\s+table\s+(?:if\s+exists\s+)?({_QNAME[1:-1]}(?:\s*,\s*{_QNAME[1:-1]})*)"
    r"(?:\s+(?:cascade|restrict))?$", re.S)
_CREATE_POLICY = re.compile(rf"^create\s+policy\s+({_IDENT})\s+on\s+{_QNAME}(.*)$", re.S)
_DROP_POLICY = re.compile(
    rf"^drop\s+policy\s+(?:if\s+exists\s+)?({_IDENT})\s+on\s+{_QNAME}(?:\s+(?:cascade|restrict))?$",
    re.S)
_TOUCHES = re.compile(
    r"\b(?:create|alter|drop)\s+(?:table|policy)\b|\brow\s+level\s+security\b", re.S)
_DO_BLOCK = re.compile(r"^do\b")
#: What makes a DO block matter to the RLS checks. Narrower than _TOUCHES: a DO block that
#: only adds a constraint (`alter table ... add constraint`) changes no access control.
_DO_TOUCHES = re.compile(
    r"\bcreate\s+(?:unlogged\s+)?table\b|\bdrop\s+table\b|\b(?:create|alter|drop)\s+policy\b"
    r"|\brow\s+level\s+security\b|\brename\s+to\b", re.S)
_DYNAMIC = re.compile(r"\b(?:execute|loop|elsif|else|perform)\b")
_GUARD = re.compile(r"^(?:begin\s+)?if\s+(?:not\s+)?exists\s*(?=\()", re.S)
_NOISE = re.compile(
    r"^(?:end(?:\s+if)?|null|exception\s+when\s+[a-z_\s]+\s+then\s+null|declare\b.*)$", re.S)
_DOLLAR = re.compile(r"\$[A-Za-z0-9_]*\$")


@dataclass
class Policy:
    name: str
    command: str               # all | select | insert | update | delete
    roles: tuple[str, ...]     # "public" when the policy has no TO clause
    using: str | None          # normalised expression, e.g. "true"
    with_check: str | None
    permissive: bool
    evidence: str              # path:line
    line: int
    renamed_since: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str                  # schema-qualified, e.g. "public.notes"
    rls: bool | None           # None: the table was not created in these migrations
    evidence: str | None       # where it was created
    policies: dict[str, Policy] = field(default_factory=dict)
    #: In declaration order. None: created elsewhere, or a change was not understood.
    columns: dict[str, Column] | None = None
    #: Constraint name -> the column it makes a foreign key, or None for any other
    #: kind. Dropping a constraint this does not know makes the columns unknown.
    constraints: dict[str, str | None] = field(default_factory=dict)


@dataclass
class Replay:
    files: list[str]
    tables: dict[str, Table]
    unparseable: list[str]     # "path:line: reason"
    untimestamped: list[str] = field(default_factory=list)   # order unknown
    latest: str | None = None  # highest leading version among the files


def split_statements(text: str) -> list[tuple[str, str, int]]:
    """(original, masked, 1-based start line) per statement.

    `original` has comments removed. `masked` additionally replaces string literals with
    '' and dollar-quoted bodies with $$ $$, so classification never looks inside them.
    Double-quoted identifiers are kept as they are in both.
    """
    statements: list[tuple[str, str, int]] = []
    original: list[str] = []
    masked: list[str] = []
    line = 1
    start: int | None = None
    i = 0
    n = len(text)

    def emit() -> None:
        nonlocal original, masked, start
        body = "".join(original).strip()
        if body:
            statements.append((body, "".join(masked).strip(), start or line))
        original, masked, start = [], [], None

    while i < n:
        char = text[i]
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            chunk = text[i:] if end == -1 else text[i:end + 2]
            line += chunk.count("\n")
            original.append(" ")
            masked.append(" ")
            i += len(chunk)
            continue
        if char == ";":
            emit()
            i += 1
            continue
        if not char.isspace() and start is None:
            start = line
        if char == "'":
            end = i + 1
            while end < n:
                if text[end] == "'" and text.startswith("''", end):
                    end += 2
                    continue
                if text[end] == "'":
                    break
                end += 1
            chunk = text[i:end + 1]
            original.append(chunk)
            masked.append("''")
            line += chunk.count("\n")
            i = end + 1
            continue
        if char == '"':
            end = text.find('"', i + 1)
            chunk = text[i:] if end == -1 else text[i:end + 1]
            original.append(chunk)
            masked.append(chunk)
            line += chunk.count("\n")
            i += len(chunk)
            continue
        if char == "$":
            tag = _DOLLAR.match(text, i)
            if tag:
                close = text.find(tag.group(0), tag.end())
                chunk = text[i:] if close == -1 else text[i:close + len(tag.group(0))]
                original.append(chunk)
                masked.append(" $$ $$ ")
                line += chunk.count("\n")
                i += len(chunk)
                continue
        if char == "\n":
            line += 1
        original.append(char)
        masked.append(char)
        i += 1
    emit()
    return statements


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1]
    return identifier.lower()


def _qualify(qname: str) -> str:
    parts = [_unquote(part) for part in re.split(r"\s*\.\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)",
                                                  qname.strip())]
    return ".".join(parts) if len(parts) == 2 else f"public.{parts[0]}"


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """The contents of the parenthesised group starting at text[start] == '('."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None


def _normalise(expression: str) -> str:
    compact = re.sub(r"\s+", " ", expression).strip()
    while compact.startswith("(") and compact.endswith(")"):
        inner = _balanced(compact, 0)
        if inner is None or inner[1] != len(compact):
            break
        compact = inner[0].strip()
    return compact.lower()


def _parse_policy_tail(tail: str) -> dict | None:
    result = {"permissive": True, "command": "all", "roles": ("public",),
              "using": None, "with_check": None}
    rest = tail.strip()
    while rest:
        lowered = rest.lower()
        if match := re.match(r"as\s+(permissive|restrictive)\b", lowered):
            result["permissive"] = match.group(1) == "permissive"
        elif match := re.match(r"for\s+(all|select|insert|update|delete)\b", lowered):
            result["command"] = match.group(1)
        elif match := re.match(rf"to\s+({_IDENT}(?:\s*,\s*{_IDENT})*)", rest, re.I):
            result["roles"] = tuple(sorted(
                _unquote(role) for role in re.split(r"\s*,\s*", match.group(1))))
        elif match := re.match(r"(using|with\s+check)\s*(?=\()", lowered):
            group = _balanced(rest, match.end())
            if group is None:
                return None
            key = "using" if match.group(1).startswith("using") else "with_check"
            result[key] = _normalise(group[0])
            rest = rest[group[1]:].strip()
            continue
        else:
            return None
        rest = rest[match.end():].strip()
    return result


class _State:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}

    def table(self, name: str) -> Table:
        return self.tables.setdefault(name, Table(name=name, rls=None, evidence=None))

    def retarget(self, old: str, new: str | None) -> None:
        """Follow a renamed (or dropped) table or column in every foreign key, as
        PostgreSQL does. `old` is "schema.table" or "schema.table.column"."""
        for table in self.tables.values():
            for column in (table.columns or {}).values():
                target = column.references
                if target is None or not (target == old or target.startswith(old + ".")):
                    continue
                column.references = None if new is None else new + target[len(old):]


def _apply(state: _State, original: str, masked: str, where: str, line: int) -> str | None:
    """Apply one statement. Returns a reason when it cannot be understood.

    Matching runs on the masked text, case-insensitively: identifiers are identical
    there, and string literals and dollar bodies cannot be mistaken for syntax.
    """
    flat = re.sub(r"\s+", " ", masked).strip()
    lowered = flat.lower()

    def match(pattern: re.Pattern) -> "re.Match | None":
        return re.match(pattern.pattern, flat, re.I | re.S)

    if _DO_BLOCK.match(lowered):
        return _apply_do(state, original, where, line)
    if _TEMP_TABLE.match(lowered):
        return None
    if found := match(_CREATE_TABLE):
        name = _qualify(found.group(2))
        if not (found.group(1) and name in state.tables):
            table = Table(name=name, rls=False, evidence=where)
            _create_columns(state, table, flat[found.end():])
            state.tables[name] = table
        return None
    if found := match(_ALTER_RLS):
        table = state.table(_qualify(found.group(1)))
        action = found.group(2).lower()
        if action == "enable":
            table.rls = True
        elif action == "disable":
            table.rls = False
        return None
    if found := match(_ALTER_RENAME):
        old = _qualify(found.group(1))
        new = f"{old.split('.', 1)[0]}.{_unquote(found.group(2))}"
        if old in state.tables:
            moved = state.tables.pop(old)
            moved.name = new
            state.tables[new] = moved
            state.retarget(old, new)
        return None
    if _ALTER_TABLE.match(lowered):
        rest = lowered.removeprefix("alter table")
        if _TOUCHES.search(rest):
            return "alter table statement not understood"
        _alter_columns(state, flat)
        return None
    if found := match(_DROP_TABLE):
        for name in _split_names(found.group(1)):
            state.tables.pop(_qualify(name), None)
            state.retarget(_qualify(name), None)
        return None
    if re.match(r"^create\s+policy\s+if\s+not\s+exists\b", lowered):
        # Seen repeatedly in the corpus. PostgreSQL has no such syntax, so the migration
        # fails when applied and what the database holds afterwards is unknowable.
        return "CREATE POLICY IF NOT EXISTS is not valid PostgreSQL, so this migration fails"
    if found := match(_CREATE_POLICY):
        parsed = _parse_policy_tail(found.group(3))
        if parsed is None:
            return "create policy statement not understood"
        name = _unquote(found.group(1))
        table = state.table(_qualify(found.group(2)))
        table.policies[name] = Policy(name=name, evidence=where, line=line, **parsed)
        return None
    if found := match(_DROP_POLICY):
        table_name = _qualify(found.group(2))
        if table_name in state.tables:
            state.tables[table_name].policies.pop(_unquote(found.group(1)), None)
        return None
    if _TOUCHES.search(lowered):
        return "statement touching tables, policies or RLS not understood"
    return None


def _apply_do(state: _State, original: str, where: str, line: int) -> str | None:
    """Replay a DO block when its only control flow is an existence guard.

    The corpus showed Lovable wrapping most policy statements in
    `IF NOT EXISTS (SELECT ... FROM pg_policies ...) THEN CREATE POLICY ...; END IF;`.
    The end state of that block does not depend on the guard, so its inner statements
    are replayed as written. Anything dynamic -- EXECUTE, a loop, another condition --
    stays unparseable: its effect cannot be known without running it.
    """
    tag = _DOLLAR.search(original)
    if tag is None:
        return "do block not understood"
    close = original.find(tag.group(0), tag.end())
    body = original[tag.end():close if close != -1 else len(original)]
    lowered_body = body.lower()
    if not _DO_TOUCHES.search(lowered_body):
        return None
    if _DYNAMIC.search(re.sub(r"'(?:[^']|'')*'", "''", lowered_body)):
        return "do block with dynamic SQL touching tables or RLS"
    path = where.rsplit(":", 1)[0]
    first_line = line + original[:tag.end()].count("\n")
    for inner_original, inner_masked, inner_line in split_statements(body):
        statement = re.sub(r"\s+", " ", inner_masked).strip()
        inner_start = inner_line
        while True:
            lowered = statement.lower()
            if lowered.startswith("begin "):
                statement = statement[len("begin "):].strip()
                continue
            guard = _GUARD.match(lowered)
            if guard is None:
                break
            group = _balanced(statement, guard.end())
            if group is None:
                return "do block not understood"
            rest = statement[group[1]:].strip()
            if not rest.lower().startswith("then"):
                return "do block not understood"
            statement = rest[len("then"):].strip()
        lowered = statement.lower()
        if not statement or _NOISE.match(lowered):
            continue
        if lowered.startswith("if ") or lowered.startswith("do "):
            if _DO_TOUCHES.search(lowered):
                return "do block with a condition that is not an existence check"
            continue
        at = first_line + inner_start - 1 + _line_offset(inner_original, statement)
        reason = _apply(state, statement, statement, f"{path}:{at}", at)
        if reason:
            return reason
    return None


def _line_offset(original: str, statement: str) -> int:
    """Lines between the start of `original` and where `statement` begins in it, so a
    finding cites the CREATE POLICY line rather than the BEGIN or IF that wraps it."""
    words = statement.split()[:2]
    if not words:
        return 0
    pattern = r"\s+".join(re.escape(word) for word in words)
    matches = list(re.finditer(pattern, original, re.I))
    return original[:matches[-1].start()].count("\n") if matches else 0


def _split_names(names: str) -> list[str]:
    """Split a comma-separated name list, ignoring commas inside double quotes."""
    return [item for item in re.split(r'\s*,\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', names) if item]


def _sql_files(root: Path) -> list[Path]:
    directory = root / MIGRATIONS_DIR
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix == ".sql")


def migration_files(root: Path) -> list[Path]:
    return [path for path in _sql_files(root) if _TIMESTAMPED.match(path.name)]


def replay(root: Path) -> Replay:
    state = _State()
    unparseable: list[str] = []
    files = migration_files(root)
    for path in files:
        relative = rel(root, path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for original, masked, line in split_statements(text):
            reason = _apply(state, original, masked, f"{relative}:{line}", line)
            if reason:
                unparseable.append(f"{relative}:{line}: {reason}")
    untimestamped = [rel(root, path) for path in _sql_files(root)
                     if not _TIMESTAMPED.match(path.name)]
    return Replay(files=[rel(root, path) for path in files], tables=state.tables,
                  unparseable=unparseable, untimestamped=untimestamped,
                  latest=_latest(path.name for path in files))


def _latest(names) -> str | None:
    """The highest leading version, as written. Compared as a number: `9_a.sql` runs
    before `10_b.sql` in no tool, but a version is a number and sorts as one here."""
    versions = [match.group(1) for name in names if (match := _VERSION.match(name))]
    return max(versions, key=lambda version: (int(version), version)) if versions else None


def detect_schema(root: Path) -> Schema | None:
    """`Datastore.schema`. None when the repository has no migration files at all."""
    if not _sql_files(root):
        return None
    result = replay(root)
    tables = None
    if not (result.unparseable or result.untimestamped):
        tables = [_schema_table(table) for _, table in sorted(result.tables.items())]
    return Schema(migrations=result.files, unreplayable=result.unparseable,
                  unordered=result.untimestamped, latest=result.latest, tables=tables)


def _schema_table(table: Table) -> SchemaTable:
    return SchemaTable(
        name=table.name,
        rls=table.rls,
        evidence=table.evidence,
        columns=None if table.columns is None else [
            Column(**vars(column)) for column in table.columns.values()],
        policies=[
            SchemaPolicy(name=policy.name, command=policy.command, roles=list(policy.roles),
                         using=policy.using, with_check=policy.with_check,
                         permissive=policy.permissive, evidence=policy.evidence,
                         renamed_since=list(policy.renamed_since))
            for _, policy in sorted(table.policies.items())
        ],
    )


# -- Columns ---------------------------------------------------------------------------
#
# RFC-0003. Everything below changes `Table.columns` and nothing else. A form it does not
# understand sets the table's columns to None: the fix then has no owner evidence for that
# table and asks, which is the safe direction. It never returns an unparseable reason.

_CONSTRAINT_END = re.compile(
    r"\s+(?:not\s+null|null|primary\s+key|references|unique|check|constraint|generated|"
    r"collate|default)\b", re.I)
#: ALTER TABLE actions that cannot change a column's name, key, nullability or default.
_HARMLESS_ACTION = re.compile(
    r"^(?:owner\s+to|replica\s+identity|set\s+schema|set\s+tablespace|set\s+\(|reset\s+\(|"
    r"(?:enable|disable)\s+(?:always\s+|replica\s+)?(?:trigger|rule)|cluster\s+on|"
    r"set\s+without\s+(?:cluster|oids)|set\s+(?:logged|unlogged)|set\s+access\s+method|"
    r"validate\s+constraint|alter\s+constraint|(?:attach|detach)\s+partition)\b", re.I)
_HARMLESS_COLUMN_CHANGE = re.compile(
    r"^(?:(?:set\s+data\s+)?type\b|set\s+statistics\b|set\s+storage\b|set\s+compression\b|"
    r"set\s+\(|reset\s+\(|add\s+generated\b|set\s+generated\b|drop\s+identity\b|restart\b)",
    re.I)


def _shallow(text: str) -> str:
    """`text` with everything inside parentheses and double quotes blanked, same length,
    so a keyword search finds only top-level keywords (`check (x is not null)` is not
    NOT NULL) and its offsets still index `text`."""
    out: list[str] = []
    depth = 0
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
            out.append(char)
        elif quoted:
            out.append(" ")
        elif char == "(":
            depth += 1
            out.append(char if depth == 1 else " ")
        elif char == ")":
            depth -= 1
            out.append(char if depth == 0 else " ")
        else:
            out.append(char if depth == 0 else " ")
    return "".join(out)


def _split_top(text: str) -> list[str]:
    """Split on commas outside parentheses and double quotes."""
    shallow = _shallow(text)
    parts: list[str] = []
    start = 0
    for index, char in enumerate(shallow):
        if char == ",":
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _ident_list(text: str) -> list[str] | None:
    items = _split_top(text)
    if not all(re.fullmatch(_IDENT, item, re.I) for item in items):
        return None
    return [_unquote(item) for item in items]


def _primary_key(table: Table) -> list[str]:
    return [name for name, column in (table.columns or {}).items() if column.primary_key]


def _target(state: _State, qname: str, column: str | None) -> str:
    """"schema.table.column" for a REFERENCES clause. A bare reference means the
    referenced table's primary key: auth.users' is `id`, and a replayed table's is
    known. When it is not, the target has no column and is evidence of nothing."""
    table = _qualify(qname)
    if column is not None:
        return f"{table}.{_unquote(column)}"
    if table == "auth.users":
        return "auth.users.id"
    key = _primary_key(state.tables[table]) if table in state.tables else []
    return f"{table}.{key[0]}" if len(key) == 1 else table


def _references(state: _State, text: str) -> str | None:
    found = re.search(rf"\breferences\s+{_QNAME}(?:\s*\(\s*({_IDENT})\s*\))?", text, re.I)
    return None if found is None else _target(state, found.group(1), found.group(2))


def _column(state: _State, table: Table, text: str) -> bool:
    """Add one column definition. False when it is not understood."""
    found = re.match(rf"({_IDENT})\s+(\S.*)$", text, re.I | re.S)
    if found is None:
        return False
    name = _unquote(found.group(1))
    rest = found.group(2)
    shallow = _shallow(rest)
    primary = bool(re.search(r"\bprimary\s+key\b", shallow, re.I))
    default = None
    if found_default := re.search(r"\bdefault\s+", shallow, re.I):
        end = _CONSTRAINT_END.search(shallow, found_default.end())
        default = _normalise(rest[found_default.end():end.start() if end else len(rest)])
    references = None
    if re.search(r"\breferences\b", shallow, re.I):
        references = _references(state, rest)
        table.constraints[_default_name(table, name, "fkey")] = name
    table.columns[name] = Column(
        name=name,
        nullable=not (primary or re.search(r"\bnot\s+null\b", shallow, re.I)),
        references=references,
        default=default,
        primary_key=primary,
    )
    if primary:
        table.constraints[_default_name(table, None, "pkey")] = None
    return True


def _default_name(table: Table, column: str | None, suffix: str) -> str:
    """PostgreSQL's name for an unnamed constraint, e.g. `tasks_owner_id_fkey`."""
    base = table.name.split(".", 1)[1]
    return f"{base}_{column}_{suffix}" if column else f"{base}_{suffix}"


def _table_constraint(state: _State, table: Table, text: str) -> bool:
    """A table-level constraint, optionally named. False when not understood."""
    name = None
    if found := re.match(rf"constraint\s+({_IDENT})\s+", text, re.I):
        name = _unquote(found.group(1))
        text = text[found.end():]
    lowered = text.lower()
    if found := re.match(r"primary\s+key\s*\(", lowered):
        group = _balanced(text, found.end() - 1)
        columns = _ident_list(group[0]) if group else None
        if not columns or any(column not in table.columns for column in columns):
            return False
        for column in columns:
            table.columns[column].primary_key = True
            table.columns[column].nullable = False
        table.constraints[name or _default_name(table, None, "pkey")] = None
        return True
    if found := re.match(r"foreign\s+key\s*\(", lowered):
        group = _balanced(text, found.end() - 1)
        columns = _ident_list(group[0]) if group else None
        if not columns or any(column not in table.columns for column in columns):
            return False
        if len(columns) == 1:
            table.columns[columns[0]].references = _references(state, text[group[1]:])
            table.constraints[name or _default_name(table, columns[0], "fkey")] = columns[0]
        elif name:
            table.constraints[name] = None   # composite: no single owner column
        return True
    if re.match(r"(?:unique|check|exclude)\b", lowered):
        if name:
            table.constraints[name] = None
        return True
    return False


def _create_columns(state: _State, table: Table, rest: str) -> None:
    """The column list of CREATE TABLE. Anything but a plain list leaves columns None:
    `AS SELECT`, `PARTITION OF`, `LIKE` and `INHERITS` take columns from elsewhere."""
    rest = rest.strip()
    group = _balanced(rest, 0) if rest.startswith("(") else None
    if group is None or re.search(r"\binherits\b", rest[group[1]:], re.I):
        return
    table.columns = {}
    for element in _split_top(group[0]):
        lowered = element.lower()
        if lowered.startswith("like "):
            table.columns = None
            return
        understood = (_table_constraint(state, table, element)
                      if re.match(r"(?:constraint|primary\s+key|foreign\s+key|unique|check|"
                                  r"exclude)\b", lowered)
                      else _column(state, table, element))
        if not understood:
            table.columns = None
            return


def _alter_columns(state: _State, flat: str) -> None:
    found = re.match(
        rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_QNAME}\s*\*?\s+(.*)$",
        flat, re.I | re.S)
    if found is None:
        return
    table = state.tables.get(_qualify(found.group(1)))
    if table is None or table.columns is None:
        return
    for action in _split_top(found.group(2)):
        if not _alter_action(state, table, action):
            table.columns = None
            table.constraints = {}
            return


def _alter_action(state: _State, table: Table, action: str) -> bool:
    lowered = action.lower()
    if _HARMLESS_ACTION.match(lowered):
        return True
    if re.match(r"add\s+(?:constraint|primary\s+key|foreign\s+key|unique|check|exclude)\b",
                lowered):
        return _table_constraint(state, table, action[len("add"):].strip())
    if found := re.match(r"add\s+(?:column\s+)?(if\s+not\s+exists\s+)?", lowered):
        body = action[found.end():]
        name = re.match(_IDENT, body, re.I)
        if found.group(1) and name and _unquote(name.group(0)) in table.columns:
            return True
        return _column(state, table, body)
    if found := re.match(rf"drop\s+constraint\s+(if\s+exists\s+)?({_IDENT})", action, re.I):
        name = _unquote(found.group(2))
        if name not in table.constraints:
            # Only a foreign key this replay recorded can be lost by dropping a
            # constraint it does not know, perhaps under a name PostgreSQL truncated. So
            # an unknown name is harmless on a table with no recorded foreign key, and
            # so is PostgreSQL's default name for a CHECK, `<table>_<column>_check`,
            # which the corpus drops constantly to widen enum-like columns. Anything
            # else might have removed owner evidence, so the columns go unknown.
            recorded = any(column.references for column in table.columns.values())
            return name.endswith("_check") or not recorded
        column = table.constraints.pop(name)
        if column in table.columns:
            table.columns[column].references = None
        return True
    if found := re.match(rf"drop\s+(?:column\s+)?(if\s+exists\s+)?({_IDENT})", action, re.I):
        name = _unquote(found.group(2))
        if name not in table.columns:
            return bool(found.group(1))
        del table.columns[name]
        return True
    if found := re.match(rf"rename\s+constraint\s+({_IDENT})\s+to\s+({_IDENT})$", action, re.I):
        old, new = _unquote(found.group(1)), _unquote(found.group(2))
        if old not in table.constraints:
            return False
        table.constraints[new] = table.constraints.pop(old)
        return True
    if found := re.match(rf"rename\s+(?:column\s+)?({_IDENT})\s+to\s+({_IDENT})$", action, re.I):
        return _rename_column(state, table, _unquote(found.group(1)), _unquote(found.group(2)))
    if found := re.match(rf"alter\s+(?:column\s+)?({_IDENT})\s+(.*)$", action, re.I | re.S):
        column = table.columns.get(_unquote(found.group(1)))
        return column is not None and _alter_column(column, found.group(2).strip())
    return False


def _alter_column(column: Column, change: str) -> bool:
    lowered = change.lower()
    if re.fullmatch(r"set\s+not\s+null", lowered):
        column.nullable = False
    elif re.fullmatch(r"drop\s+not\s+null", lowered):
        column.nullable = True
    elif found := re.match(r"set\s+default\s+", lowered):
        column.default = _normalise(change[found.end():])
    elif re.fullmatch(r"drop\s+default", lowered):
        column.default = None
    elif not _HARMLESS_COLUMN_CHANGE.match(lowered):
        return False
    return True


def _rename_column(state: _State, table: Table, old: str, new: str) -> bool:
    if old not in table.columns or new in table.columns:
        return False
    table.columns = {(new if name == old else name): column
                     for name, column in table.columns.items()}
    table.columns[new].name = new
    table.constraints = {name: (new if column == old else column)
                         for name, column in table.constraints.items()}
    for policy in table.policies.values():
        policy.renamed_since.extend(item for item in (old, new)
                                    if item not in policy.renamed_since)
    state.retarget(f"{table.name}.{old}", f"{table.name}.{new}")
    return True
