---
rfc: 0001
title: "Architecture: detectors, AppSpec, plan and apply, renderers, gaps"
status: draft
authors: [ishantdeep-hue]
created: 2026-09-05
updated: 2026-09-05
---

> Establishes the core architecture: deterministic scripts that read an application
> repository into an intermediate representation and render declarative configuration from
> it, a typed gap for everything they could not determine, and a plan/apply cycle that is
> the only path by which a model may influence the output.

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

### Two ways a generator can be wrong

Anything that reconstructs a deployment definition by reading code fails in two distinct
ways, and they need different machinery.

**It does not know.** The repository does not say which port the API listens on, or
whether the database should run in-cluster. This is the tractable case: the tool can
report it.

**It is confident and wrong.** Static analysis fails silently. A health detector matches
`@router.get("/health")` and records the probe path at high confidence, but the router is
mounted with `app.include_router(router, prefix=settings.api_prefix)` and the real path is
`/api/health`. The generated liveness probe 404s, the pod never becomes ready, and nothing
objected — a probe pointing at a path that does not exist is a perfectly valid `httpGet`,
so schema validation passes, and a golden-file test locks the wrong answer in and defends
it against correction.

The second failure is the harder one, and the reason a model appears anywhere in this
design. It is also why the model is confined to proposing data rather than producing
output: the mechanism that catches a wrong detection must not be able to introduce a wrong
generation.

## Proposal

Five deterministic scripts and one model-produced artifact between them.

```
  repository ─▶ scan ─▶ AppSpec + gaps ─▶ render ─▶ output tree ─▶ verify
                 ▲            │                          ▲            │
                 │            ▼                          └────────────┘
          answers.yaml ◀── apply ◀── plan ─▶ show ─▶ human      re-derive and diff
           (persisted)    (script)  (data)  (script)  reviews
                                       ▲
                                     model
```

`scan`, `show`, `apply`, `render` and `verify` are pure functions with golden tests. `plan` is the
only artifact a model produces, it is typed and schema-validated, and it is data rather
than an action. The shape of the middle is a compiler's: many front ends, one IR, many back
ends. Adding a source platform is a detector; adding a deployment target is a renderer.
The combinations do not multiply.

`render` runs before any model does. A user gets a complete output tree on the first
command, with unresolved gaps marked in it, and the plan/apply cycle improves that tree
rather than gating it.

### AppSpec

The IR. Everything detected, nothing rendered. A plain data structure, serialisable to
JSON, with a versioned schema.

```
AppSpec
  name          str
  source        { platform, repo_url, commit }   # provenance; never answerable
  services      [Service]
  datastores    [Datastore]
  routes        [Route]
  environments  [Environment]

Service
  name          str                 # identity: answers key on this, never on index.
                                    # Derived from the build context directory. Never
                                    # answerable; changing the derivation is breaking.
  role          web | api | worker | cron
  runtime       { language, version }
  build         { context, dockerfile?, install_cmd, build_cmd?, start_cmd, args: [str] }
  ports         [int]
  health        { path, port, kind: http|tcp }
  env           [EnvVar]
  resources     { requests, limits }
  replicas      int

EnvVar
  name          str
  source        literal | secret | datastore
  binding       runtime | build_arg   # build_arg is baked into the image, not the pod

Datastore
  kind          mongodb | postgres | redis | ...
  version       str?
  mode          in_cluster | managed | external   # never defaulted; always the user's
  consumed_by   [service_name]
  env_keys      [str]

Route
  host          str?
  path          str
  service       str
  port          int
  tls           bool

Environment
  name          str
  replicas      {service: int}?
  resources     {service: ...}?
```

`EnvVar.binding` exists because the first scenario breaks without it. A Vite or CRA
frontend reads its API base from `VITE_API_URL` or `REACT_APP_BACKEND_URL`, and both are
substituted into the JavaScript bundle at build time. Rendering them as a `ConfigMap` and
a Deployment `env:` block is a no-op against a container serving static files: the bundle
still contains the platform URL, every check passes, and the migrated frontend talks to
the platform the user just left. `binding: build_arg` routes the value into
`build.args` and the Dockerfile instead.

Two rules keep the boundaries honest: **detectors never write files, renderers never read
the source repository.** Wanting to break either one means the AppSpec is missing a field.
Adding the field is the fix.

