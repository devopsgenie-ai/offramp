# Roadmap

What we intend to build, roughly in order. This is a statement of direction, not a
commitment to dates. Everything here needs an RFC before it is implemented; items are
listed with the RFC that covers them once one exists.

`offramp` is maintained by [DevOps Genie](https://devopsgenie.ai) and is MIT licensed.
The tool generates configuration and stops there — see the declarative constraint in
[AGENTS.md](AGENTS.md#the-declarative-constraint). That boundary is permanent and applies
to us as much as to anyone.

`offramp` has two tracks over one shared core
([RFC-0005](rfcs/0005-two-tracks.md)). The **auditor** tells you whether an app is safe
to keep running, and repairs what the repository proves. The **generator** writes the
deployment definition for running it somewhere you own. They run in parallel. The only
dependency between them is that each one's use of answers waits for `show` and `apply`
in the core.

## Core

Shared by both tracks. A change here needs the agreement of both.

| When | Item | RFC | Notes |
|---|---|---|---|
| Done | Architecture: detectors, AppSpec, gaps, plan and apply | [0001](rfcs/0001-architecture.md) | `scan`, the AppSpec, the gap model, the migration replay, `verify`. |
| Now | **`show`, `apply` and the answers file** | [0001](rfcs/0001-architecture.md) | As RFC-0001 specifies. The first answerable field is a table's owner (`write_scope`), the only one with a working consumer today. Delivery fields follow with no change to the mechanism. |
| Now | Gaps labelled by track | [0005](rfcs/0005-two-tracks.md) | Derived from the gap id. A count per track is reported, and the total stays the gate. |
| Later | **TypeScript implementation** | — | The AppSpec is language-neutral by design. A TS port would make `npx` distribution possible and fit the agent-tooling ecosystem. Only worth doing once the schema is stable. |

## Auditor

`audit` and `fix`.

| When | Item | RFC | Notes |
|---|---|---|---|
| Done | **`audit`: read-only readiness report** | [0002](rfcs/0002-audit-first.md) | Supabase row-level security, secrets in the bundle, committed credentials, platform couplings. Lovable and Emergent. |
| Now | **`fix`: row-level-security migrations**, completed | [0003](rfcs/0003-fix-generation.md) | Implemented. Remaining: decide Open question 6 (#35), fix the defects found in review, record the RFC as implemented (#45), then document `fix` in the README (#42). |
| Now | Database functions that trust a caller-supplied user id | [0004](rfcs/0004-functions-that-trust-the-caller.md) | The replay reads functions (#46). Remaining: the check, its fixture and the corpus precision run (#47). |
| Next | `fix` from answered owners | [0003](rfcs/0003-fix-generation.md) | Once `apply` exists, an answered `write_scope` becomes an executable fix. Today 2 of 478 corpus findings get one. |
| Next | **Distribution** | — | A GitHub Action or a package for `audit --fail-on`, so CI can run it without a clone. Needs an RFC. |
| Later | Edge functions that use the service-role key without checking the caller | — | Reads TypeScript, not SQL. Its own RFC (RFC-0004, *Alternatives considered*). |
| Later | `fix` for definer functions | — | Emit the `revoke` for a function the audit reports. |

## Generator

`render` and `verify`, then the skill. The first implementation is RFC-0001's scope as
written: Emergent (FastAPI + CRA + Mongo) to Kubernetes through Kustomize and GitOps.

| When | Item | RFC | Notes |
|---|---|---|---|
| Now | **Kustomize + GitOps renderer for Emergent** | [0001](rfcs/0001-architecture.md) | `base/`, a `production` overlay, an `ApplicationSet`, and a Dockerfile per service that has none. Renders with gaps marked, before any answer exists. `verify` learns to check a second renderer in the same change. |
| Next | Delivery answers through `apply` | [0001](rfcs/0001-architecture.md) | A human answers the registry, ingress class and GitOps repository. CI answers the image tag on every build. |
| Next | **Skill** | [0001](rfcs/0001-architecture.md) | `SKILL.md`, orchestrating both tracks. Clone-and-point at `skills/offramp/`. The scripts remain the product. |
| Next | **MCP server** | — | Same operations as the scripts, exposed as tools so a coding agent can drive the pipeline and resolve gaps conversationally. |
| Next | **More scenarios** | — | The Lovable shape (Vite + React + hosted Postgres), Vercel, then Bolt / v0 / Replit. Vercel is the highest-demand and hardest: middleware, ISR, edge runtime, image optimisation and server actions do not lift cleanly. |
| Next | **Generated migration runbook** | — | Typed, per-app, replacing hand-written prose: maintenance mode, dump and restore, secret cutover, DNS, verification, rollback. The tool writes it; a human runs it. |
| Later | **Adopt mode** | — | Read an existing deployment repository, infer its conventions, and generate config in its dialect instead of the default layout. The higher-value mode for established teams; needs greenfield as its reference first. Convention inference must report what it inferred rather than guess silently. |
| Later | **Terraform target** | — | Managed data services, registries, and cluster-adjacent resources. Policies generated in HCL with real interpolation — never as opaque literals in a variables file. |
| Later | **More targets** | — | Docker Compose and a single-host profile for people who do not want Kubernetes; Nomad; managed container services. |
| Later | **Autonomous verification** | — | Boot the generated output on a disposable local cluster, assert health and a smoke request. Turns "hands-off success rate" into a measured number instead of a claim, and is the precondition for any autonomy. |
| Later | **Compliance evidence** | — | A signed manifest per run — what moved, where it landed, region and residency, image digests, how secrets were handled. One evidence model with pluggable mappings, rather than separate implementations per regime. Residency is the part that is a genuine product capability rather than paperwork. |

## Explicitly not planned

- Holding cloud credentials or mutating running infrastructure. This is the line the
  project is built around, and it is permanent. Executing a migration rather than writing
  it into a runbook is on the far side of that line, so it is not on this roadmap at any
  horizon — not as a later item, not behind a flag.
- Favouring a DevOps Genie product in generated output. No release will propose one as a
  default or a preferred option, and any DevOps Genie target ships as one renderer among
  peers. `offramp` does not hold your credentials and does not steer you.
- Becoming a deployment platform, a CI system, or a Kubernetes distribution.
- Supporting proprietary formats we cannot test against openly.
