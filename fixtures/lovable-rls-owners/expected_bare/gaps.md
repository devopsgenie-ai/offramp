# Gaps — team-journal

blocking: 10  important: 3  cosmetic: 0  (total 13)

1 of these are actions rather than values: there is nothing to record, you do the thing and confirm it.

## blocking

### datastore.supabase.mode

Does the Supabase database stay where it is? The client points at a hosted Supabase project, so `external` -- keep it, and point the migrated application at it -- is proposed. `managed` moves the data to a Postgres instance you create; `in_cluster` runs Postgres yourself. The database also holds auth users and storage, which a plain Postgres instance does not replace.

- proposed: `external`
- confidence: medium
- evidence: `src/integrations/supabase/client.ts:1`

### datastore.supabase.table.public.assignments.write_scope

Who may change rows in `public.assignments`? More than one column could be the owner, and nothing says which: `assignee_id` (it references auth.users.id); `created_by` (it references auth.users.id). `created_by` is proposed from its name alone, which is not evidence. No policy can be written without guessing. Answer with the owner column, or `server_only` if only your server should write.

- proposed:

  ```json
  {
    "column": "created_by",
    "kind": "owner"
  }
  ```
- confidence: medium
- evidence: `supabase/migrations/20260302100000_tables.sql:48`, `supabase/migrations/20260302100000_tables.sql:55`

### datastore.supabase.table.public.bookmarks.write_scope

Who may change rows in `public.bookmarks`? `user_id` references auth.users.id, but nothing says the user it names is the one who writes the row: no policy compares it to auth.uid(), and it has no `default auth.uid()`. It is proposed, which is not evidence. No policy can be written without guessing. Answer with the owner column, or `server_only` if only your server should write.

- proposed:

  ```json
  {
    "column": "user_id",
    "kind": "owner"
  }
  ```
- confidence: medium
- evidence: `supabase/migrations/20260302100000_tables.sql:13`

### datastore.supabase.table.public.drafts.write_scope

Who may change rows in `public.drafts`? The policy that would prove the owner was written before a column it names was renamed, so the migrations no longer say which column it means: policy "Authors can read their drafts" names `author` (supabase/migrations/20260302100000_tables.sql:79). `author_id` is proposed from its name alone, which is not evidence. No policy can be written without guessing. Answer with the owner column, or `server_only` if only your server should write.

- proposed:

  ```json
  {
    "column": "author_id",
    "kind": "owner"
  }
  ```
- confidence: medium
- evidence: `supabase/migrations/20260302100000_tables.sql:73`, `supabase/migrations/20260302100000_tables.sql:79`, `supabase/migrations/20260302100000_tables.sql:81`

### datastore.supabase.table.public.invoices.write_scope

Who may change rows in `public.invoices`? More than one column could be the owner, and nothing says which: `customer_id` (policy "Customers can view their invoices" compares it to auth.uid() (supabase/migrations/20260302100000_tables.sql:67)); `issuer_id` (it references auth.users.id). No policy can be written without guessing. Answer with the owner column, or `server_only` if only your server should write.

- proposed: none — this one has no sensible default
- confidence: low
- evidence: `supabase/migrations/20260302100000_tables.sql:60`, `supabase/migrations/20260302100000_tables.sql:67`, `supabase/migrations/20260302100000_tables.sql:69`

### datastore.supabase.table.public.notes.write_scope

Who may change rows in `public.notes`? No column is proven to identify a row's owner. `user_id` is proposed from its name alone, which is not evidence. No policy can be written without guessing. Answer with the owner column, or `server_only` if only your server should write.

- proposed:

  ```json
  {
    "column": "user_id",
    "kind": "owner"
  }
  ```
- confidence: medium
- evidence: `supabase/migrations/20260302100000_tables.sql:30`

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