### Detectors

Each detector answers one narrow question and contributes facts. They compose; they do not
coordinate. A detector that cannot determine its answer emits a gap rather than a default.

For the first scenario the set is: runtime and version, dependency manifest and install
command, service entrypoint and port, health endpoint, environment variable usage and
binding, datastore identification, and static frontend build output.

### Gaps

A gap is a typed record of something the tool could not determine:

```
Gap
  id            str                 # stable and name-based, "service.api.health.path"
  pointer       str                 # JSON pointer into this run's AppSpec
  question      str
  proposed      any?
  confidence    high | medium | low
  severity      blocking | important | cosmetic
  evidence      [str]
  origin        detector | model | stale_answer
```

`id` is the durable identity and the key everything else uses. `pointer` is a per-run
addressing convenience, recomputed on every scan, and nothing may persist it — array
indices are a function of detector output, not of application identity.

Gaps are the product, not an admission of failure. They are emitted as JSON for machines
and rendered into the human runbook. A `blocking` gap means the output will not work until
a human answers.

### Plan — the model's only output

A plan is a typed changeset proposing values for the AppSpec. It is produced by a model and
consumed by a script.

```
Plan
  basis         { appspec_sha256, repo_commit, detectors_version }
  entries       [PlanEntry]

PlanEntry
  target        str                 # a Gap.id or a detected-fact id declared
                                    # answerable — never a pointer
  kind          resolve | override
  current       any?                # value at plan time; a compare-and-swap precondition
  proposed      any
  rationale     str                 # why, in plain language, for the human reviewing
  evidence      [str]               # file:line, checked by apply to exist and to match
  confidence    high | medium | low
```

`kind` separates the two failure modes from the Problem section, because they carry
opposite risk and must not be reviewed at the same rate:

- **`resolve`** supplies a value where a gap exists. `scan` already admitted it did not
  know, so almost any answer is an improvement on a blocking gap. Low risk.
- **`override`** contradicts a fact a detector asserted. This is the model overruling
  deterministic analysis, and it is the only path by which a model can make correct output
  wrong. Acceptance is typed — an override accepted as a resolve is rejected — so
  overruling a detector is an act a script can check, not one a prose file describes.

**Address space.** A plan may only target ids a detector or gap declares answerable.
Schema validation is type-level and would happily accept `/services/0/name` — renaming
every generated file and every ArgoCD application — or `/source/commit`, falsifying
provenance. The answerable set is declared, not inferred.

Declaring it is not sufficient on its own, because nothing would stop a later detector
declaring one of those very fields — "the detected service name is wrong" is a real
complaint. So the exclusion is an invariant, on the same footing as the secret rule below:
**`AppSpec.source` in its entirety, and any field used to construct a stable id —
`Service.name` above all — are never answerable by any detector.** A detector that declares
one is a bug, and the schema check rejects it.

**Basis.** `apply` recomputes the AppSpec hash and refuses a plan whose basis no longer
matches, with no override flag: re-scan and re-plan. Terraform's saved plans are bound to a
state serial for the same reason, and the shape is worthless without it. A plan that
proposes `/services/0` while a teammate adds a worker service would otherwise write the
API's probe path onto the worker, type-check cleanly, and persist both failures.

The vocabulary is Terraform's deliberately. The mapping that matters holds: `plan` writes
nothing and is the artifact to read carefully, `apply` is what commits, and `show` renders a
plan for review. It is imperfect — Terraform's plan is the deterministic step and here it is
the only nondeterministic one — but the scrutiny it directs is at the right place.

### Show

`show(plan, appspec) -> text` renders a plan for a human to review: entries grouped by
`kind`, overrides first and marked as contradicting a detector, each with its rationale, its
evidence, and the value it would replace.

It is a script for the same reason the renderers are. Reviewing the plan is the one moment a
human decides whether a model's judgement enters the output, and if the surface they read
were model-authored, the layer this RFC is careful to keep out of output content would be
composing the argument for accepting it.

### Apply

`apply(appspec, plan, answers, accepted_resolves, accepted_overrides, now) -> answers'` is
a pure function. The
clock is an argument supplied at the edge, never read inside — the same rule AGENTS.md §3
applies to renderers.

