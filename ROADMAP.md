# Roadmap

What we intend to build, roughly in order. This is a statement of direction, not a
commitment to dates. Everything here needs an RFC before it is implemented; items are
listed with the RFC that covers them once one exists.

`offramp` is maintained by [DevOps Genie](https://devopsgenie.ai) and is MIT licensed.
The tool generates configuration and stops there — see the declarative constraint in
[AGENTS.md](AGENTS.md#the-declarative-constraint). That boundary is permanent and applies
to us as much as to anyone.

## Now

| Item | RFC | Notes |
|---|---|---|
| Core architecture | [0001](rfcs/0001-architecture.md) | Detectors, AppSpec, renderers, gaps. **In review.** |
| First scenario: Python API + JS frontend + document DB | — | The common shape from current app-generation platforms. |
| First target: Kubernetes via Kustomize + GitOps | — | Base, overlays, `ApplicationSet`, Dockerfiles. |
| Golden fixture corpus + gap metrics in CI | — | The quality baseline everything else is measured against. |
| CLI | — | `scan`, `generate`, `gaps`. |

## Next

| Item | Notes |
|---|---|
| **MCP server** | Same operations as the CLI, exposed as tools so a coding agent can drive the pipeline and resolve gaps conversationally. |
| **Adopt mode** | Read an existing deployment repository, infer its conventions, and generate config in its dialect instead of the default layout. The higher-value mode for established teams; needs greenfield as its reference first. Convention inference must report what it inferred rather than guess silently. |
| **Terraform target** | Managed data services, registries, and cluster-adjacent resources. Policies generated in HCL with real interpolation — never as opaque literals in a variables file. |
| **More scenarios** | Lovable (Vite + React + hosted Postgres), Vercel, then Bolt / v0 / Replit. Vercel is the highest-demand and hardest: middleware, ISR, edge runtime, image optimisation and server actions do not lift cleanly. |
| **Generated migration runbook** | Typed, per-app, replacing hand-written prose: maintenance mode, dump and restore, secret cutover, DNS, verification, rollback. The tool writes it; a human runs it. |

## Later

| Item | Notes |
|---|---|
| **Autonomous verification** | Boot the generated output on a disposable local cluster, assert health and a smoke request. Turns "hands-off success rate" into a measured number instead of a claim, and is the precondition for any autonomy. |
| **More targets** | Docker Compose and a single-host profile for people who do not want Kubernetes; Nomad; managed container services. |
| **Compliance evidence** | A signed manifest per run — what moved, where it landed, region and residency, image digests, how secrets were handled. One evidence model with pluggable mappings, rather than separate implementations per regime. Residency is the part that is a genuine product capability rather than paperwork. |
| **TypeScript implementation** | The AppSpec is language-neutral by design. A TS port would make `npx` distribution possible and fit the agent-tooling ecosystem. Only worth doing once the schema is stable. |
| **Data migration execution** | Currently and deliberately a runbook. Executing it is the highest-blast-radius thing this tool could ever do; it requires the verification work above to exist first. |

## Explicitly not planned

- Holding cloud credentials or mutating running infrastructure. This is the line the
  project is built around.
- Becoming a deployment platform, a CI system, or a Kubernetes distribution.
- Supporting proprietary formats we cannot test against openly.
