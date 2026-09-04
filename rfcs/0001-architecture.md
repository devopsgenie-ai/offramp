---
rfc: 0001
title: "Architecture: detectors, AppSpec, renderers, gaps"
status: draft
authors: [ishantdeep-hue]
created: 2026-09-05
updated: 2026-09-05
---

> Establishes the core architecture: a three-stage pipeline that reads an application
> repository into an intermediate representation, renders declarative configuration from
> it, and reports everything it could not determine as typed gaps.

## Problem

An application built on a hosted app platform has no deployment definition. The platform
supplies the container build, the routing, the TLS, the environment variables and the
database, and none of it is expressed in the repository. The source code is portable; the
deployment is not.

A team that needs to leave — for cost, or because a compliance requirement says data must
live in a particular jurisdiction — has to reconstruct all of it by hand. In practice that
means writing a Dockerfile per service, working out which environment variables the app
actually reads, deciding how the database is going to run, writing Kubernetes manifests
and the GitOps wiring to deliver them, and discovering the gaps by watching pods fail.

It is roughly a week of undifferentiated work, it is done from scratch by every team that
does it, and the parts that are hard to get right — health probe paths, which env vars are
secret, what the database connection actually needs — are exactly the parts that produce
confusing failures at 2am rather than clear ones at build time.

There is no shortage of tools that deploy an app once it is already described. There is
very little that produces the description.

## Proposal

A three-stage pipeline with a typed intermediate representation in the middle.

```
repository ──▶ detectors ──▶ AppSpec ──▶ renderers ──▶ output tree
                                │
                                └──────▶ gaps ──▶ report + runbook
```

The shape is a compiler's: many front ends, one IR, many back ends. Adding a source
platform is a detector. Adding a deployment target is a renderer. The combinations do not
multiply.

### AppSpec

The IR. Everything detected, nothing rendered. It is a plain data structure, serialisable
to JSON, with a versioned schema.

```
AppSpec
  name          str
  source        { platform, repo_url, commit }
  services      [Service]
  datastores    [Datastore]
  routes        [Route]
  environments  [Environment]

Service
  name          str
  role          web | api | worker | cron
  runtime       { language, version }
  build         { context, dockerfile?, install_cmd, build_cmd?, start_cmd }
  ports         [int]
  health        { path, port, kind: http|tcp }
  env           [EnvVar]            # name + source: literal | secret | datastore
  resources     { requests, limits }
  replicas      int

Datastore
  kind          mongodb | postgres | redis | ...
  version       str?
  mode          in_cluster | managed | external
  consumed_by   [service_name]
  env_keys      [str]               # which env vars carry its connection details

Route
  host          str?
  path          str
  service       str
  port          int
  tls           bool

Environment
  name          str                 # local, staging, production
  replicas      {service: int}?
  resources     {service: ...}?
```

Two rules keep the boundaries honest, and both are enforced by review: **detectors never
write files, renderers never read the source repository.** Wanting to break either one
means the AppSpec is missing a field. Adding the field is the fix.

### Detectors

Each detector answers one narrow question and contributes facts. They compose; they do
not coordinate. A detector that cannot determine its answer emits a gap rather than a
default.

For the first scenario the set is: runtime and version, dependency manifest and install
command, service entrypoint and port, health endpoint, environment variable usage,
datastore identification, and static frontend build output.

### Renderers

Each renderer turns an AppSpec into a directory of files for one target. Renderers are
pure functions and their output is byte-stable.

The first target is Kubernetes delivered by GitOps, emitting the conventional
Kustomize layout — a `base/` holding the manifests and per-environment overlays that patch
image, resources and environment-specific values — plus the GitOps `ApplicationSet` that
points at each overlay, and a Dockerfile per service.

The layout is deliberately conventional rather than novel. The output should look like
something a competent platform engineer would have written by hand, because a human has to
review it, own it, and modify it for the next five years.

### Gaps

A gap is a typed record of something the tool could not determine:

```
Gap
  id            str                 # stable, e.g. "service.api.health.path"
  pointer       str                 # JSON pointer into the AppSpec
  question      str                 # asked in plain language
  proposed      any?                # a default, if we have a reasonable one
  confidence    high | medium | low
  severity      blocking | important | cosmetic
  evidence      [str]               # files and lines that informed the guess
```

Gaps are the product, not an admission of failure. They are emitted as JSON for machines
and rendered into the human runbook. A `blocking` gap means the output will not work until
a human answers.

Because gaps are typed and carry a pointer, an agent can resolve them conversationally and
write the answers back into the AppSpec, then re-render. This is how the tool composes
with a coding agent without embedding one: the deterministic core does the generation, the
agent supplies the judgement.

### Gap count is the quality metric

Every fixture run records how many gaps were produced, at what severity. Falling gap
counts on a fixed corpus is the definition of the tool improving, and it is measurable in
CI from the first commit. Feature counts and lines of code are not tracked.

### Surfaces