It rejects an entry when: the basis does not match; `current` differs from the live value
(compare-and-swap); an entry with `kind: override` was accepted as a resolve; the target is
outside the declared answerable set; two entries target
the same id or nest; the cited evidence does not resolve to a real file and line range; or
the target is an `EnvVar` whose `source` is `secret`.

That last rule matters more than it looks. The answers file is written to the user's
repository and committed. Without the rule, the natural reading of "supply the value the
tool could not determine" walks a user into pasting a live database password into a file
they are then told to commit. Secret *values* never enter an answer, per AGENTS.md §4;
an answer may say where a secret comes from, never what it is.

### The answers file

The accumulated, human-accepted result of applied plans. It lives in the user's repository,
is reviewed in a diff, and is a deterministic input to `scan` on the same footing as the
source tree. It is what stops the second run re-asking everything.

```
AnswersFile
  version       int                 # 1. Bumped on any breaking change to this format.
  answers       [Answer]

Answer
  target        str                 # the stable id
  value         any
  detected      any?                # what the detector produced when this was answered
  evidence      [{path, lines, sha256}]
  detectors_version str
```

The file carries a format version from the first commit, and `scan` hard-fails on a version
it does not recognise rather than attempting a migration. The AppSpec deliberately carries
no version: it is derived, regenerated on every run, and breaking it freely is the point of
being pre-release. The answers file is the opposite — it is committed to other people's
repositories and outlives every version of this tool, so a v0.3 file silently mis-merged by
v0.9 would be the exact failure the machinery below exists to prevent, arriving through the
one door that machinery does not watch.

**Precedence and invalidation are the load-bearing part of this design.** "Answers win
over detection" is the obvious rule and it is wrong: it makes one accepted answer permanent,
silences the detector that would later correct it, and reintroduces the confidently-wrong
failure in a stickier form — because now gap count *falls* when it happens. A stale answer
is invisible to every check in this document.

The rule is therefore:

> A live detector value that disagrees with a stored answer never wins silently, in either
> direction. It becomes a gap.

`scan` merges an answer only when the detector still produces the value recorded in
`detected` (or still produces nothing, when `detected` is null) **and** every cited evidence
range hashes to what was recorded. Otherwise the answer is not merged: it is re-emitted as
a gap with `origin: stale_answer`, carrying both the stored answer and the new detected
value, which the model turns into an ordinary plan entry for a human to confirm.

There is a third way an answer goes wrong, and it defeats that test rather than failing it.
If the answer's target no longer exists in this run's AppSpec — a service renamed, a service
removed — then for a `resolve` answer `detected` is null, the detector still produces
nothing, and if the cited files did not move their ranges still hash. Both conditions pass
while the thing they guard does not exist. Such an answer is re-emitted as a gap with
`origin: orphaned_answer`, carrying the stored value and the ids that do exist. It is never
silently dropped: a human accepted it, and its disappearance is information.

This is why `Service.name`'s derivation is fixed by this RFC rather than left to a detector.
Changing it renames every service at once and orphans every answer in every user's
repository, which makes it a breaking change requiring a superseding RFC.

Evidence is hashed by cited line range rather than by whole file. Hashing
`backend/server.py` would invalidate every answer that cites it on every unrelated commit,
which is the re-asking the answers file exists to eliminate.

This is also what makes detector graduation work. When a recurring plan entry is retired
into a real detector, that detector now produces a value where it produced none, the
stored answers go stale rather than shadowing it, and the improvement shows up instead of
being masked by exactly the answers that justified writing it.

### Renderers

Each renderer turns an AppSpec into a directory of files for one target. Renderers are pure
functions and their output is byte-stable.

The first target is Kubernetes delivered by GitOps, emitting the conventional Kustomize
layout — a `base/` holding the manifests and per-environment overlays that patch image,
resources and environment-specific values — plus the GitOps `ApplicationSet` that points at
each overlay, and a Dockerfile per service.

The layout is deliberately conventional rather than novel. The output should look like
something a competent platform engineer would have written by hand, because a human has to
review it, own it, and modify it for the next five years.

**A datastore's `mode` is never defaulted.** Whether a database runs in-cluster, on a managed
service, or against something the team already operates is a decision about cost, operations
and risk — nothing in a repository can answer it, and a tool that picks has made an expensive
choice on the user's behalf without saying so. The gap is `blocking` with no `proposed` value,
and no datastore manifest is emitted for any mode.

