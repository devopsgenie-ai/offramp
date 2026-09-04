# offramp

**The off-ramp from managed app platforms.** `offramp` reads an application that was
built on a hosted platform — Emergent, Lovable, Vercel — and generates the declarative
configuration needed to run it on infrastructure you own.

> **Status: nothing is implemented yet.** The architecture, the contribution process and
> the roadmap are being proposed for review. Watch the open pull requests.

## What it will do

Given an app repository, produce a Dockerfile per service, Kubernetes configuration and
the GitOps wiring to deliver it, infrastructure as code for the services the app depends
on, and a report of everything it could not determine on its own.

## What it will never do

`offramp` is a code author, not a deployer. It does not hold cloud credentials and does
not mutate running infrastructure. It writes files; you review them, and your own pipeline
applies them.

## License

MIT — see [LICENSE](LICENSE).
