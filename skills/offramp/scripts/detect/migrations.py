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

RFC-0004 adds functions and who may execute them. Bodies are read, never replayed, and
functions are tracked apart from tables: a function statement this module does not
understand makes the functions unknown and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rls import UID_PATTERN
from spec import Column
from spec import Function as SchemaFunction
from spec import Policy as SchemaPolicy
from spec import Schema
from spec import Table as SchemaTable
from spec import Write
from walk import rel

MIGRATIONS_DIR = Path("supabase") / "migrations"

#: A migration's run order comes from the version number its name starts with: the
#: Supabase CLI writes `<version>_<name>.sql`, Lovable writes `<version>-<uuid>.sql`. A
#: file with no leading version has no knowable place in that order.
_TIMESTAMPED = re.compile(r"^\d+(?:[_-][^/]*)?\.sql$")
_VERSION = re.compile(r"^(\d+)")

#: A quoted identifier may contain anything, a doubled `""` standing for one quote.
_IDENT = r'(?:"(?:[^"]|"")+"|[a-z_][a-z0-9_$]*)'
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
#: Control flow whose effect cannot be known without running it. `execute` is dynamic
#: SQL, but not as a privilege (`grant execute on`) or a trigger's `execute function`.
_DYNAMIC = re.compile(
    r"\b(?:execute(?!\s+(?:on|function|procedure)\b)|loop|elsif|else|perform)\b")
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
    #: Constraint name, as PostgreSQL names it -> (kind, columns): "fkey" and its one
    #: column, "pkey" and its columns, or "other". Dropping one undoes what it made;
    #: dropping a constraint this does not know may make the columns unknown.
    constraints: dict[str, tuple[str, list[str]]] = field(default_factory=dict)


@dataclass
class Replay:
    files: list[str]
    tables: dict[str, Table]
    unparseable: list[str]     # "path:line: reason"
    untimestamped: list[str] = field(default_factory=list)   # order unknown
    latest: str | None = None  # highest leading version among the files
    functions: dict[str, "_Function"] = field(default_factory=dict)   # by signature
    function_problems: list[str] = field(default_factory=list)       # "path:line: reason"


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
        if char == ";" and not _inside_atomic("".join(masked)):
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


_ATOMIC = re.compile(r"\bbegin\s+atomic\b", re.I)


def _inside_atomic(masked: str) -> bool:
    """True inside a SQL-standard function body (`begin atomic ... end`, PostgreSQL 14),
    whose statements end in `;` but belong to the CREATE FUNCTION around them. Only an
    `end` that closes the body ends it; a `case ... end` inside it is a known limit."""
    opened = None
    for opened in _ATOMIC.finditer(masked):
        pass
    if opened is None:
        return False
    return not re.search(r"\bend\s*$", masked[opened.end():], re.I)


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.lower()


_QUOTED = re.compile(r'("(?:[^"]|"")*")')


