"""The AppSpec: offramp's intermediate representation, plus gap identity.

Detectors contribute facts here; renderers consume it. Nothing in this module reads
the filesystem, the clock, the environment or the network -- AGENTS.md §3.

Field-level notes record why a field exists where RFC-0001 argues for it, because
several of them look redundant and are not.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

GAP_KINDS = ("value", "action")
GAP_SEVERITIES = ("blocking", "important", "cosmetic")
GAP_CONFIDENCE = ("high", "medium", "low")
GAP_ORIGINS = ("detector", "model", "stale_answer", "orphaned_answer")
ENV_SOURCES = ("literal", "datastore", "platform")
ENV_BINDINGS = ("runtime", "build_arg")
DATASTORE_MODES = ("in_cluster", "managed", "external")
PROBE_ROLES = ("liveness", "readiness", "startup")
PROBE_KINDS = ("http", "tcp")
WRITE_SCOPE_KINDS = ("owner", "server_only")
SERVICE_ROLES = ("web", "api", "worker", "cron")

#: Report order. Blocking first, because a blocking gap means the output will not work.
SEVERITY_ORDER = GAP_SEVERITIES


@dataclass
class Evidence:
    """One cited line. Paths are relative to the scan root and POSIX-separated:
    an absolute path would make the AppSpec depend on where the repo was cloned."""

    path: str
    line: int


@dataclass
class Source:
    """Provenance. Never answerable -- see gaps.NEVER_ANSWERABLE."""

    platform: str
    repo_url: str | None
    commit: str | None


@dataclass
class Runtime:
    language: str
    version: str | None


@dataclass
class Build:
    context: str
    dockerfile: str | None
    install_cmd: str
    build_cmd: str | None = None
    start_cmd: str | None = None
    output_dir: str | None = None


@dataclass
class EnvVar:
    """`source` is provenance; `sensitive` is the security axis. One enum cannot say
    "comes from the datastore" and "is a credential" at once -- MONGO_URL is both, and
    a guard keyed on source would miss the one value it most needs to protect."""

    name: str
    source: str            # literal | datastore | platform
    binding: str           # runtime | build_arg
    sensitive: bool
    value: str | None = None   # never a secret value


@dataclass
class Probe:
    role: str              # liveness | readiness | startup
    path: str
    port: int
    kind: str              # http | tcp


@dataclass
class Resources:
    requests: dict[str, str]
    limits: dict[str, str]


@dataclass
class Service:
    name: str              # from the build context directory; never answerable
    role: str              # web | api | worker | cron
    runtime: Runtime
    build: Build
    ports: list[int]       # empty if the renderer owns the port
    probes: list[Probe]    # liveness | readiness | startup; not one "health"
    env: list[EnvVar]
    resources: Resources
    replicas: int


@dataclass
class Column:
    """RFC-0003. `default` and `primary_key` go beyond the RFC's sketch: the fix has to
    say whether the app must set the owner on insert, and the one-hop owner rule needs
    to know which column is a table's key."""

    name: str
    nullable: bool
    references: str | None     # "auth.users.id", "public.profiles.id"
    default: str | None        # normalised expression, e.g. "auth.uid()"
    primary_key: bool


@dataclass
class Policy:
    name: str
    command: str               # all | select | insert | update | delete
    roles: list[str]           # "public" when the policy has no TO clause
    using: str | None          # normalised expression, e.g. "true"
    with_check: str | None
    permissive: bool
    evidence: str              # path:line of the CREATE POLICY
    #: Columns renamed after this policy was created, old and new names. PostgreSQL
    #: rewrites a stored policy on rename; the replay keeps the text as written, so a
    #: policy with renames since it was written is not evidence of anything by name.
    renamed_since: list[str] = field(default_factory=list)


@dataclass
class WriteScope:
    """Who may change a table's rows. RFC-0003. Answerable.

    `basis` goes beyond the RFC's sketch: the renderer's comment has to say *why* a
    column is the owner, and a renderer may not re-derive it from the repository.
    """

    kind: str                  # owner | server_only
    column: str | None         # required for owner; names one of the table's columns
    basis: list[str] = field(default_factory=list)   # the evidence, in words


@dataclass
class Table:
    name: str                  # "public.tasks". Identity; never answerable
    rls: bool | None           # None: altered in these migrations, created elsewhere
    evidence: str | None       # where it was created; None when created elsewhere
    #: None when a statement changing columns was not understood, or the table was
    #: created elsewhere. Tracked apart from `rls`: an unparseable column change must
    #: not cost the RLS checks their answer.
    columns: list[Column] | None
    policies: list[Policy]     # sorted by name
    write_scope: WriteScope | None = None   # answerable. None = not determined