This costs less coherence than it appears to, because the *service* side of the rendering is
identical under all three modes. Per AGENTS.md §4 a connection string is emitted as a
reference — a `secretKeyRef` — never a value, and that reference is the same whether the
string ends up pointing at a StatefulSet in the next namespace or at a managed cluster three
regions away. So the application manifests render complete and correct; the only thing absent
is the database itself, which is the only thing the user has not decided. A pod that starts
before the decision is made fails with `secret "..." not found`, which names the missing
input, rather than with a DNS timeout against a Service that was never going to exist.

The gap states what each mode would require, and the generated runbook carries the steps for
whichever is chosen.

### Verify — and why the output tree is not honour-system

`render` writes `.offramp/manifest.json` alongside its output: for every emitted path, the
SHA-256 of the bytes it wrote and the AppSpec revision that produced them.

`verify` re-renders into a temporary directory and diffs against what is on disk. It is a
script, it needs no model, and it enforces the invariant this whole design rests on:

> Every byte under the output tree is a pure function of (AppSpec, answers, renderer
> version). A non-empty verify diff is a bug report against a renderer, never something to
> reconcile by editing the output.

Without it the central safety claim is unenforceable. A model running a skill has ordinary
write access to every file it just produced, and when a probe path is wrong the cheap fix is
to edit `base/api-deployment.yaml` while the expensive one is to propose a plan entry, get
it accepted, apply, and re-render. The wrong output validates — `kubeconform` is satisfied
by a probe pointing nowhere — so nothing else in the design would notice. The same manifest
lets `render` refuse to clobber a file whose on-disk hash has changed, reporting it as a
conflict instead of destroying a user's hand edits on the second run.

`verify` lands in the same change as the first renderer — it cannot be tested before
something renders — and must be green before a second renderer, before `apply`, and before
the skill.

### Surfaces

The distribution surface is a **skill**: an instruction file that orchestrates, plus a
bundled directory of deterministic scripts that decide.

```
skills/offramp/
  SKILL.md        orchestration: which script to run, when to ask a human
  scripts/        scan, apply, render, verify — the whole product
```

The governing rule, in the form that can actually be reviewed against:

> **No byte of the output tree may be produced by anything except a renderer script reading
> the AppSpec. The model may propose AppSpec field values, and nothing else.**

The earlier phrasing — "SKILL.md must not determine output content" — does not survive
contact: the model's `proposed` values and its `rationale` prose do influence what a human
accepts, and therefore output bytes. The rule above is narrower and forbids the tempting
shortcut it is aimed at, which is writing target configuration by hand because the renderer
for it does not exist yet.

Choosing a skill over a CLI-first build order is a decision about who the user is. They are
already inside a coding agent; a skill is installable today; and it collapses four surfaces
into one before a single detector exists. **The scripts remain the product.** They take
paths and files, not conversations, so `scan`, `render` and `verify` run unattended in CI
with no model and no key — which is what keeps the quality metric below measurable, and
what a library, a CLI or an MCP server can be layered onto later without redesign.

### Gap count is the quality metric, measured twice

Every fixture run records gap count by severity. Falling gap counts on a fixed corpus is
the definition of the tool improving.

`scan` now takes two inputs, so the number must be qualified or it becomes gameable — a
contributor whose change trips the gate could accept a plan entry that echoes a gap's own
`proposed` value, and the count would fall with no detector touched and no golden file
moved.

- **Bare gap count** — `scan` run with the answers file absent. This is the headline number
  and the only series CI gates on. It measures detection.
- **Answered gap count** — with answers merged. Tracked, never gating. It measures how far
  one plan/apply cycle gets a user.

For every field a detector is supposed to determine, at least one corpus fixture must carry
no answer for it, so that detection stays under test.

Gap count alone still rewards the wrong thing, because a detector that guesses instead of
emitting a gap improves it, and the golden tree cannot object — it records whatever was
generated. So each fixture also carries `truth.yaml`: the values a human knows to be correct
for that application, written by hand and independent of anything a detector produces. CI
gates on two series — **bare gap count falling**, and **detected facts agreeing with
`truth.yaml` at 100%**. A gap that becomes a confidently wrong fact fails the second even as
it improves the first.

### Scope of the first implementation

- **One scenario:** a full-stack app with a Python API, a static JavaScript frontend, and a
  document database — the common shape produced by current app-generation platforms.