def _collapse(text: str) -> str:
    """Runs of whitespace to one space, except inside a quoted identifier, where the
    whitespace is part of the name."""
    parts = _QUOTED.split(text)
    return "".join(part if index % 2 else re.sub(r"\s+", " ", part)
                   for index, part in enumerate(parts)).strip()


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
        self.functions: dict[str, _Function] = {}
        self.function_problems: list[str] = []
        #: Who a new function grants EXECUTE to. PostgreSQL's built-in default is a
        #: global grant to `public`; Supabase adds `anon` and `authenticated` for
        #: schema `public`, per schema. A function starts with the union of the two.
        self.default_global: set[str] = {"public"}
        self.default_schema: dict[str, set[str]] = {"public": {"anon", "authenticated"}}

    def function_problem(self, where: str, reason: str) -> None:
        self.function_problems.append(f"{where}: {reason}")

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
    flat = _collapse(masked)
    lowered = flat.lower()

    def match(pattern: re.Pattern) -> "re.Match | None":
        return re.match(pattern.pattern, flat, re.I | re.S)

    if _DO_BLOCK.match(lowered):
        return _apply_do(state, original, where, line)
    if _FN_STATEMENT.match(lowered):
        _apply_function(state, original, flat, where, line)
        return None
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

    RFC-0004: function statements in the block follow the same rule, and a block that
    cannot be followed makes the functions unknown as well as the tables.
    """
    tag = _DOLLAR.search(original)
    if tag is None:
        return "do block not understood"
    close = original.find(tag.group(0), tag.end())
    body = original[tag.end():close if close != -1 else len(original)]
    lowered_body = body.lower()
    path = where.rsplit(":", 1)[0]
    first_line = line + original[:tag.end()].count("\n")
    if not _DO_TOUCHES.search(lowered_body):
        _do_columns(state, body)
        _do_functions(state, body, path, first_line)
        return None

    def fail(reason: str) -> str:
        if _FN_TOUCHES.search(lowered_body):
            state.function_problem(where, reason)
        return reason

    if _DYNAMIC.search(re.sub(r"'(?:[^']|'')*'", "''", lowered_body)):
        return fail("do block with dynamic SQL touching tables or RLS")
    for inner_original, inner_masked, inner_line in split_statements(body):
        statement = _unguard(_collapse(inner_masked))
        if statement is None:
            return fail("do block not understood")
        inner_start = inner_line
        lowered = statement.lower()
        if not statement or _NOISE.match(lowered):
            continue
        at = first_line + inner_start - 1 + _line_offset(inner_original, statement)
        if lowered.startswith("if ") or lowered.startswith("do "):
            if _DO_TOUCHES.search(lowered):
                return fail("do block with a condition that is not an existence check")
            _forget_columns(state, statement)
            if _FN_TOUCHES.search(lowered):
                state.function_problem(f"{path}:{at}", "function statement under a condition "
                                                       "that is not an existence check")
            continue
        reason = _apply(state, _function_original(inner_original, statement), statement,
                        f"{path}:{at}", at)
        if reason:
            return fail(reason)
    return None


def _unguard(statement: str) -> str | None:
    """`statement` without its leading BEGIN and existence guards. None when a guard is
    not understood."""
    while True:
        lowered = statement.lower()
        if lowered.startswith("begin "):
            statement = statement[len("begin "):].strip()
            continue
        guard = _GUARD.match(lowered)
        if guard is None:
            return statement
        group = _balanced(statement, guard.end())
        if group is None:
            return None
        rest = statement[group[1]:].strip()
        if not rest.lower().startswith("then"):
            return None
        statement = rest[len("then"):].strip()


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


def replay(root: Path, extra: "tuple[tuple[str, str], ...] | list" = ()) -> Replay:
    """Replay the repository's migrations. `extra` adds (relative path, text) pairs as
    though they were files in `supabase/migrations/`: the fix renderer's round trip
    replays its own output this way, in memory, before writing anything."""
    state = _State()
    unparseable: list[str] = []
    texts = {rel(root, path): path for path in migration_files(root)}
    texts.update(dict(extra))
    files = sorted(texts, key=lambda relative: relative.rsplit("/", 1)[-1])
    for relative in files:
        source = texts[relative]
        text = (source.read_text(encoding="utf-8", errors="ignore")
                if isinstance(source, Path) else source)
        for original, masked, line in split_statements(text):
            reason = _apply(state, original, masked, f"{relative}:{line}", line)
            if reason:
                unparseable.append(f"{relative}:{line}: {reason}")
    untimestamped = [rel(root, path) for path in _sql_files(root)
                     if not _TIMESTAMPED.match(path.name)]
    return Replay(files=files, tables=state.tables,
                  unparseable=unparseable, untimestamped=untimestamped,
                  latest=_latest(relative.rsplit("/", 1)[-1] for relative in files),
                  functions=state.functions, function_problems=state.function_problems)


def _latest(names) -> str | None:
    """The highest leading version, as written. Compared as a number: `9_a.sql` runs
    before `10_b.sql` in no tool, but a version is a number and sorts as one here."""
    versions = [match.group(1) for name in names if (match := _VERSION.match(name))]
    return max(versions, key=lambda version: (int(version), version)) if versions else None


def detect_schema(root: Path, extra=()) -> Schema | None:
    """`Datastore.schema`. None when the repository has no migration files at all."""
    if not (_sql_files(root) or extra):
        return None
    result = replay(root, extra)
    tables = None
    if not (result.unparseable or result.untimestamped):
        tables = [_schema_table(table) for _, table in sorted(result.tables.items())]
    functions = None
    if not (result.function_problems or result.untimestamped):
        functions = [_schema_function(function)
                     for _, function in sorted(result.functions.items())]
    return Schema(migrations=result.files, unreplayable=result.unparseable,
                  unordered=result.untimestamped, latest=result.latest, tables=tables,
                  functions=functions, functions_unreplayable=result.function_problems)


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
    named = {}   # kind -> the name an inline `constraint <name>` gave it
    for found in re.finditer(rf"\bconstraint\s+({_IDENT})\s+(\w+)", shallow, re.I):
        kind = {"primary": "pkey", "references": "fkey"}.get(found.group(2).lower(), "other")
        constraint = _unquote(rest[found.start(1):found.end(1)])
        if kind == "other":
            table.constraints[constraint] = ("other", [])
        else:
            named[kind] = constraint
    references = None
    if re.search(r"\breferences\b", shallow, re.I):
        references = _references(state, rest)
        table.constraints[named.get("fkey") or _default_name(table, name, "fkey")] = (
            "fkey", [name])
    table.columns[name] = Column(
        name=name,
        nullable=not (primary or re.search(r"\bnot\s+null\b", shallow, re.I)),
        references=references,
        default=default,
        primary_key=primary,
    )
    if primary:
        table.constraints[named.get("pkey") or _default_name(table, None, "pkey")] = (
            "pkey", [name])
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
        table.constraints[name or _default_name(table, None, "pkey")] = ("pkey", columns)
        return True
    if found := re.match(r"foreign\s+key\s*\(", lowered):
        group = _balanced(text, found.end() - 1)
        columns = _ident_list(group[0]) if group else None
        if not columns or any(column not in table.columns for column in columns):
            return False
        if len(columns) == 1:
            table.columns[columns[0]].references = _references(state, text[group[1]:])
            table.constraints[name or _default_name(table, columns[0], "fkey")] = (
                "fkey", columns)
        elif name:
            table.constraints[name] = ("other", [])   # composite: no single owner column
        return True
    if re.match(r"(?:unique|check|exclude)\b", lowered):
        if name:
            table.constraints[name] = ("other", [])
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


#: The table an ALTER TABLE names, even inside an EXECUTE string. A name built at run
#: time (`public.%I`, `' || t`) does not match: the lookaheads reject a keyword taken for
#: the name and a name cut short at a placeholder.
_ALTER_TARGET = re.compile(
    rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?(?!(?:if|only)\s){_QNAME}(?=[\s*;']|$)",
    re.I | re.S)


def _do_columns(state: _State, body: str) -> None:
    """A DO block that touches no access control can still change columns, and the
    corpus guards column changes that way. Its ALTER TABLE statements are replayed as
    `_apply_do` replays policies. What cannot be followed -- dynamic SQL, a condition
    that is not an existence check -- makes the columns of the tables it names unknown."""
    if not re.search(r"\balter\s+table\b", body, re.I):
        return
    if _DYNAMIC.search(re.sub(r"'(?:[^']|'')*'", "''", body.lower())):
        _forget_columns(state, body)
        return
    for _, masked, _ in split_statements(body):
        statement = _unguard(_collapse(masked))
        if statement is None or statement.lower().startswith(("if ", "do ")):
            _forget_columns(state, masked)
        elif _ALTER_TABLE.match(statement.lower()):
            _alter_columns(state, statement)


def _forget_columns(state: _State, text: str) -> None:
    """Make unknown the columns of every table an ALTER TABLE in `text` may change: every
    table, when one of them names its table only at run time."""
    names = [_qualify(found.group(1)) for found in _ALTER_TARGET.finditer(text)]
    if len(names) < len(re.findall(r"\balter\s+table\b", text, re.I)):
        tables = list(state.tables.values())
    else:
        tables = [state.tables[name] for name in names if name in state.tables]
    for table in tables:
        table.columns = None
        table.constraints = {}


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
            # which the corpus drops constantly to widen enum-like columns. Any other
            # name ending in `_check` is a name someone chose, and could be anything.
            # Anything else might have removed owner evidence, so the columns go unknown.
            recorded = any(column.references for column in table.columns.values())
            base = re.escape(table.name.split(".", 1)[1])
            return not recorded or bool(re.fullmatch(rf"{base}_.+_check\d*", name))
        kind, columns = table.constraints.pop(name)
        for column in (table.columns[each] for each in columns if each in table.columns):
            if kind == "fkey":
                column.references = None
            elif kind == "pkey":
                column.primary_key = False
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
    table.constraints = {
        name: (kind, [new if column == old else column for column in columns])
        for name, (kind, columns) in table.constraints.items()}
    for policy in table.policies.values():
        policy.renamed_since.extend(item for item in (old, new)
                                    if item not in policy.renamed_since)
    state.retarget(f"{table.name}.{old}", f"{table.name}.{new}")
    return True


# -- Functions -------------------------------------------------------------------------
#
# RFC-0004. Everything below changes the replay's functions and nothing else. A function
# statement it does not understand records a problem, which makes `Schema.functions`
# None; it never returns an unparseable reason, so it never costs the RLS checks their
# answer. Bodies are read, shallowly, and never replayed.

_FN_STATEMENT = re.compile(
    r"^(?:create\s+(?:or\s+replace\s+)?function|drop\s+function|alter\s+function"
    r"|alter\s+default\s+privileges\b.*\bon\s+(?:functions|routines)\b"
    r"|(?:grant|revoke)\b.*?\bon\s+(?:function|routine|all\s+(?:functions|routines))\b)",
    re.S)
#: A function statement anywhere in a text, string literals included: used to decide
#: whether a block the replay cannot follow had any bearing on functions.
_FN_TOUCHES = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?function\b|\b(?:drop|alter)\s+function\b"
    r"|\b(?:grant|revoke)\b[^;]*?\bon\s+(?:function|routine|all\s+(?:functions|routines))\b"
    r"|\balter\s+default\s+privileges\b[^;]*?\bon\s+(?:functions|routines)\b", re.S)
_TRACKED = ("public", "anon", "authenticated")
#: The role that runs the migrations: default privileges set for it apply to the
#: functions they create, and those set FOR ROLE anyone else do not.
_MIGRATION_ROLES = {"postgres", "current_user", "session_user", "current_role"}
#: Roles whose ownership leaves a function bypassing RLS as it would by default.
_OWN_ROLES = _MIGRATION_ROLES | {"supabase_admin"}
_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint",
    "bool": "boolean", "float8": "double precision", "float": "double precision",
    "float4": "real", "varchar": "character varying", "char": "character",
    "bpchar": "character", "decimal": "numeric", "varbit": "bit varying",
    "timestamptz": "timestamp with time zone", "timestamp": "timestamp without time zone",
    "timetz": "time with time zone", "time": "time without time zone",
}
_MULTIWORD_TYPES = {
    "double precision", "character varying", "bit varying", "timestamp with time zone",
    "timestamp without time zone", "time with time zone", "time without time zone",
}
_HARMLESS_FN_ACTION = re.compile(
    r"(?:\s*(?:immutable|stable|volatile|(?:not\s+)?leakproof|strict|restrict"
    r"|called\s+on\s+null\s+input|returns\s+null\s+on\s+null\s+input"
    r"|cost\s+[\d.]+|rows\s+[\d.]+|parallel\s+(?:unsafe|restricted|safe)))*\s*", re.I)
_WRITE_FORMS = (
    ("insert", re.compile(rf"\binsert\s+into\s+{_QNAME}", re.I)),
    ("update", re.compile(
        rf"\bupdate\s+(?:only\s+)?{_QNAME}\s*\*?\s+(?:(?:as\s+)?{_IDENT}\s+)?set\b", re.I)),
    ("delete", re.compile(rf"\bdelete\s+from\s+(?:only\s+)?{_QNAME}", re.I)),
)
_BODY_DYNAMIC = re.compile(r"\bexecute\b(?!\s+(?:on|function|procedure)\b)|\bdblink\w*\s*\(")
_COMPARE = r"(?:=|<>|!=|\bis\s+(?:not\s+)?distinct\s+from\b)"
#: Words before a parenthesis that are not a call that checks the caller: a value list,
#: a subquery, or a built-in that only passes the value along.
_NOT_A_CHECK = {
    "values", "in", "exists", "select", "where", "and", "or", "not", "if", "when", "then",
    "else", "return", "returning", "coalesce", "nullif", "greatest", "least", "concat",
    "format", "cast", "array", "row", "any", "all", "some", "raise", "notice", "into",
    "set", "on", "using", "check", "from", "join", "case", "elsif", "perform",
}


@dataclass
class _Function:
    name: str                  # "public.add_points"
    types: list[str]           # callable parameters' types, normalised
    params: list[str]
    definer: bool
    trigger: bool
    owner: str | None
    grants: set[str]
    writes: list[Write] | None
    compares_caller: list[str]
    role_check: bool
    evidence: str

    @property
    def signature(self) -> str:
        return f"{self.name}({', '.join(self.types)})"


def _schema_function(function: _Function) -> SchemaFunction:
    grants = function.grants
    executable = [role for role in ("anon", "authenticated") if {"public", role} & grants]
    return SchemaFunction(
        name=function.name, signature=function.signature, params=list(function.params),
        definer=function.definer, trigger=function.trigger, owner=function.owner,
        grants=sorted(grants), executable_by=executable,
        writes=None if function.writes is None else [
            Write(verb=w.verb, table=w.table, uses=list(w.uses), line=w.line)
            for w in function.writes],
        compares_caller=list(function.compares_caller), role_check=function.role_check,
        evidence=function.evidence,
    )


def _function_original(original: str, statement: str) -> str:
    """For a CREATE FUNCTION found behind a guard in a DO block, the original text from
    the CREATE on: the body is masked in `statement`, and reading it needs the source."""
    if re.match(r"create\s+(?:or\s+replace\s+)?function\b", statement, re.I):
        found = re.search(r"\bcreate\s+(?:or\s+replace\s+)?function\b", original, re.I)
        if found:
            return original[found.start():]
    return statement


def _do_functions(state: _State, body: str, path: str, first_line: int) -> None:
    """Function statements in a DO block that touches no tables, policies or RLS. As for
    policies: a statement under an existence guard is read as written; dynamic SQL or
    any other condition makes the functions unknown."""
    if not _FN_TOUCHES.search(body.lower()):
        return
    if _DYNAMIC.search(re.sub(r"'(?:[^']|'')*'", "''", body.lower())):
        state.function_problem(f"{path}:{first_line}", "do block with dynamic SQL touching "
                                                       "functions")
        return
    for inner_original, inner_masked, inner_line in split_statements(body):
        statement = _unguard(_collapse(inner_masked))
        at = first_line + inner_line - 1
        if statement is None:
            if _FN_TOUCHES.search(inner_masked.lower()):
                state.function_problem(f"{path}:{at}", "do block not understood")
            continue
        lowered = statement.lower()
        if not statement or _NOISE.match(lowered):
            continue
        at += _line_offset(inner_original, statement)
        if lowered.startswith(("if ", "do ")):
            if _FN_TOUCHES.search(lowered):
                state.function_problem(f"{path}:{at}", "function statement under a condition "
                                                       "that is not an existence check")
            continue
        if _FN_STATEMENT.match(lowered):
            _apply_function(state, _function_original(inner_original, statement),
                            _collapse(statement), f"{path}:{at}", at)


def _apply_function(state: _State, original: str, flat: str, where: str, line: int) -> None:
    lowered = flat.lower()
    if re.match(r"create\s+(?:or\s+replace\s+)?function\b", lowered):
        reason = _create_function(state, original, flat, where, line)
    elif lowered.startswith("drop function"):
        reason = _drop_function(state, flat)
    elif lowered.startswith("alter function"):
        reason = _alter_function(state, flat)
    elif lowered.startswith("alter default privileges"):
        reason = _default_privileges(state, flat)
    else:
        reason = _grant(state, flat)
    if reason:
        state.function_problem(where, reason)


# -- names, arguments and types --

def _words(text: str) -> list[str]:
    """Split on whitespace outside quotes and parentheses; `=` is a word of its own."""
    words: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    for char in re.sub(r"\s+(?=[(\[])", "", text):
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        if not quoted and depth == 0 and (char.isspace() or char == "="):
            if current:
                words.append("".join(current))
                current = []
            if char == "=":
                words.append("=")
            continue
        current.append(char)
    if current:
        words.append("".join(current))
    return words


def _normalise_type(text: str) -> str:
    text = re.sub(r"\([^()]*\)", "", _collapse(text)).strip()
    arrays = ""
    if found := re.search(r"(?:\s*\[\s*\d*\s*\])+$", text):
        arrays = "[]" * found.group(0).count("[")
        text = text[:found.start()].strip()
    parts = [_unquote(part) for part in re.split(r'\.(?=(?:[^"]*"[^"]*")*[^"]*$)', text)]
    if len(parts) == 2 and parts[0] == "pg_catalog":
        parts = parts[1:]
    name = re.sub(r"\s+", " ", ".".join(parts))
    return _TYPE_ALIASES.get(name, name) + arrays


def _arguments(text: str) -> list[tuple[str, str, str]] | None:
    """(mode, name, type) per argument of a function's parameter list."""
    arguments = []
    for item in _split_top(text):
        words = _words(item)
        for index, word in enumerate(words):
            if word == "=" or word.lower() == "default":
                words = words[:index]
                break
        mode = "in"
        if words and words[0].lower() in ("in", "out", "inout", "variadic"):
            mode = words.pop(0).lower()
        if not words:
            return None
        phrase = re.sub(r"\([^()]*\)|\[\s*\d*\s*\]", "", " ".join(words)).lower().strip()
        if len(words) == 1 or phrase in _MULTIWORD_TYPES:
            name, type_ = "", " ".join(words)
        else:
            name, type_ = _unquote(words[0]), " ".join(words[1:])
        arguments.append((mode, name, _normalise_type(type_)))
    return arguments


