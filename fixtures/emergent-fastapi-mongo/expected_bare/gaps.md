# Gaps — widgets

blocking: 8  important: 6  cosmetic: 1  (total 15)

4 of these are actions rather than values: there is nothing to record, you do the thing and confirm it.

## blocking

### credential.backend.env.JWT_SECRET

`JWT_SECRET` is committed with a real-looking value in `backend/.env` (line 3). Rotate this credential before cutover, and treat the current value as disclosed: it is in the git history, so deleting the file does not remove it. Change it at the source (the database user's password, the signing key, the provider's token), then supply the new value to the cluster as a Secret rather than a committed file. Doing it now costs least -- the datastore is moving anyway, so one cutover covers both. Confirm here once the credential has been rotated. The value is not recorded anywhere in this tool's output.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high
- evidence: `backend/.env:3`

### credential.backend.env.MONGO_URL

`MONGO_URL` is committed with a real-looking value in `backend/.env` (line 2). Rotate this credential before cutover, and treat the current value as disclosed: it is in the git history, so deleting the file does not remove it. Change it at the source (the database user's password, the signing key, the provider's token), then supply the new value to the cluster as a Secret rather than a committed file. Doing it now costs least -- the datastore is moving anyway, so one cutover covers both. Confirm here once the credential has been rotated. The value is not recorded anywhere in this tool's output.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high
- evidence: `backend/.env:2`

### datastore.mongodb.mode

How should mongodb run after the migration? `in_cluster` generates a StatefulSet and a PersistentVolumeClaim and you operate it: cheapest, and backups, upgrades and failover become yours. `managed` generates a reference to a provider-run instance you create separately: the operational work goes away and the bill appears. `external` generates a reference to something that already exists and is not moving -- including the platform's own database, if you are migrating compute first. Nothing is proposed: this is a cost and data-residency decision, and nothing in the repository indicates the answer.

- proposed: none — this one has no sensible default
- confidence: low
- evidence: `backend/services/db.py`

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

### routes.host

Which hostname serves this application? One answer sets the host on all 2 detected routes. Nothing in the repository records the production hostname -- the platform supplied it.

- proposed: none — this one has no sensible default
- confidence: low

## important

### datastore.mongodb.version

Which mongodb version? Only needed if mongodb runs in the cluster -- a managed or external instance already has one. Match what the application runs on today, which the client library's own compatibility range does not pin down.

- **answer `datastore.mongodb.mode` first** — this question may not need an answer at all once it is
- proposed: none — this one has no sensible default
- confidence: low

### delivery.image_tag

Which image tag should the overlay reference? CI normally answers this on every build rather than a person answering it once, and the answers file records that it was accepted by CI rather than by a human.

- proposed: none — this one has no sensible default
- confidence: low

### service.backend.runtime.version

Which Python version does `backend` run on? Nothing in the service declares one -- no .python-version, runtime.txt or requires-python -- so the image tag would otherwise be a guess.

- proposed: `3.11`
- confidence: medium
- evidence: `backend/requirements.txt`

### service.frontend.dependencies.lockfile

Commit a lock file for `frontend`. There is none, so `npm install` resolves ranges afresh on every build and two builds of the same commit can ship different dependency code. Run `npm install` locally, commit the resulting package-lock.json, and confirm here -- the generated build will then use `npm ci`.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high
- evidence: `frontend/package.json`

### service.frontend.probes

`frontend` exposes no health endpoint, so no liveness or readiness probe can be generated for it. Add one -- a handler that returns 200 without touching the database is enough -- and confirm here. Without it Kubernetes cannot tell a started container from a working one, and a rollout will report success while the service is broken. No path is proposed because pointing a probe at a business endpoint restarts a healthy container whenever that endpoint is slow.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high

### service.frontend.runtime.version

Which Node version builds `frontend`? Neither `engines.node` nor .nvmrc declares one, so the build image tag would be a guess.

- proposed: `20`
- confidence: medium
- evidence: `frontend/package.json`

## cosmetic

### service.backend.env.UNUSED_LEGACY_FLAG

`UNUSED_LEGACY_FLAG` is set in `backend/.env` but no module in `backend` reads it. It is probably dead configuration left by the platform. Answer with a value to carry it into the deployment anyway, or say so and it will be dropped.

- proposed: none — this one has no sensible default
- confidence: medium
- evidence: `backend/.env:5`
