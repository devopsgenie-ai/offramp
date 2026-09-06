# offramp

**The off-ramp from managed app platforms.** `offramp` reads an application that was
built on a hosted platform — Emergent, Lovable, Vercel — and generates the declarative
configuration needed to run it on infrastructure you own.

> **Status: pre-implementation.** This repository currently contains process and design
> only. No tool exists yet. [RFC-0001](rfcs/0001-architecture.md) proposes the
> architecture and is open for comment. Nothing gets built until it is accepted.

## What it will do

Given an app repository, `offramp` produces:

- **Container definitions** — a Dockerfile per service, following the conventions of the
  detected stack.
- **Kubernetes configuration** — Kustomize bases and per-environment overlays, plus the
  GitOps wiring (an ArgoCD `ApplicationSet`) to deliver them.
- **Infrastructure as code** — Terraform for the managed services the app depends on.
- **A gap report** — a typed, machine-readable list of everything it could not determine
  on its own, each with a question, a proposed default, and a confidence level.

## What it will never do

`offramp` is a **code author, not a deployer**. It does not hold cloud credentials and
does not mutate running infrastructure.

It will not run `kubectl apply`, `terraform apply`, `helm install`, or any mutating
cloud API call. It writes files. You review them, you commit them, and your own
pipeline applies them.

This is a hard architectural constraint, not a default — see
[AGENTS.md](AGENTS.md#the-declarative-constraint).

## Why

Apps built on hosted platforms are fast to create and difficult to leave. The code is
yours, but the deployment is not: no Dockerfile, no manifests, no infrastructure
definition, and no way to run it anywhere else. Teams hit this wall for ordinary
reasons — cost at scale, or a compliance requirement that data live somewhere specific.

Getting out is a week of undifferentiated work that every team does from scratch.
`offramp` automates the part of that week nobody should be writing by hand — the
containers, the manifests, the GitOps wiring — and tells you plainly what it could not
work out. It does not provision your cluster, move your data, or cut your DNS over. Those
are yours, and the tool is explicit about which is which.

## Design principles

1. **Declarative output only.** Everything the tool emits is configuration. The
   imperative steps that genuinely cannot be — a data migration, a DNS cutover — are
   written into a runbook for a human, never executed.
2. **Deterministic core.** Detectors and renderers are pure functions: repository in,
   configuration out. Same input, same bytes. This is what makes the output testable.
3. **Honest gaps.** The tool reports what it does not know instead of guessing. An
   unresolved gap is a normal outcome, not a failure.
4. **Agent-friendly, not agent-dependent.** It runs standalone on a laptop or in CI, and
   exposes the same operations to a coding agent so an agent can resolve gaps
   conversationally. The reasoning is optional; the generation is not.

## Using it

Not yet — see the status note above; `skills/` does not exist until RFC-0001 is accepted.
When it does, the install path is this:

Clone the repository and point your coding agent at `skills/offramp/`. There is no
registry to install from and no account to create — the skill is a directory of
instructions and scripts, and the scripts run on their own if you would rather not use an
agent at all.

That is deliberate. A tool whose purpose is getting you off a platform should not make
its own distribution depend on one.

## Contributing

The RFC process gates all implementation — see [CONTRIBUTING.md](CONTRIBUTING.md) and
[rfcs/README.md](rfcs/README.md). Discussion on RFC-0001 is the most useful thing you can
contribute right now.

## License

MIT — see [LICENSE](LICENSE).
