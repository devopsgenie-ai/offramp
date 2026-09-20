# Gaps — settings-prefix

blocking: 6  important: 3  cosmetic: 0  (total 9)

1 of these are actions rather than values: there is nothing to record, you do the thing and confirm it.

## blocking

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

### service.backend.ports

Which port does `backend` listen on? Nothing in the repository binds one -- no literal `uvicorn.run(port=...)` and no Dockerfile EXPOSE -- so the generated Service, the probes and the Ingress backend would all be guessing together, and they would have to guess the same thing.

- proposed:

  ```json
  [
    8000
  ]
  ```
- confidence: medium
- evidence: `backend/requirements.txt`

### service.backend.routes.prefix

What is the mounted path prefix for `backend`? `backend/server.py` line 7 mounts a router with `prefix=settings.api_prefix`, which is read from configuration rather than written in the repository, so the routes cannot be composed. Answer with the prefix this application actually serves on in production (for example `/api`). Reporting the decorator literal instead would generate an Ingress that returns 404 while every manifest check passes.

- proposed: none — this one has no sensible default
- confidence: low
- evidence: `backend/server.py:7`

## important

### delivery.image_tag

Which image tag should the overlay reference? CI normally answers this on every build rather than a person answering it once, and the answers file records that it was accepted by CI rather than by a human.

- proposed: none — this one has no sensible default
- confidence: low

### service.backend.probes

`backend` exposes no health endpoint, so no liveness or readiness probe can be generated for it. Add one -- a handler that returns 200 without touching the database is enough -- and confirm here. Without it Kubernetes cannot tell a started container from a working one, and a rollout will report success while the service is broken. No path is proposed because pointing a probe at a business endpoint restarts a healthy container whenever that endpoint is slow.

- kind: action — nothing goes in the answers file; do it and say so
- proposed: none — this one has no sensible default
- confidence: high

### service.backend.runtime.version

Which Python version does `backend` run on? Nothing in the service declares one -- no .python-version, runtime.txt or requires-python -- so the image tag would otherwise be a guess.

- proposed: `3.11`
- confidence: medium
- evidence: `backend/requirements.txt`