@dataclass
class Schema:
    """The replay of `supabase/migrations/`, as a detector. RFC-0003: one replay with
    two consumers, the audit's RLS checks and the fix renderer, so they can never
    disagree about the schema."""

    migrations: list[str]      # replayed, in run order
    unreplayable: list[str]    # "path:line: reason". Non-empty means tables is None
    unordered: list[str]       # no leading version; order unknown, so tables is None
    latest: str | None         # highest migration version seen
    tables: list[Table] | None


@dataclass
class Datastore:
    name: str              # identity; kind alone collides
    kind: str
    version: str | None
    mode: str | None       # in_cluster | managed | external. Never defaulted.
    consumed_by: list[str]
    env_keys: list[str]
    #: A postgres datastore whose migrations are in the repository. None otherwise.
    schema: Schema | None = None


@dataclass
class Route:
    host: str | None
    path: str
    service: str
    port: int | None       # null = wire to the Service's named port
    tls: bool


@dataclass
class Environment:
    name: str
    namespace: str | None = None
    replicas: dict[str, int] | None = None
    resources: dict[str, Any] | None = None


@dataclass
class Delivery:
    """Destination inputs. None of these is a property of the application and none can
    be detected -- but a field that cannot be pointed at cannot be a well-formed gap and
    cannot be answered, so they live in the AppSpec."""

    image_registry: str | None
    image_tag: str | None
    gitops_repo_url: str | None
    ingress_class: str | None
    target_cluster: str


@dataclass
class AppSpec:
    name: str | None       # git remote, or a blocking gap. Never the checkout directory.
    source: Source
    services: list[Service]
    datastores: list[Datastore]
    routes: list[Route]
    environments: list[Environment]
    delivery: Delivery


@dataclass
class Gap:
    id: str
    kind: str              # value | action
    pointers: list[str]    # zero-to-many; recomputed every scan, never persisted
    question: str
    proposed: Any
    confidence: str        # high | medium | low
    severity: str          # blocking | important | cosmetic
    evidence: list[str]
    origin: str = "detector"
    depends_on: list[str] = field(default_factory=list)
    evidence_scope: list[str] = field(default_factory=list)


def gap_id(*parts: str) -> str:
    """Gap identity, from names alone.

    Renderers are pure functions of the AppSpec and are also asked to mark unresolved
    gaps in the output tree. Gaps are not in the AppSpec, so a renderer can only name
    one if the id is reconstructible from names it already has.
    """
    return ".".join(parts)


def evidence_text(items: list[Evidence]) -> list[str]:
    return [f"{item.path}:{item.line}" for item in items]


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Byte-stable JSON. Sorted keys, fixed indent, trailing newline."""
    return json.dumps(to_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check_enums(appspec: AppSpec, gaps: list[Gap]) -> list[str]:
    """Catch a typo'd enum value, and make the enums above the single place they are
    written down. Not a full schema check -- that arrives with the plan model."""
    problems: list[str] = []
    for index, gap in enumerate(gaps):
        for field_name, allowed in (
            ("kind", GAP_KINDS),
            ("severity", GAP_SEVERITIES),
            ("confidence", GAP_CONFIDENCE),
            ("origin", GAP_ORIGINS),
        ):
            value = getattr(gap, field_name)
            if value not in allowed:
                problems.append(
                    f"gaps[{index}] {gap.id}: {field_name}={value!r} not in {allowed}"
                )
    for service in appspec.services:
        if service.role not in SERVICE_ROLES:
            problems.append(f"{service.name}: role={service.role!r} not in {SERVICE_ROLES}")
        for var in service.env:
            if var.source not in ENV_SOURCES:
                problems.append(f"{service.name}.{var.name}: source={var.source!r}")
            if var.binding not in ENV_BINDINGS:
                problems.append(f"{service.name}.{var.name}: binding={var.binding!r}")
            if var.binding == "build_arg" and var.sensitive:
                # A build arg is compiled into a file served to every browser, so it is
                # public by construction and can never be sensitive.
                problems.append(f"{service.name}.{var.name}: build_arg marked sensitive")
        for probe in service.probes:
            if probe.role not in PROBE_ROLES:
                problems.append(f"{service.name}: probe role={probe.role!r}")
            if probe.kind not in PROBE_KINDS:
                problems.append(f"{service.name}: probe kind={probe.kind!r}")
    for datastore in appspec.datastores:
        if datastore.mode is not None and datastore.mode not in DATASTORE_MODES:
            problems.append(f"datastore {datastore.name}: mode={datastore.mode!r}")
        for table in (datastore.schema.tables or []) if datastore.schema else []:
            scope = table.write_scope
            if scope is None:
                continue
            if scope.kind not in WRITE_SCOPE_KINDS:
                problems.append(f"table {table.name}: write_scope.kind={scope.kind!r}")
            names = {column.name for column in table.columns or []}
            if scope.kind == "owner" and scope.column not in names:
                problems.append(f"table {table.name}: owner {scope.column!r} is not a column")
    return problems