- **One target:** Kubernetes via Kustomize and GitOps.
- **One environment**, `production`, emitted through the full overlay mechanism rather than
  around it, so that adding `staging` later is additive instead of structural. This makes the
  `binding: build_arg` promotion problem moot rather than solved: with one environment the
  frontend's baked API URL is simply correct. When staging arrives the answer is runtime
  config injection — an entrypoint writing `/config.js` — which changes the user's
  application and is therefore a runbook item, not a generated file. Per-environment image
  builds are the alternative and they break build-once-promote-the-artifact; defaulting to
  them silently is the failure this note exists to prevent.
- **Greenfield only:** the user has no existing deployment repository, so the tool scaffolds
  one from an opinionated default.
- **Python**, because the first scenario's applications are Python and the ecosystem overlap
  makes detectors easier to write and verify.
- **Build order:** `scan` first; then the first renderer and its manifest, with `verify` in
  the same change; then `show` and `apply`; then the skill. The model is the last thing
  added, not the first.

## Alternatives considered

**An agent with prompt-embedded templates.** Ship a skill that instructs a model to write
the manifests, with example YAML in the prompt. Far less code, and nothing to integrate.

Rejected because the logic ends up living in prose. Nothing validates a template embedded in
a prompt, no test can assert on it, and gap count as a quality metric requires a run that
gives the same answer twice. This RFC keeps the skill as a *wrapper* and puts every byte-
producing decision in a script, which is a different proposal: scripts are testable, and
`verify` proves at runtime that they were the ones that wrote the output.

**A tool that calls a model itself.** Have a detector ask a model when it cannot determine
something. Rejected: it requires network access during generation and an API key as a
precondition for output, and it makes detectors non-deterministic, which ends golden-file
testing. Under the skill design `offramp` calls no model at all — the model already running
calls `offramp`. AGENTS.md §2's prohibition on reading from the network during generation
therefore needs no amendment.

**Letting the model edit the AppSpec directly.** Simpler, and one artifact fewer. Rejected
because the model's output would become an action rather than data: unreviewable as a diff,
unvalidatable against an address space, and impossible to reject entry by entry.

**Answers win over detection.** The obvious merge rule, and one line of code. Rejected
above: it makes a wrong answer permanent and inverts the quality metric.

**Direct generation with no IR.** Read the repository and write files in one pass. Rejected
because the second target reuses none of the first: N×M implementations that drift, against
N+M with the IR.

**Deploying, not just generating.** Rejected for the first implementation and constrained
permanently by `AGENTS.md`.

**Reading the running application instead of reasoning about it.** The strongest alternative
considered. Every FastAPI app serves `/openapi.json`, returning the complete post-mounting
route table — real paths with prefixes already applied. One GET against the URL the user is
migrating away from would answer the health path, the Ingress route list and the
frontend/backend prefix split as a checked fact rather than a judgement, in the case that
static analysis of a settings-derived prefix cannot decide in general.

Rejected, for four reasons that compound.

It is not reliably available: migrations happen after a billing cutoff, after a shutdown, or
from a repository handed to someone who never had access to the running app. A detection
input that is present for some runs and absent for others produces two classes of output
whose quality cannot be compared, and the quality story here rests on a fixed corpus.

It is not reliably public. `/openapi.json` behind authentication means handling a credential
to read it, which reintroduces the secret-handling surface this design otherwise does not
have.

It is framework-shaped. FastAPI serves an OpenAPI document; Express, Django and most
JavaScript backends do not. It would resolve the flagship case for one framework and leave
the detector work to be done anyway for the rest.

And it would narrow the declarative constraint. That constraint's value is that it is
absolute and needs no argument at the boundary; narrowing it once turns every later request
into an argument about degree.

The consequence is accepted rather than wished away: the router-prefix case remains
undecidable by static analysis, and it is what the model surface exists to carry. A related
consequence is an improvement — `truth.yaml` stays hand-written, and ground truth that no
tool generated is better ground truth for testing a tool.

## Impact on generated output

None — there is no existing output. This RFC establishes the baseline that later RFCs will
be measured against.

## Testing

