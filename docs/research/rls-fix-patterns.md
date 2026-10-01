# Research: row-level-security fix patterns for Supabase

Background for [RFC-0003](../../rfcs/0003-fix-generation.md): what a generated RLS fix
should look like, what Supabase's own tooling says about the same issues, how
Lovable-generated schemas identify a row's owner, and the ways a fix goes wrong. Written
2026-09-28, before the renderer, so that each rendering decision can cite something.

Sources are primary: Supabase's documentation, the lint SQL of
[`supabase/splinter`](https://github.com/supabase/splinter) (what the Security Advisor
runs), and the PostgreSQL manual. Behaviour marked **[checked]** was also confirmed on a
disposable PostgreSQL 14 with a stub `auth.uid()` that copies Supabase's definition.
That is corroboration, not a source. Corpus figures are aggregates over the recall corpus
from RFC-0002 (#16), and no repository is named.

## Sources

| Key | URL |
|---|---|
| SB-RLS | https://supabase.com/docs/guides/database/postgres/row-level-security |
| SB-PERF | https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv |
| SB-API | https://supabase.com/docs/guides/api/securing-your-api |
| SB-USERS | https://supabase.com/docs/guides/auth/managing-user-data |
| SB-RAG | https://supabase.com/docs/guides/ai/rag-with-permissions |
| SB-ADV | https://supabase.com/docs/guides/database/database-advisors |
| SPL | https://github.com/supabase/splinter/tree/main/lints |
| SB-EX | https://github.com/supabase/supabase/blob/master/examples/user-management/nextjs-user-management/supabase/migrations/20221017024722_init.sql |
| PG-RLS | https://www.postgresql.org/docs/current/ddl-rowsecurity.html |
| PG-CP | https://www.postgresql.org/docs/current/sql-createpolicy.html |
| PG-DP | https://www.postgresql.org/docs/current/sql-droppolicy.html |
| PG-AT | https://www.postgresql.org/docs/current/sql-altertable.html |
| PG-INS | https://www.postgresql.org/docs/current/sql-insert.html |
| LV-DB | https://docs.lovable.dev/features/database |
| LV-SEC | https://docs.lovable.dev/features/security |

## 1. The owner-scoped pattern Supabase recommends

- **One policy per command, not `FOR ALL`.** SB-RLS recommends a separate policy for
  each of SELECT, INSERT, UPDATE and DELETE, so that each rule says which operation it is
  for.
- **Which clause each command takes.** SELECT and DELETE take `using`. INSERT takes only
  `with check` (PG-CP: an INSERT policy cannot have a USING expression). UPDATE takes
  both: `using` chooses the rows that can be changed, and `with check` constrains what
  they may become (SB-RLS, PG-CP).
- **Name the role.** SB-RLS says to always give a `to` clause. SB-PERF adds the reason:
  with `to authenticated`, the policy is skipped entirely for `anon` requests.
- **`auth.uid()` is null without a signed-in user** (SB-RLS). An owner comparison
  granted to `anon` can never pass, which is why a replacement for an `anon` or `public`
  policy is granted `to authenticated` (RFC-0003).
- **Wrap the call: `(select auth.uid()) = user_id`.** The subselect lets PostgreSQL
  evaluate it once per statement as an initPlan, instead of once per row (SB-RLS,
  SB-PERF). The Advisor's `auth_rls_initplan` lint (0003, WARN) flags the unwrapped form.
  Its check is a string match on the deparsed policy, which PostgreSQL stores as
  `( SELECT auth.uid() AS uid)`, so the wrapped form passes (SPL). [checked: `EXPLAIN`
  shows an InitPlan for the wrapped form.]
- **UPDATE also needs a SELECT policy.** SB-RLS: "a corresponding SELECT policy is
  required". Without one, a filtered UPDATE matches no rows and raises no error.
  [checked] `INSERT … RETURNING` (what `supabase-js` does for `.insert().select()`)
  likewise needs the new row to pass a SELECT policy (PG-CP).
- **Index the owner column.** SB-RLS and SB-PERF report large speed-ups from a plain
  index on the column a policy filters on.

**Decisions this supports.** The renderer emits one policy per command, `to
authenticated`, with `(select auth.uid())` in both `using` and `with check` on UPDATE.
It does not create indexes: an index is a performance change with a locking cost on a
large table, not part of closing the finding, so `FIXES.md` recommends it instead.

## 2. Defaults and nullable owner columns

- **`default auth.uid()`** fills an owner column the client leaves out of the INSERT
  (PG-INS: an omitted column gets its default). Supabase's own RAG example declares
  `owner_id uuid not null references auth.users (id) default auth.uid()` (SB-RAG).
  [checked]
- **An explicit NULL is not the default.** It is stored as NULL, the `with check` then
  evaluates to null, and PG-CP says a WITH CHECK that is false *or null* raises an error.
  [checked]
- **Rows whose owner is NULL become invisible** to every API user once an owner-scoped
  `using` applies, because rows for which the expression is null are not visible (PG-CP).
  `service_role` still sees them (it has BYPASSRLS, SB-RLS).
- **A server write with no user token has `auth.uid()` null**, so it fails an owner
  check unless it uses a role that bypasses RLS (SB-RLS).

**Decisions this supports.** `FIXES.md` states, per fixed table, whether the owner
column is nullable (rows with no owner become unreachable) and whether it lacks
`default auth.uid()` (the app must set the owner on every insert). The renderer does not
add a default or a NOT NULL constraint itself: both change behaviour the finding did not
name, and NOT NULL fails outright on a table that already holds such rows.

## 3. FORCE ROW LEVEL SECURITY

PostgreSQL exempts a table's owner from its policies unless the table is set to `FORCE
ROW LEVEL SECURITY`. Superusers and roles with BYPASSRLS are exempt either way (PG-RLS,
PG-AT). On Supabase the `postgres` role that owns migration-created tables and the
`service_role` both have BYPASSRLS (SB-RLS). `anon` and `authenticated` own nothing, so
policies already apply to every API request once RLS is enabled. Neither SB-RLS, SB-API
nor the Advisor's remediation for 0013 recommends FORCE [checked: FORCE changed nothing
for a superuser, and hid rows from a non-bypassing owner].

**Decision.** The renderer never emits FORCE. It would change nothing for the API roles
the findings are about, and it can break SECURITY DEFINER helpers owned by a role
without BYPASSRLS.

## 4. How Supabase's Security Advisor phrases the same issues

Names and levels are from each lint's SQL (SPL), which is what the Advisor runs. The
summaries are paraphrased from the Advisor docs (SB-ADV).

| Lint | Level | What it reports | offramp's equivalent |
|---|---|---|---|
| 0013 `rls_disabled_in_public` | ERROR | A table in an exposed schema without RLS: anyone with the project URL can read and change it. Fix: enable RLS. | `supabase.rls.disabled` (critical) |
| 0024 `rls_policy_always_true` | WARN | A permissive UPDATE, DELETE, ALL or INSERT policy for `public`, `anon` or `authenticated` whose condition is literally `true` (or `1=1`). SELECT `using (true)` is deliberately excluded by the SQL, although the docs page says otherwise. | `supabase.rls.permissive_write` (high; medium for INSERT) |
| 0007 `policy_exists_rls_disabled` | ERROR in the SQL, INFO on the docs page | Policies written, but RLS never enabled, so none of them applies. | reported as `rls.disabled`; see §6, pitfall 5 |
| 0008 `rls_enabled_no_policy` | INFO | RLS on, no policy: nothing is reachable through the API. The docs suggest an explicit `using (false)` if that is the intent. | the state an inert block deliberately does not create |
| 0006 `multiple_permissive_policies` | WARN (performance) | More than one permissive policy for the same role and command. `FOR ALL` expands to all four, and a policy with no `TO` counts for every role. | why a fix drops the offending policy instead of adding beside it |
| 0003 `auth_rls_initplan` | WARN (performance) | `auth.uid()` evaluated per row. Fix: `(select auth.uid())`. | the form the renderer emits |

`supabase.rls.public_read` has no Advisor equivalent: 0024 excludes SELECT on purpose,
because public reads are often intended. That matches RFC-0002's position that a public
read is a question, not a defect.

## 5. How Lovable-generated schemas identify an owner

Lovable's documentation says it creates RLS policies automatically when it builds
features that store user data, keeps schema changes as migrations in
`supabase/migrations/`, and runs a security scan that flags tables without RLS and
access rules that let everyone through (LV-DB, LV-SEC). It documents no ownership
convention. What follows is therefore measured, not quoted. It comes from replaying the
migrations of the 83 RLS-assessable Lovable apps in the recall corpus with the schema
detector added in this change (`detect/migrations.py`).

| Measure (83 apps, 1,008 tables in `public`, 1,936 policies) | Count |
|---|---|
| Apps with at least one foreign key to `auth.users` | 56 (67%) |
| Apps with a `profiles`-style table whose primary key references `auth.users` | 32 (39%) |
| Tables with a column referencing `auth.users.id` | 211 (21%) |
| … of which with two or more such columns (besides a primary key) | 15 |
| Tables reaching `auth.users` one hop through a `profiles`-style table | 113 |
| Tables with a `user_id`/`owner_id`/`created_by`/`author_id` column but no foreign key to `auth.users` | 171 |
| Owner foreign-key columns that are nullable | 57 |
| Tables whose columns the replay could not determine | 90 (9%) |
| Policy expressions calling `auth.uid()` | 1,587 |
| … of which wrapped as `(select auth.uid())` | 4 |
| Policies with no `TO` clause (so `public`) | 1,441 (74%) |
| Policies by command: SELECT / INSERT / UPDATE / DELETE / ALL | 769 / 411 / 301 / 162 / 293 |

Column names that reference `auth.users.id`, most common first: `user_id` (140),
a primary key (35, the `profiles` pattern), `created_by` (12), `assigned_to` (7),
`owner_id` (4), then a long tail of role-specific names (`buyer_id`, `approved_by`,
`invited_by`, `therapist_id`, …). Column names compared to `auth.uid()` by an existing
policy: `user_id` (541 expressions), `id` (78, again `profiles`), `owner_id` (19),
`author_id` (10), and a similar tail.

What this means for owner detection:

- **The two evidence bases in RFC-0003 cover the common shape.** Two in three apps
  declare a foreign key to `auth.users`, and the policy basis (`auth.uid() = <col>`) is
  nearly universal among apps that write any owner policy at all. *Later:* the precision
  run showed that a foreign key alone is not ownership evidence, because attribution
  columns reference users too. See [fix-precision.md](fix-precision.md).
- **Names are not evidence, and the corpus shows why.** `assigned_to`, `approved_by`
  and `invited_by` reference `auth.users` exactly as `user_id` does, but they name
  someone other than the owner. 15 tables have two or more such columns. That is the
  "multiple candidates" gap, and a name heuristic would pick one of them. Conversely,
  171 tables carry an owner-sounding name with no foreign key behind it. For those the
  name is a proposal for a human, never a policy.
- **`profiles` is owned by `id`.** Supabase's documented pattern (SB-USERS) and 35
  tables in the corpus use the primary key itself as the owner. RFC-0003 accepts that
  basis explicitly.
- **Almost nothing uses the wrapped form or a `TO` clause.** Existing policies are
  therefore not a style guide. The renderer writes Supabase's recommended form, not the
  corpus's habits.
- **Supabase's own example teaches the habits.** Its user-management starter (SB-EX)
  has a SELECT `using (true)` on `profiles`, no `TO` clauses and unwrapped
  `auth.uid()`: the shape the corpus reproduces.

## 6. Pitfalls: how a fix locks users out or leaves data open

**Left open.**

1. **A scoped policy added beside a `true` one.** Permissive policies are OR-ed (PG-RLS),
   so the table stays open. [checked] The fix must drop the offending policy, and RFC-0003
   checks by round trip that it did.
2. **A second permissive UPDATE policy with a weaker `with check`.** Checks of permissive
   policies are OR-ed as well. The owner check is only as strong as the weakest one.
3. **An explicit `with check (true)` on UPDATE** lets a user hand a row to someone else.
   Omitting `with check` does not: PostgreSQL then reuses `using` as the check (PG-CP).
   [checked] The renderer writes both clauses anyway, as SB-RLS recommends.
4. **Views and SECURITY DEFINER functions bypass RLS** (SB-RLS; Advisor lints 0010,
   0028, 0029). A fixed table can still leak through a view that selects from it. That is
   out of scope for the replay (views are not tracked) and is named in `FIXES.md`.
5. **Enabling RLS activates policies that were already written.** A table with RLS off
   and a `true` UPDATE policy (Advisor 0007) is open twice. Enabling RLS alone leaves it
   open through the policy. The fix must treat pre-existing permissive writes on that
   table as part of the same exposure.
6. **Grants.** Supabase is moving new projects away from automatic table grants to
   `anon` and `authenticated` (SB-RLS, SB-API). On a project with automatic grants, only
   policies hold `anon` back from writing, which is the exposure the findings describe.
   On a project without them, a policy is never reached until the role is granted the
   operation.
7. **`user_metadata` in a policy.** The user can edit it (SB-RLS; Advisor 0015). It is
   never evidence of ownership.

**Locked shut.**

8. **Enabling RLS with no policy** denies the app every row (PG-RLS; Advisor 0008). This
   is why an undetermined table gets an inert, commented block and not a bare `enable`.
9. **The wrong owner column.** For example, `assigned_to` is taken as the owner, so the
   creator of a row can no longer change it. No static check catches this. The defence
   is the evidence rule and the behavioural test on a real database.
10. **Owner-only reads on a table read publicly on purpose.** 44 of 83 apps declare a
    public read. This is why the fix preserves today's reads when it first enables RLS
    (RFC-0003, open question 1).
11. **Rows with a NULL owner** disappear from the app after the fix (§2).
12. **The app never sets the owner column** and it has no `default auth.uid()`. Every
    insert then fails the owner check. `FIXES.md` names this per table.
13. **A dropped policy before its replacement is created**, if the two statements are
    applied separately. The renderer writes each drop directly before its replacement,
    and `FIXES.md` says to apply the file as one migration. Supabase's migration tools
    apply a file as a unit, but that was not verified here.

## 7. What was not verified

- Whether Lovable-generated schemas follow a documented convention. Lovable's
  documentation is silent, and §5 is measured from the corpus instead.
- Whether the Supabase CLI applies each migration file in a single transaction.
- Which level the Advisor dashboard shows for lint 0007, where the SQL and the docs
  disagree.
- Whether `auth.uid()` stays executable by `anon` and `authenticated` on projects that
  revoke default function privileges.