def _callable(arguments: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The arguments a caller passes, which also make up the signature: not OUT."""
    return [argument for argument in arguments if argument[0] != "out"]


def _function_refs(text: str) -> list[tuple[str, list[str] | None]] | None:
    """`name[(args)][, name[(args)] ...]` as (qualified name, argument types or None)."""
    refs = []
    rest = text.strip()
    while rest:
        found = re.match(rf"{_QNAME}\s*", rest, re.I)
        if found is None:
            return None
        name = _qualify(found.group(1))
        rest = rest[found.end():]
        types = None
        if rest.startswith("("):
            group = _balanced(rest, 0)
            arguments = _arguments(group[0]) if group else None
            if arguments is None:
                return None
            types = [type_ for _, _, type_ in _callable(arguments)]
            rest = rest[group[1]:].strip()
        refs.append((name, types))
        if rest.startswith(","):
            rest = rest[1:].strip()
        elif rest:
            return None
    return refs


def _resolve(state: _State, name: str, types: list[str] | None,
             verb: str) -> tuple[list[_Function], str | None]:
    """The functions a reference names. A name these migrations never define is a
    function made elsewhere, and is no business of theirs; a name they define but whose
    argument types do not match, or an overloaded name without types, is not understood."""
    candidates = [f for f in state.functions.values() if f.name == name]
    if types is not None:
        exact = [f for f in candidates if f.types == types]
        if exact or not candidates:
            return exact, None
        return [], (f"{verb} names {name}({', '.join(types)}), which these migrations do "
                    f"not define with those argument types")
    if len(candidates) > 1:
        return [], f"{verb} names {name}, which has {len(candidates)} overloads"
    return candidates, None


def _roles(text: str) -> list[str]:
    text = re.sub(r"\s+(?:with\s+grant\s+option|granted\s+by\s+\S+|cascade|restrict)\s*$",
                  "", text.strip(), flags=re.I)
    names = [re.sub(r"^group\s+", "", item.strip(), flags=re.I) for item in text.split(",")]
    return [_unquote(name) for name in names if name]


# -- statements --

def _create_function(state: _State, original: str, flat: str, where: str,
                     line: int) -> str | None:
    found = re.match(rf"create\s+(?:or\s+replace\s+)?function\s+{_QNAME}\s*\(", flat, re.I)
    group = _balanced(flat, found.end() - 1) if found else None
    arguments = _arguments(group[0]) if group else None
    if arguments is None:
        return "create function statement not understood"
    callable_ = _callable(arguments)
    name = _qualify(found.group(1))
    clauses = flat[group[1]:].lower()
    language = re.search(r'\blanguage\s+"?([a-z_][a-z0-9_]*)"?', clauses)
    function = _Function(
        name=name, types=[type_ for _, _, type_ in callable_],
        params=[name_ for _, name_, _ in callable_],
        definer=bool(re.search(r"\bsecurity\s+definer\b", clauses)),
        trigger=bool(re.search(r"\breturns\s+(?:event_)?trigger\b", clauses)),
        owner=None, grants=set(), writes=None, compares_caller=[], role_check=False,
        evidence=where,
    )
    previous = state.functions.get(function.signature)
    if previous is not None:
        # CREATE OR REPLACE keeps the function's grants and owner, as PostgreSQL does.
        function.grants, function.owner = previous.grants, previous.owner
    else:
        schema = name.split(".", 1)[0]
        function.grants = set(state.default_global) | state.default_schema.get(schema, set())
    body = None if _ATOMIC.search(clauses) else _function_body(original)
    if body is not None and language and language.group(1) in ("sql", "plpgsql"):
        text, offset = body
        _read_body(function, text, line + offset)
    state.functions[function.signature] = function
    return None


def _function_body(original: str) -> tuple[str, int] | None:
    """The body of a CREATE FUNCTION, from its dollar-quoted or single-quoted string, and
    how many lines into the statement it starts."""
    returns = re.search(r"\breturns\b", original, re.I)
    start = returns.end() if returns else 0
    found = re.compile(r"\bas\s+(\$[A-Za-z0-9_]*\$|')", re.I).search(original, start)
    if found is None:
        return None
    opening = found.group(1)
    begin = found.end()
    if opening != "'":
        close = original.find(opening, begin)
        if close == -1:
            return None
        return original[begin:close], original[:begin].count("\n")
    end = begin
    while end < len(original):
        if original.startswith("''", end):
            end += 2
            continue
        if original[end] == "'":
            return original[begin:end].replace("''", "'"), original[:begin].count("\n")
        end += 1
    return None


def _drop_function(state: _State, flat: str) -> str | None:
    found = re.match(r"drop\s+function\s+(?:if\s+exists\s+)?(.*?)(?:\s+(?:cascade|restrict))?$",
                     flat, re.I | re.S)
    refs = _function_refs(found.group(1)) if found else None
    if refs is None:
        return "drop function statement not understood"
    for name, types in refs:
        functions, reason = _resolve(state, name, types, "drop function")
        if reason:
            return reason
        for function in functions:
            del state.functions[function.signature]
    return None


def _alter_function(state: _State, flat: str) -> str | None:
    found = re.match(rf"alter\s+function\s+{_QNAME}\s*", flat, re.I)
    if found is None:
        return "alter function statement not understood"
    reference, rest = flat[found.start(1):found.end()], flat[found.end():]
    if rest.startswith("("):
        group = _balanced(rest, 0)
        if group is None:
            return "alter function statement not understood"
        reference, rest = reference + rest[:group[1]], rest[group[1]:]
    refs = _function_refs(reference)
    if refs is None or len(refs) != 1:
        return "alter function statement not understood"
    functions, reason = _resolve(state, *refs[0], "alter function")
    if reason or not functions:
        return reason
    [function] = functions
    action = rest.strip()
    lowered = action.lower()
    if renamed := re.fullmatch(rf"rename\s+to\s+({_IDENT})", action, re.I):
        del state.functions[function.signature]
        function.name = f"{function.name.split('.', 1)[0]}.{_unquote(renamed.group(1))}"
        state.functions[function.signature] = function
        return None
    if moved := re.fullmatch(rf"set\s+schema\s+({_IDENT})", action, re.I):
        del state.functions[function.signature]
        function.name = f"{_unquote(moved.group(1))}.{function.name.split('.', 1)[1]}"
        state.functions[function.signature] = function
        return None
    if owner := re.fullmatch(rf"owner\s+to\s+({_IDENT})", action, re.I):
        role = _unquote(owner.group(1))
        function.owner = None if role in _OWN_ROLES else role
        return None
    rest = lowered
    if security := re.search(r"\b(?:external\s+)?security\s+(definer|invoker)\b", lowered):
        function.definer = security.group(1) == "definer"
        rest = (lowered[:security.start()] + lowered[security.end():]).strip()
    if rest and not (re.match(r"(?:set|reset)\s", rest) or _HARMLESS_FN_ACTION.fullmatch(rest)):
        return "alter function statement not understood"
    return None


_GRANT = re.compile(
    r"(grant|revoke)\s+(grant\s+option\s+for\s+)?(.+?)\s+on\s+(.+?)\s+(?:to|from)\s+(.+)$",
    re.I | re.S)


def _privilege(text: str) -> bool:
    """Whether a privilege list includes EXECUTE, the only one a function has."""
    return any(item.strip().lower() in ("execute", "all", "all privileges")
               for item in text.split(","))


def _grant(state: _State, flat: str) -> str | None:
    found = _GRANT.match(flat)
    if found is None:
        return "grant or revoke on a function not understood"
    verb, option, privileges, target, roles = found.groups()
    if option or not _privilege(privileges):
        return None
    roles = [role for role in _roles(roles) if role in _TRACKED]
    if every := re.fullmatch(r"all\s+(?:functions|routines)\s+in\s+schema\s+(.+)", target,
                             re.I | re.S):
        schemas = {_unquote(item) for item in every.group(1).split(",")}
        functions = [f for f in state.functions.values() if f.name.split(".", 1)[0] in schemas]
    else:
        listed = re.fullmatch(r"(?:function|routine)\s+(.+)", target, re.I | re.S)
        refs = _function_refs(listed.group(1)) if listed else None
        if refs is None:
            return "grant or revoke on a function not understood"
        functions = []
        for name, types in refs:
            found_functions, reason = _resolve(state, name, types, verb.lower())
            if reason:
                return reason
            functions += found_functions
    for function in functions:
        if verb.lower() == "grant":
            function.grants |= set(roles)
        else:
            function.grants -= set(roles)
    return None


def _default_privileges(state: _State, flat: str) -> str | None:
    """ALTER DEFAULT PRIVILEGES for functions created later. Per-schema defaults only add
    to the global ones, as in PostgreSQL, so a per-schema revoke cannot remove the
    built-in grant to `public`. Defaults for another role's objects do not apply to the
    functions these migrations create."""
    found = re.match(r"alter\s+default\s+privileges\s+(.*?)\s*((?:grant|revoke)\s.*)$",
                     flat, re.I | re.S)
    if found is None:
        return "alter default privileges not understood"
    options, statement = found.groups()
    owners = re.search(rf"\bfor\s+(?:role|user)\s+({_IDENT}(?:\s*,\s*{_IDENT})*)", options,
                       re.I)
    if owners and not {_unquote(item) for item in owners.group(1).split(",")} & _MIGRATION_ROLES:
        return None
    schemas = re.search(rf"\bin\s+schema\s+({_IDENT}(?:\s*,\s*{_IDENT})*)", options, re.I)
    grant = _GRANT.match(statement)
    if grant is None or not re.fullmatch(r"functions|routines", grant.group(4).strip(), re.I):
        return "alter default privileges not understood"
    verb, option, privileges, _, roles = grant.groups()
    if option or not _privilege(privileges):
        return None
    roles = {role for role in _roles(roles) if role in _TRACKED}
    targets = ([state.default_schema.setdefault(_unquote(item), set())
                for item in schemas.group(1).split(",")] if schemas
               else [state.default_global])
    for target in targets:
        if verb.lower() == "grant":
            target |= roles
        else:
            target -= roles
    return None


# -- reading a body --

def _param_pattern(name: str) -> str:
    escaped = re.escape(name.lower())
    return rf'(?:"{escaped}"|(?<![\w$."]){escaped}(?![\w$]))'


def _uses(text: str, params: list[str]) -> list[str]:
    """Parameters a statement mentions, by name or by position, in parameter order."""
    positions = {int(n) for n in re.findall(r"(?<![\w$])\$(\d+)(?!\d)", text)}
    uses = []
    for index, name in enumerate(params, start=1):
        if index in positions or (name and re.search(_param_pattern(name), text)):
            uses.append(name or f"${index}")
    return uses


def _read_body(function: _Function, body: str, first_line: int) -> None:
    """Writes, and how the body asks who the caller is. Shallow by design: no PL/pgSQL
    parser, only each INSERT, UPDATE and DELETE up to the next top-level `;`."""
    statements = split_statements(body)
    text = " ; ".join(_collapse(masked) for _, masked, _ in statements).lower()
    writes: list[Write] | None = None if _BODY_DYNAMIC.search(text) else []
    for _, masked, inner_line in statements if writes is not None else []:
        for verb, pattern in _WRITE_FORMS:
            for found in pattern.finditer(masked):
                written = found.group(1)
                table = _qualify(written) if "." in written else _unquote(written)
                at = first_line + inner_line - 1 + masked[:found.start()].count("\n")
                writes.append(Write(verb=verb, table=table,
                                    uses=_uses(masked[found.start():].lower(),
                                               function.params),
                                    line=at))
    if writes is not None:
        writes.sort(key=lambda write: write.line)
    function.writes = writes
    function.compares_caller, function.role_check = _caller(text, body.lower(),
                                                            function.params)


def _caller(text: str, raw: str, params: list[str]) -> tuple[list[str], bool]:
    """(parameters compared with the caller, whether a role check is made). A variable
    assigned auth.uid() stands for the caller. Testing that someone is signed in, or
    recording them as the actor, is neither."""
    aliases = set(re.findall(
        rf"\b([a-z_][\w$]*)(?:\s+[a-z_][\w$.]*(?:\s*\[\s*\])?)?\s*(?::=|\bdefault\b)\s*"
        rf"{UID_PATTERN}", text))
    aliases |= set(re.findall(rf"\bselect\s+{UID_PATTERN}\s+into\s+(?:strict\s+)?"
                              rf"([a-z_][\w$]*)", text))
    terms = [UID_PATTERN] + [rf"(?<![\w$.]){re.escape(a)}(?![\w$(])" for a in sorted(aliases)]
    caller = "(?:" + "|".join(terms) + ")"
    compared = []
    for index, name in enumerate(params, start=1):
        param = rf"(?:{_param_pattern(name)}|(?<![\w$])\${index}(?!\d))" if name \
            else rf"(?<![\w$])\${index}(?!\d)"
        if re.search(rf"{param}\s*{_COMPARE}\s*{caller}|{caller}\s*{_COMPARE}\s*{param}",
                     text):
            compared.append(name or f"${index}")
    role_check = bool(re.search(r'"?auth"?\s*\.\s*"?(?:role|jwt)"?\s*\(', text)
                      or re.search(r"current_setting\s*\(\s*'request\.jwt", raw))
    if not role_check:
        role_check = any(_inside_a_call(text, found.start())
                         for found in re.finditer(caller, text))
    return compared, role_check


def _inside_a_call(text: str, position: int) -> bool:
    """Whether `position` is an argument of a function call, such as has_role(...)."""
    depth = 0
    index = position - 1
    while index >= 0:
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            if depth == 0:
                break
            depth -= 1
        index -= 1
    if index < 0:
        return False
    word = re.search(r'([\w$."]+)\s*$', text[:index])
    if word is None:
        return False
    name = word.group(1).split(".")[-1].strip('"')
    return bool(name) and not name[0].isdigit() and name not in _NOT_A_CHECK
