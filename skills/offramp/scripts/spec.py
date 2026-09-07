"""AppSpec, Gap, Evidence, and canonical serialisation.

SPIKE, revision 2. Revision 1 implemented RFC-0001's AppSpec as literally as it
could and recorded where it broke. This revision applies the fixes, so the shape
below is a *counter-proposal* to the RFC rather than a transcription of it.

Every departure carries a `# RFC-0001: ...` note naming the finding in SPIKE.md.
The table in SPIKE.md is the summary; these notes are the detail.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

SCHEMA_NOTE = "spike rev-2: RFC-0001 AppSpec with the SPIKE.md findings applied"

GAP_KINDS = ("value", "action")
GAP_SEVERITIES = ("blocking", "important", "cosmetic")
GAP_CONFIDENCE = ("high", "medium", "low")
GAP_ORIGINS = ("detector", "model", "stale_answer", "orphaned_answer")
ENV_SOURCES = ("literal", "datastore", "platform")
ENV_BINDINGS = ("runtime", "build_arg")
DATASTORE_MODES = ("in_cluster", "managed", "external")
PROBE_ROLES = ("liveness", "readiness", "startup")


@dataclass
class Evidence:
    """A cited line, with the hash the answers file needs.

    RFC-0001 #11: `Answer.evidence` is [{path, lines, sha256}] and staleness is
    checked by re-hashing every cited range -- but the AppSpec had nowhere to
    record a detector's evidence for a fact, so there was nothing to hash. This is
    that record. Hashing the cited line rather than the file is deliberate: hashing
    backend/server.py would invalidate every answer citing it on every commit.
    """

    path: str
    line: int
    sha256: str


@dataclass
class Source:
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
    build_cmd: str | None
    # RFC-0001 #3: typed `str` and required. A service compiled to static assets has
    # no start command of its own -- the renderer supplies the web server.
    start_cmd: str | None
    # RFC-0001 #6, revised. Revision 1 retyped this to a mapping, because [str]
    # cannot hold a value. Answering a build arg then set EnvVar.value and left
    # Build.args holding a stale null: the same fact in two places, and the
    # renderer read the stale one. The field is gone. A build argument is exactly
    # "an EnvVar with binding: build_arg", and the renderer derives it. RFC-0001
    # says binding "routes the value into build.args and the Dockerfile"; the
    # first half of that is what creates the second source of truth. See #29.
    # RFC-0001 #2: the RFC names a "static frontend build output" detector and gives
    # it nowhere to write its answer.
    output_dir: str | None = None


@dataclass
class EnvVar:
    """RFC-0001 #4: `source` was one enum doing two orthogonal jobs.

    MONGO_URL is datastore-sourced *and* a credential. Under the RFC's
    `literal | secret | datastore` you can record one or the other, never both --
    and `apply`'s rule "reject an entry targeting an EnvVar whose source is secret"
    then never fires for the connection string, which is the single value it most
    needs to protect.

    So `source` is provenance only, and `sensitive` is the security axis. The rule
    in `apply` and the renderer's choice of secretKeyRef both key on `sensitive`.
    """

    name: str
    source: str            # literal | datastore | platform   (provenance)
    binding: str           # runtime | build_arg
    sensitive: bool        # the axis apply's guard must key on
    # RFC-0001 #1: EnvVar had no field for a value, so a literal could not be
    # rendered into a ConfigMap and `binding: build_arg` had no value to carry.
    value: str | None = None


@dataclass
class Probe:
    """RFC-0001 #24: `Service.health` was a single object.

    Kubernetes wants liveness and readiness, and a slow-starting Python service
    wants a startup probe too. A readiness check that differs from the liveness one
    is common and correct, and the single-object shape cannot express it.
    """

    role: str              # liveness | readiness | startup
    path: str
    port: int
    kind: str              # http | tcp
    initial_delay_seconds: int
    period_seconds: int


@dataclass
class Resources:
    requests: dict[str, str]
    limits: dict[str, str]


@dataclass
class Service:
    name: str
    role: str              # web | api | worker | cron
    runtime: Runtime
    build: Build
    # RFC-0001 #3: empty for a service compiled to static assets. The port a
    # detector can find in the source is the dev server's (3000), which is wrong
    # for the container that ships; the real port belongs to the renderer that
    # chooses the web server. Empty here means "the renderer decides", and the
    # renderer must emit a gap when it does.
    ports: list[int]
    probes: list[Probe]
    env: list[EnvVar]
    resources: Resources
    replicas: int


@dataclass
class Datastore:
    # RFC-0001 #25: the RFC keys a datastore on `kind` alone, so two Postgres
    # instances collide and the generated Secret name has no stable source.
    name: str
    kind: str
    version: str | None
    mode: str | None       # in_cluster | managed | external. Never defaulted.
    consumed_by: list[str]
    env_keys: list[str]


@dataclass
class Route:
    host: str | None
    path: str
    service: str
    # RFC-0001 #3: typed `int`. Null means "wire the Ingress to the Service's named
    # port", which is valid Kubernetes and removes the detector's need to know a
    # number the renderer owns.
    port: int | None
    tls: bool


@dataclass
class Environment:
    name: str
    replicas: dict[str, int] | None
    resources: dict[str, Any] | None
    # RFC-0001 #5: every Kustomize overlay needs a namespace and the RFC's AppSpec
    # cannot hold one.
    namespace: str | None = None


@dataclass
class Delivery:
    """RFC-0001 #5, in its entirety.

    The overlay needs a registry, the ApplicationSet needs the URL of the
    *deployment* repository and a destination cluster, and the Ingress needs a
    class. None is a property of the application and none can be detected.
    `AppSpec.source.repo_url` is provenance for the source repository and is
    declared never answerable, so it is the wrong field twice over.

    This is worse than a missing field: `Gap.pointer` addresses the AppSpec, so a
    thing with no field cannot be pointed at, cannot be a well-formed gap, and
    cannot be answered. Four of the six blocking gaps in revision 1 were in this
    position.
    """

    image_registry: str | None
    image_tag: str | None
    gitops_repo_url: str | None
    ingress_class: str | None
    target_cluster: str


@dataclass
class AppSpec:
    # RFC-0001 #17: the RFC fixes Service.name's derivation in the document and
    # calls changing it breaking, but says nothing about AppSpec.name -- which is
    # baked into every label, the namespace, the Ingress, the ApplicationSet and the
    # Secret the backend reads its connection string from. Deriving it from the
    # filesystem made 10 of 15 output files depend on the clone directory name.
    # Null means "no stable source found"; it is then a blocking gap, never a guess.
    name: str | None
    source: Source
    services: list[Service]
    datastores: list[Datastore]
    routes: list[Route]
    environments: list[Environment]
    delivery: Delivery


@dataclass
class Gap:
    id: str
    # RFC-0001 #20: several real gaps are not answered by supplying a value at a
    # pointer -- rotate this credential, commit a lock file, freeze your transitive
    # dependencies. `Answer {target, value, ...}` cannot represent "I did the
    # thing", so under the RFC those gaps can never be resolved and reappear on
    # every run forever. `action` gaps are acknowledged, not valued.
    kind: str              # value | action
    # RFC-0001 #8: `pointer` was one string. Two gaps have no pointer at all (the
    # credential value must never enter the AppSpec; the unused .env key is
    # deliberately absent from it) and route.host is one question whose answer sets
    # N fields. Gap-to-field is zero-to-many.
    pointers: list[str]
    question: str
    proposed: Any
    confidence: str        # high | medium | low
    severity: str          # blocking | important | cosmetic
    evidence: list[str]
    # RFC-0001 #12: the RFC's Gap block lists detector|model|stale_answer; its
    # answers-file section then requires a fourth, orphaned_answer, for an answer
    # whose target no longer exists in this run's AppSpec. That case defeats the
    # staleness test rather than failing it -- detected is null, the detector still
    # produces nothing, the cited files still hash -- so it needs its own origin or
    # it is silently dropped. GAP_ORIGINS is the enum the schema check keys on.
    origin: str = "detector"
    # RFC-0001 #21: gap dependencies. datastore.version only matters if mode is
    # answered in_cluster; the frontend's API base depends on the hostname. A flat
    # list asks questions that may turn out to be moot.
    depends_on: list[str] = field(default_factory=list)
    # RFC-0001 #34: path prefixes within which a citation is load-bearing for this
    # question. A plan may cite anything it likes -- reasoning across the repository
    # is what a model is for -- but only citations inside this scope are hashed into
    # the answer and become staleness triggers. Everything else is carried as
    # context: shown to the reviewer, never a reason to re-ask.
    #
    # Empty means nothing is load-bearing. That is the honest answer for a question
    # the repository cannot speak to at all -- which registry, which hostname -- and
    # it is not the same as "cite nothing": the plan that answers `app.name` reasons
    # from the application code, and a reviewer should see that.
    evidence_scope: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gap identity
#
# RFC-0001 #7: renderers are pure functions of the AppSpec, and are also asked to
# leave "unresolved gaps marked in" the output. Gaps are not in the AppSpec, so a
# renderer can only name one if the id is reconstructible from names alone. That
# is a real constraint on the id scheme and the RFC does not state it. Both layers
# call this, and spike_check.sh asserts every id named in the tree exists in
# gaps.json.
# ---------------------------------------------------------------------------
def gap_id(*parts: str) -> str:
    return ".".join(parts)


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def from_jsonable(data: dict) -> AppSpec:
    """The one deserialiser. #28.

    The AppSpec is "a plain data structure, serialisable to JSON" in RFC-0001, and
    a tree of dataclasses here. That is a fine choice until something writes a JSON
    value into the middle of the tree -- which is exactly what merging an answer
    does, since the answers file is JSON. The result is a spec that is part
    dataclass and part dict, and then every consumer breaks differently:
    compare-and-swap sees a mismatch that is not one, the staleness rule reports
    the detector disagreeing when it has not, and an attribute access raises.

    So a merge round-trips through here, and there is exactly one place that knows
    how to turn the JSON form back into the typed form.
    """
    return AppSpec(
        name=data["name"],
        source=Source(**data["source"]),
        services=[
            Service(
                name=item["name"],
                role=item["role"],
                runtime=Runtime(**item["runtime"]),
                build=Build(**item["build"]),
                ports=list(item["ports"]),
                probes=[Probe(**probe) for probe in item["probes"]],
                env=[EnvVar(**var) for var in item["env"]],
                resources=Resources(**item["resources"]),
                replicas=item["replicas"],
            )
            for item in data["services"]
        ],
        datastores=[Datastore(**item) for item in data["datastores"]],
        routes=[Route(**item) for item in data["routes"]],
        environments=[Environment(**item) for item in data["environments"]],
        delivery=Delivery(**data["delivery"]),
    )


def check_enums(appspec: AppSpec, gaps: list[Gap]) -> list[str]:
    """Cheap stand-in for the schema check RFC-0001 assumes exists.

    RFC-0001 leans on schema validation in several places -- rejecting a plan whose
    target is outside the answerable set, rejecting a detector that declares an
    excluded field answerable -- without one existing yet. This is not that schema;
    it is enough to catch a typo'd enum value, and enough to make the enums above
    the single place they are written down.
    """
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
                problems.append(f"gaps[{index}] {gap.id}: {field_name}={value!r} not in {allowed}")
    for service in appspec.services:
        for var in service.env:
            if var.source not in ENV_SOURCES:
                problems.append(f"{service.name}.{var.name}: source={var.source!r}")
            if var.binding not in ENV_BINDINGS:
                problems.append(f"{service.name}.{var.name}: binding={var.binding!r}")
        for probe in service.probes:
            if probe.role not in PROBE_ROLES:
                problems.append(f"{service.name}: probe role={probe.role!r}")
    for datastore in appspec.datastores:
        if datastore.mode is not None and datastore.mode not in DATASTORE_MODES:
            problems.append(f"datastore {datastore.name}: mode={datastore.mode!r}")
    return problems


def canonical_json(value: Any) -> str:
    """Byte-stable JSON. Sorted keys, fixed separators, trailing newline."""
    return json.dumps(to_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