One core library with three thin adapters over it, in order of construction:

1. **Library** — `scan()`, `render()`, `gaps()`. Everything else calls this.
2. **CLI** — `offramp scan`, `offramp generate`, `offramp gaps`. What CI uses.
3. **MCP server** — the same operations as tools, so a coding agent can drive the pipeline
   and resolve gaps interactively.

The MCP server exposes no capability the CLI lacks. An agent is a convenience, never a
requirement.

### Scope of the first implementation

- **One scenario:** a full-stack app with a Python API, a static JavaScript frontend, and
  a document database — the common shape produced by current app-generation platforms.
- **One target:** Kubernetes via Kustomize and GitOps.
- **Greenfield only:** the user has no existing deployment repository, so the tool
  scaffolds one from an opinionated default. Reading an existing repository and matching
  its conventions is deliberately deferred; see Out of scope.
- **Python**, because the first scenario's applications are Python and the ecosystem
  overlap makes detectors easier to write and verify.

## Alternatives considered

**An agent with prompt-embedded templates.** Ship playbooks that instruct a coding agent
to write the manifests, with example YAML in the prompt. Far less code, and genuinely
better on unusual repositories, because the model can reason about things no detector
anticipated.

Rejected because the logic ends up living in prose. Nothing validates a template embedded
in a prompt, no test can assert on it, and there is no way to tell whether a change made
the tool better or worse. It also produces nothing reusable by anything other than an
agent. The gap mechanism above is an attempt to keep the flexibility this approach offers
while keeping the generation itself testable.

**Direct generation with no IR.** Read the repository and write files in one pass. Simpler
at first, and for a single scenario and a single target it is strictly less code.

Rejected because the second target reuses none of the first. With N sources and M targets
it is N×M implementations that drift; with the IR it is N+M. The cost is designing the
AppSpec well, and getting it wrong is expensive once several detectors depend on it —
which is why it is the substance of this RFC rather than an implementation detail.

**Deploying, not just generating.** Have the tool apply what it produces and iterate until
the app is healthy, which is what a user actually wants.

Rejected for the first implementation, and constrained permanently by `AGENTS.md`: a tool
that holds cloud credentials and mutates infrastructure is a different security proposition
and cannot be run casually against a repository you do not own. Verification against a
disposable local cluster is a much better version of this idea and is on the roadmap.

## Impact on generated output

None — there is no existing output. This RFC establishes the baseline that later RFCs will
be measured against.

## Testing

**Golden fixtures.** A corpus of sample applications lives in `fixtures/`, each with its
input tree and an `expected/` directory of generated output. Tests render each fixture and
diff against `expected/`. Determinism, per `AGENTS.md`, is what makes this possible.

**Validation of generated artifacts.** Golden files prove output is stable, not that it is
correct. Rendered manifests are additionally checked with `kustomize build` and
`kubeconform`, and generated Dockerfiles are built in CI. These are read-only checks that
touch no real infrastructure.

**Gap metrics.** Every fixture run records gap count by severity. A change that increases
blocking gaps on the corpus fails CI unless the RFC authorising it says why.

**Fixtures must be synthetic or explicitly licensed.** No customer repository, and no code
that we do not have the right to redistribute, enters the corpus.

## Declarative constraint

This proposal complies. Every renderer emits files. Nothing in the pipeline calls a cloud
API, requires credentials, or mutates any running system. The validation steps
(`kustomize build`, `kubeconform`, `docker build`) are read-only and operate on generated
output in a temporary directory.

Work that cannot be declarative — moving data, cutting over DNS, setting a secret value
for the first time — is written into the generated runbook as instructions for a human,
with the commands spelled out. The tool does not run them.

## Out of scope

- **Adopting an existing deployment repository.** Inferring conventions from a repository
  the user already has, and generating config that matches its dialect. This is the more
  valuable mode for established teams and is deliberately second: greenfield produces the
  reference implementation that convention-inference will be checked against.
- **Data migration.** Detecting the datastore and generating its deployment is in scope.
  Moving the contents is not; it is a runbook.
- **Additional targets** — Terraform, Nomad, Compose, managed container services.
- **Additional source platforms.**
- **Autonomous verification** against a live or ephemeral cluster.
- **Compliance evidence artifacts.**

Each is tracked in `ROADMAP.md` and will get its own RFC.

## Open questions

1. **AppSpec versioning.** Should the schema carry a version field from the first commit,
   with a documented compatibility policy, or is it acceptable to break it freely until
   the first tagged release? Committing early costs flexibility exactly when the shape is
   least certain.
2. **Environment modelling.** The draft treats environments as overlays over one service
   definition. Is a single environment (production) the honest scope for the first
   implementation, with staging and local following once the overlay mechanism has been
   exercised against real fixtures?
3. **Datastore default.** For a document database with no managed service selected, should
   the default rendering be an in-cluster StatefulSet — runnable immediately, but a
   database a novice now operates — or a blocking gap that refuses to guess?
