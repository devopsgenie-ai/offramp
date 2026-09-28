"""What the fix does for each row-level-security finding, as data. RFC-0003.

The rule behind every decision here: **a fix closes exactly the exposure its finding
names, preserves every other behaviour, and turns each remaining decision into a gap.**

  rls.disabled, owner           enable RLS; owner-scoped INSERT, UPDATE and DELETE `to
                                authenticated`; a SELECT `true` that keeps today's reads
  rls.disabled, server_only     enable RLS, no policy
  permissive_write (high),      drop the offending policy; an owner-scoped replacement
  owner                         for the same command, unless one already exists; for ALL,
                                the SELECT half kept as it was
  permissive_write (high),      drop the offending policy
  server_only
  no owner determined           nothing executable: an inert block and the gap
  permissive_write (medium),    nothing: a decision for a human, not a defect
  public_read

Enabling RLS also activates any policy already written on the table (Supabase Advisor
lint 0007). A `true` UPDATE, DELETE or ALL among them is part of the same exposure, so
it is dropped too. Any other policy it activates keeps doing what it says, and the audit
will ask about the public reads and anonymous inserts it raises: those are listed as
`expected_after`, and the round trip requires exactly them and nothing else.

Pure: reads the AppSpec and the audit document, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from checks.supabase import DISABLED, PERMISSIVE_WRITE, PUBLIC_READ
from detect.owners import undetermined_reason
from fix_sql import owner_expression
from rls import ANYONE, is_open_change, is_permissive_write, is_public_read, owner_column
from spec import AppSpec, Policy, Schema, Table, WriteScope, gap_id
from walk import dns_label

RLS_CHECKS = (DISABLED, PERMISSIVE_WRITE, PUBLIC_READ)
OWNED_COMMANDS = ("insert", "update", "delete")
PREFIX = "offramp: "
#: Who may read a table with RLS off today: every API role.
TODAYS_READERS = ("anon", "authenticated")

DECISION = {
    PUBLIC_READ: ("Anyone may read every row. That is often intended, a catalogue or public "
                  "profiles, so it is a decision for you, not something to fix blindly: "
                  "confirm the table holds nothing private."),
    PERMISSIVE_WRITE: ("Anyone may add rows. That is usually a form, a waitlist or a contact "
                       "form, so it is a decision for you: consider rate limits and what a "
                       "row may contain."),
}


@dataclass(frozen=True)
class NewPolicy:
    name: str
    command: str
    roles: tuple[str, ...]
    using: str | None
    with_check: str | None
    why: str                   # one line for the migration's comment
    kept_from: str | None = None   # the ALL policy whose read half this keeps


@dataclass(frozen=True)
class TableFix:
    table: Table
    scope: WriteScope
    fixed: tuple[str, ...]                 # finding ids this closes
    enable: bool
    drops: tuple[Policy, ...]
    creates: tuple[NewPolicy, ...]
    covered: tuple[tuple[str, str], ...]   # (command, existing owner policy relied on)
    expected_after: tuple[str, ...]        # findings the fix is expected to raise


@dataclass(frozen=True)
class Unfixed:
    table: Table
    findings: tuple[str, ...]
    gap: str
    reason: str
    open_policies: tuple[str, ...]         # the `true` policies a human might drop


@dataclass(frozen=True)
class Outcome:
    id: str
    check: str
    severity: str
    table: str
    policy: str | None
    status: str                # fixed | gap | not_in_scope
    gap: str | None
    reason: str


@dataclass(frozen=True)
class FixPlan:
    fixes: tuple[TableFix, ...] = ()
    unfixed: tuple[Unfixed, ...] = ()
    outcomes: tuple[Outcome, ...] = ()

    @property
    def expected_after(self) -> list[str]:
        return sorted({item for fix in self.fixes for item in fix.expected_after})


@dataclass
class _Draft:
    outcomes: list[Outcome] = field(default_factory=list)
    fixes: list[TableFix] = field(default_factory=list)
    unfixed: list[Unfixed] = field(default_factory=list)


def supabase_schema(appspec: AppSpec) -> Schema | None:
    store = next((d for d in appspec.datastores if d.name == "supabase"), None)
    return None if store is None else store.schema


def plan_fix(appspec: AppSpec, document: dict) -> FixPlan:
    findings = [f for f in document["findings"] if f["check"] in RLS_CHECKS]
    schema = supabase_schema(appspec)
    tables = {t.name: t for t in (schema.tables or [])} if schema else {}
    by_table: dict[str, list[dict]] = {}
    for finding in findings:
        by_table.setdefault(finding["subject"]["table"], []).append(finding)
    draft = _Draft()
    for name in sorted(by_table):
        _plan_table(draft, tables.get(name), name, sorted(by_table[name], key=lambda f: f["id"]),
                    schema)
    return FixPlan(fixes=tuple(draft.fixes), unfixed=tuple(draft.unfixed),
                   outcomes=tuple(sorted(draft.outcomes, key=lambda o: o.id)))


def _outcome(finding: dict, status: str, reason: str, gap: str | None = None) -> Outcome:
    return Outcome(id=finding["id"], check=finding["check"], severity=finding["severity"],
                   table=finding["subject"]["table"], policy=finding["subject"].get("policy"),
                   status=status, gap=gap, reason=reason)


def _plan_table(draft: _Draft, table: Table | None, name: str, findings: list[dict],
                schema: Schema | None) -> None:
    actionable = []
    for finding in findings:
        if finding["check"] == PUBLIC_READ or finding["severity"] == "medium":
            draft.outcomes.append(_outcome(finding, "not_in_scope", DECISION[finding["check"]]))
        else:
            actionable.append(finding)
    if not actionable:
        return
    if table is None:
        for finding in actionable:
            draft.outcomes.append(_outcome(
                finding, "not_in_scope",
                "The table is not in the AppSpec's schema, so nothing can be rendered for it. "
                "This happens when Supabase is declared but not imported (see the gap "
                "datastore.supabase.disputed)."))
        return
    if table.write_scope is None:
        gap = gap_id("datastore", "supabase", "table", name, "write_scope")
        reason = undetermined_reason(table, schema)
        for finding in actionable:
            draft.outcomes.append(_outcome(finding, "gap", reason, gap))
        draft.unfixed.append(Unfixed(
            table=table, findings=tuple(f["id"] for f in actionable), gap=gap, reason=reason,
            open_policies=tuple(f["subject"]["policy"] for f in actionable
                                if f["subject"].get("policy"))))
        return
    fix, failure = _fix(table, table.write_scope, actionable)
    for finding in actionable:
        draft.outcomes.append(_outcome(finding, "fixed", "") if fix
                              else _outcome(finding, "not_in_scope", failure))
    if fix:
        draft.fixes.append(fix)


def _fix(table: Table, scope: WriteScope, findings: list[dict]):
    """(TableFix, None), or (None, why the table cannot be fixed as the rule demands)."""
    enable = any(f["check"] == DISABLED for f in findings)
    policies = {p.name: p for p in table.policies}
    offenders = [policies[f["subject"]["policy"]] for f in findings
                 if f["check"] == PERMISSIVE_WRITE]
    if enable:
        offenders += [p for p in table.policies if is_open_change(p) and p not in offenders]
    offenders.sort(key=lambda p: p.name)
    remaining = [p for p in table.policies if p not in offenders]
    taken = {p.name for p in remaining}
    creates: list[NewPolicy] = []
    covered: list[tuple[str, str]] = []

    def add(base: str, **fields) -> NewPolicy:
        name, number = PREFIX + base, 2
        while name in taken:
            name, number = f"{PREFIX}{base} ({number})", number + 1
        taken.add(name)
        policy = NewPolicy(name=name, **fields)
        creates.append(policy)
        return policy

    if scope.kind == "owner":
        needed = set(OWNED_COMMANDS) if enable else {
            command for p in offenders for command in OWNED_COMMANDS
            if p.command in (command, "all")}
        for command in OWNED_COMMANDS:
            if command not in needed:
                continue
            existing = next((p.name for p in remaining
                             if _covers(p, command, scope.column, table.name)), None)
            if existing:
                covered.append((command, existing))
                continue
            expression = owner_expression(scope.column)
            add(f"owner can {command}", command=command, roles=("authenticated",),
                using=None if command == "insert" else expression,
                with_check=None if command == "delete" else expression,
                why=f"only the row's owner can {command}")
        for offender in offenders:
            if offender.command != "all" or offender.using is None:
                continue
            if not _reproducible(offender.using):
                return None, (
                    f'The read half of "{offender.name}" uses a condition the replay does '
                    f"not keep exactly (a string literal or quoted name), so it cannot be "
                    f"preserved. Replacing the policy would change who can read the table.")
            if any(_reads_as(p, offender) for p in remaining + list(creates)):
                continue
            add(_reader_name(offender.roles), command="select", roles=tuple(offender.roles),
                using=offender.using, with_check=None,
                why=f'keeps the read half of "{offender.name}" as it was',
                kept_from=offender.name)
        if enable and not any(is_public_read(p) for p in remaining):
            add("anyone can read", command="select", roles=TODAYS_READERS, using="true",
                with_check=None, why="keeps today's reads: with RLS off, anyone could read")

    expected = []
    for policy in (remaining if enable else []):
        if is_public_read(policy):
            expected.append(f"{PUBLIC_READ}.{table.name}.{dns_label(policy.name)}")
        elif is_permissive_write(policy):
            expected.append(f"{PERMISSIVE_WRITE}.{table.name}.{dns_label(policy.name)}")
    for policy in creates:
        if (policy.command == "select" and policy.using == "true"
                and any(role in ANYONE for role in policy.roles)):
            expected.append(f"{PUBLIC_READ}.{table.name}.{dns_label(policy.name)}")
    return TableFix(table=table, scope=scope, fixed=tuple(f["id"] for f in findings),
                    enable=enable, drops=tuple(offenders), creates=tuple(creates),
                    covered=tuple(covered), expected_after=tuple(sorted(expected))), None


def _covers(policy: Policy, command: str, column: str, table: str) -> bool:
    """An existing permissive policy that already scopes `command` to the owner."""
    if not policy.permissive or policy.command not in (command, "all"):
        return False
    if not {"authenticated", "public"} & set(policy.roles):
        return False
    check = policy.with_check if policy.with_check is not None else policy.using
    using_ok = owner_column(policy.using, table) == column
    check_ok = owner_column(check, table) == column
    return {"insert": check_ok, "update": using_ok and check_ok, "delete": using_ok}[command]


def _reproducible(expression: str) -> bool:
    """The replay masks string literals and dollar bodies and lower-cases expressions.
    Only an expression with none of those can be written back unchanged."""
    return expression == "true" or not any(mark in expression for mark in ("''", '"', "$$"))


def _reads_as(policy, offender: Policy) -> bool:
    return (policy.command == "select" and policy.using == offender.using
            and ("public" in policy.roles or set(offender.roles) <= set(policy.roles)))


def _reader_name(roles: tuple[str, ...]) -> str:
    if any(role in ANYONE for role in roles):
        return "anyone can read"
    if tuple(roles) == ("authenticated",):
        return "signed-in users can read"
    return "kept read"