**Golden fixtures, twice over.** Each fixture in `fixtures/` carries an input tree, an
`expected_bare/` rendered with no answers merged, and an `expected_answered/` rendered with
its checked-in answers. `expected_bare/` is the tree that moves when a detector improves and
is where the signal lives; without the split, a fixture whose expected output depends on an
applied answer passes identically whether the detector works or does not run at all.

**Provenance assertions.** Every leaf of the merged AppSpec carries `origin: detector |
answer` in a canonically serialised sidecar. This makes detector graduation mechanically
testable: after adding detector X, assert the provenance for its field flips from `answer`
to `detector` and that `expected_bare/` gains the correct value.

**Staleness and orphan tests.** Fixtures with an answer whose cited evidence has moved, and
with a detector that now disagrees, asserting a `stale_answer` gap is emitted rather than the
answer being merged; and a fixture whose answered service has been renamed away, asserting an
`orphaned_answer` gap rather than a silent drop.

**Ground truth.** Every fixture's detected facts are asserted against its hand-written
`truth.yaml`. This is the assertion that fails when a detector guesses.

**Show is golden-tested.** The review surface a human reads is byte-stable for a given plan,
including the ordering that puts overrides first.

**Apply is a pure function** and is golden-tested as one: `(appspec, plan, answers,
accepted, now) -> answers'`, including every rejection path — bad basis, failed
compare-and-swap, out-of-address-space target, colliding entries, unresolvable evidence, and
an attempt to answer a secret, and an override accepted as a resolve.

**Verify.** A fixture whose output tree has been deliberately tampered with, asserting a
non-zero exit and a diff naming the file.

**Validation of generated artifacts.** `kustomize build`, `kubeconform`, and `docker build`
on generated Dockerfiles. Read-only, touching no real infrastructure.

**Gap metrics.** Bare gap count by severity is recorded per fixture; an increase in blocking
gaps fails CI unless the authorising RFC says why.

**CI runs no model.** Everything above executes with no API key present. Plan handling is
tested against checked-in plan fixtures, not generated ones.

**Fixtures must be synthetic or explicitly licensed.** No customer repository, and no
credential, hostname or account identifier — not even an expired one.

## Declarative constraint

This proposal complies, and tightens rather than loosens the constraint.

Every renderer emits files. Nothing in the pipeline calls a cloud API, requires cloud
credentials, or mutates any running system. `offramp` itself makes no network request and
holds no API key: the model runs outside it, in the agent the user is already using, and
reaches the scripts by executing them. The validation steps are read-only and operate on
generated output in a temporary directory.

Two consequences deserve stating rather than inferring. First, `verify` converts the
declarative claim from an honour-system rule into a check anyone can run — it is the only
mechanism in this document that tests whether a renderer actually produced the output.
Second, the skill layout must be brought inside the project's own enforcement: everything
under `skills/` is program text, `SKILL.md` included, and the RFC gate in
`scripts/check_rfcs.py` does not gate `skills/` at all, and adding it to the gated prefixes
would not be sufficient — the `.md` suffix exemption, written when Markdown meant
documentation, would still release `SKILL.md`, the one file that governs the model. The gate
therefore needs a prefix set carrying no suffix exemption. That change lands with this RFC,
not after it.

Work that cannot be declarative — moving data, cutting over DNS, setting a secret value for
the first time — is written into the generated runbook as instructions for a human, with the
commands spelled out. The tool does not run them.

## Out of scope

- **Adopting an existing deployment repository** — inferring conventions from a repository
  the user already has. The more valuable mode for established teams, deliberately second.
- **Data migration.** Detecting the datastore and generating its deployment is in scope;
  moving the contents is a runbook.
- **Additional targets** — Terraform, Nomad, Compose, managed container services.
- **Additional source platforms.**
- **A library, CLI or MCP server surface.** The scripts are designed to make these additive.
- **Three-way merging of user edits into regenerated output.** `render` detects the conflict
  via the manifest and refuses; reconciling it is the user's, for now.
- **Autonomous verification** against a live or ephemeral cluster. `verify` checks that the
  output matches the renderers, not that it runs.
- **Compliance evidence artifacts.**

## Open questions

None. The five questions this RFC opened during drafting are resolved in the sections above:
the answers file carries a format version and the AppSpec does not; a datastore's `mode` is
never defaulted; reading the running application is rejected under Alternatives; the
Terraform vocabulary is kept deliberately; and the first implementation targets a single
`production` environment through the full overlay mechanism.
