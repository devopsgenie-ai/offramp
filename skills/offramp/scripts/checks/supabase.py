"""The three row-level-security checks. RFC-0002.

In the modal Lovable application the Supabase anon key is written into the frontend as
a literal. That is by design: the key is public, and the database's row-level security
is the only access control the application has. CVE-2025-48757 is the public record of
that control failing, in 10.3% of the applications scanned.

All three checks read one replay of `supabase/migrations/`: the `Schema` that `scan`
produced (RFC-0003), never a replay of their own, so the audit and the fix renderer can
never disagree about the schema. The finding builders below are pure functions of that
`Schema`, which is what lets the fix renderer re-run them over its own output. They share
the replay's limits, and say so rather than report `clean`:

  no migrations    the tables were created in the dashboard; nothing here can see them
  unparseable      one statement the replay did not understand makes every answer
                   partial, so none is given

Only tables in the `public` schema are reported: it is the schema Supabase exposes
through its API by default.
"""

from __future__ import annotations

from pathlib import Path

from spec import Policy, Schema
from findings import Assessment, Finding
from walk import dns_label

DISABLED = "supabase.rls.disabled"
PERMISSIVE_WRITE = "supabase.rls.permissive_write"
PUBLIC_READ = "supabase.rls.public_read"

_WRITE_COMMANDS = ("all", "insert", "update", "delete")
_ANYONE = ("anon", "public")
_SIGNED_IN = ("authenticated",)


def _uses_supabase(root: Path, scan) -> bool:
    return (any(store.name == "supabase" for store in scan.appspec.datastores)
            or (root / "supabase").is_dir())


def _precondition(check: str, root: Path, scan) -> Assessment | None:
    if not _uses_supabase(root, scan):
        return Assessment(check=check, status="not_applicable",
                          reason="the application does not use Supabase")
    schema = scan.schema
    if schema is not None and schema.unordered:
        return Assessment(
            check=check, status="could_not_assess",
            reason=("migrations are not all in Supabase's <timestamp>_name.sql format, so "
                    "the order they ran in is unknown: " + schema.unordered[0]),
        )
    if schema is None or not schema.migrations:
        return Assessment(
            check=check, status="could_not_assess",
            reason=("the application uses Supabase, but the repository has no migrations "
                    "in supabase/migrations/, so its tables and policies were created "
                    "outside the repository"),
        )
    if schema.unreplayable:
        return Assessment(
            check=check, status="could_not_assess",
            reason=("a migration could not be replayed, so any answer would be partial: "
                    + schema.unreplayable[0]),
        )
    return None


def _public_tables(schema: Schema):
    return [table for table in schema.tables if table.name.startswith("public.")]


def _anyone(policy: Policy) -> bool:
    return any(role in _ANYONE for role in policy.roles)


def check_rls_disabled(root: Path, scan) -> tuple[list[Finding], Assessment]:
    if (early := _precondition(DISABLED, root, scan)) is not None:
        return [], early
    findings = rls_disabled_findings(scan.schema)
    return findings, Assessment(check=DISABLED, status="found" if findings else "clean")


def rls_disabled_findings(schema: Schema) -> list[Finding]:
    findings = []
    for table in _public_tables(schema):
        if table.rls is not False or table.evidence is None:
            continue
        findings.append(Finding(
            id=f"{DISABLED}.{table.name}",
            check=DISABLED,
            category="security",
            severity="critical",
            title=f"Table `{table.name}` has no row-level security",
            detail=(
                f"`{table.name}` is created in the migrations and row-level security is "
                f"never enabled on it. Supabase exposes tables in `public` through its "
                f"API, and the key that reaches that API is in every visitor's browser. "
                f"Anyone can read, change and delete every row."
            ),
            remedy=(
                "Enable row-level security on the table and add policies scoped to the "
                "row's owner. Until then, treat the data in it as exposed."
            ),
            evidence=[table.evidence],
        ))
    return findings


def _policy_findings(schema: Schema, predicate, build) -> list[Finding]:
    findings = []
    for table in _public_tables(schema):
        if table.rls is False:
            continue  # policies do not apply; rls.disabled reports the table instead
        for policy in table.policies:
            if policy.permissive and predicate(policy):
                findings.append(build(table.name, policy))
    return findings


def _policy_check(check: str, root: Path, scan, findings_of) -> tuple[list[Finding], Assessment]:
    if (early := _precondition(check, root, scan)) is not None:
        return [], early
    findings = findings_of(scan.schema)
    return findings, Assessment(check=check, status="found" if findings else "clean")


def _is_permissive_write(policy: Policy) -> bool:
    return (policy.command in _WRITE_COMMANDS
            and any(role in _ANYONE + _SIGNED_IN for role in policy.roles)
            and "true" in (policy.using, policy.with_check))


def _is_public_read(policy: Policy) -> bool:
    return (policy.command == "select"
            and any(role in _ANYONE for role in policy.roles)
            and policy.using == "true")


def check_rls_permissive_write(root: Path, scan) -> tuple[list[Finding], Assessment]:
    return _policy_check(PERMISSIVE_WRITE, root, scan, permissive_write_findings)


def permissive_write_findings(schema: Schema) -> list[Finding]:
    def build(table: str, policy: Policy) -> Finding:
        verb = "change" if policy.command != "insert" else "add"
        command = "every operation" if policy.command == "all" else policy.command.upper()
        return Finding(
            id=f"{PERMISSIVE_WRITE}.{table}.{dns_label(policy.name)}",
            check=PERMISSIVE_WRITE,
            category="security",
            # An anonymous INSERT with `true` is usually a contact or waitlist form: worth
            # asking about (spam, oversized rows), not the same risk as rewriting rows.
            severity="medium" if policy.command == "insert" else "high",
            title=(f"Policy \"{policy.name}\" lets "
                   f"{'anyone' if _anyone(policy) else 'any signed-in user'} "
                   f"{verb} rows in `{table}`"),
            detail=(
                f"The policy \"{policy.name}\" on `{table}` allows {command} for "
                + ("anyone, without signing in. " if _anyone(policy) else
                   "any signed-in user. If sign-up is open, that is anyone. ")
                + "Its condition is simply `true`, so it never checks whose row it is."
            ),
            remedy=(
                "Replace `true` with a condition on the row's owner, for example "
                "`auth.uid() = user_id`, or remove the policy if nobody should do this."
            ),
            evidence=[policy.evidence],
        )
    return _policy_findings(schema, _is_permissive_write, build)


def check_rls_public_read(root: Path, scan) -> tuple[list[Finding], Assessment]:
    return _policy_check(PUBLIC_READ, root, scan, public_read_findings)


def public_read_findings(schema: Schema) -> list[Finding]:
    def build(table: str, policy: Policy) -> Finding:
        return Finding(
            id=f"{PUBLIC_READ}.{table}.{dns_label(policy.name)}",
            check=PUBLIC_READ,
            category="security",
            severity="medium",
            title=f"Every row in `{table}` is readable without signing in",
            detail=(
                f"The policy \"{policy.name}\" lets anyone read every row of `{table}`. "
                f"That can be intended -- a public catalogue, published posts -- but it "
                f"also applies to every column, including any added later."
            ),
            remedy=(
                "Confirm the table holds nothing private. If it does, restrict the "
                "policy, or move the private columns to a table with its own policies."
            ),
            evidence=[policy.evidence],
        )
    return _policy_findings(schema, _is_public_read, build)
