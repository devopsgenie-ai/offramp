# Gaps — booking-desk

blocking: 5  important: 3  cosmetic: 0  (total 8)

1 of these are actions rather than values: there is nothing to record, you do the thing and confirm it.

## blocking

### datastore.supabase.mode

Does the Supabase database stay where it is? The client points at a hosted Supabase project, so `external` -- keep it, and point the migrated application at it -- is proposed. `managed` moves the data to a Postgres instance you create; `in_cluster` runs Postgres yourself. The database also holds auth users and storage, which a plain Postgres instance does not replace.

- proposed: `external`
- confidence: medium
- evidence: `src/integrations/supabase/client.ts:1`

### delivery.gitops_repo_url

What is the URL of the *deployment* repository -- the one this generated tree will be committed to and that Argo CD watches? This is not the application's source repository, which is recorded separately as provenance.

- proposed: none — this one has no sensible default
- confidence: low

### delivery.image_registry

Which container registry will hold the built images? The overlay writes image references against it, so nothing can be pulled until it is set. Example: ghcr.io/your-org.

- proposed: none — this one has no sensible default
- confidence: low

### delivery.ingress_class

Which ingress controller serves this cluster? The Ingress needs its class name. Common answers are nginx, traefik and alb.

- proposed: none — this one has no sensible default
- confidence: low

### environment.production.namespace

Which Kubernetes namespace does the `production` overlay deploy into? Every generated object is namespaced by it.

- proposed: none — this one has no sensible default
- confidence: medium

## important

### delivery.image_tag

Which image tag should the overlay reference? CI normally answers this on every build rather than a person answering it once, and the answers file records that it was accepted by CI rather than by a human.

- proposed: none — this one has no sensible default
- confidence: low

### service.web.probes

`web` exposes no health endpoint, so no liveness or readiness probe can be generated for it. Add one -- a handler that returns 200 without touching the database is enough -- and confirm here. Without it Kubernetes cannot tell a started container from a working one, and a rollout will report success while the service is broken. No path is proposed because pointing a probe at a business endpoint restarts a healthy container whenever that endpoint is slow.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high

### service.web.runtime.version

Which Node version builds `.`? Neither `engines.node` nor .nvmrc declares one, so the build image tag would be a guess.

- proposed: `20`
- confidence: medium
- evidence: `package.json`
