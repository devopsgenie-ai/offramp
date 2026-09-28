---
rfc: 0004
title: "Audit check: database functions that trust a caller-supplied user id"
status: draft
authors: [devyansh-dg]
created: 2026-09-28
updated: 2026-09-28
---

> Adds one audit check, `supabase.function.trusts_caller_id`. It finds `SECURITY DEFINER`
> functions in `public` that anyone with the app's public key can call, that take a user id
> as an argument, and that write rows keyed by it without asking the database who the
> caller is. The migration replay learns functions and `EXECUTE` grants to support it.
> Nothing else changes.

## Problem

`audit` reads tables and policies. It does not read functions, and in the modal Lovable
app functions are a second, larger door around row-level security.

Three facts about Supabase make that door open by default:

- **Every function in `public` is callable over the API.** `POST /rest/v1/rpc/<name>` with
  the public key runs it. PostgreSQL grants `EXECUTE` to `PUBLIC` on every new function,
  and Supabase's default privileges grant it to `anon` and `authenticated` explicitly.
  Supabase's own advisor has lints for exactly this: `anon_security_definer_function_executable`
  (0028) and `authenticated_security_definer_function_executable` (0029).
- **`SECURITY DEFINER` runs as the function's owner.** In Supabase that owner is
  `postgres`, which bypasses RLS. Policies on the tables the function writes do not apply.
- **Nothing in the function knows who called it** unless it asks, with `auth.uid()` or
  `auth.jwt()`. A parameter named `p_user_id` is whatever the caller sends.

Put together, `add_points(p_user_id uuid, p_amount int)` declared `SECURITY DEFINER` lets
anyone who has loaded the site add points to any account. So does `assign_role`,
`remove_member`, `deduct_balance`, `upsert_push_subscription` and `create_session`. This
is the pattern generated code reaches for when an RLS policy got in the way: the
function "fixes" the permission error by stepping around RLS, and takes the user from the
client because that is where it was.

The evidence is the recall corpus from RFC-0002, 125 Lovable and 65 Emergent public
repositories, reported in aggregate only. A throwaway detector built to this RFC's rule
(below) and run on 2026-09-28 found:

| | Apps |
|---|---|
| Lovable apps with migrations in the repository | 100 |
| with at least one function this check would report | **14** |
| Functions reported, in total | 47 |

Every one of the 47 was labelled by hand against its source:

| Tier | Functions | Clearly harmful | Low impact but true | Harmless |
|---|---|---|---|---|
| **high**: an `UPDATE` or `DELETE` keyed by the id parameter | 25 | 20 | 5 | 0 |
| **medium**: only `INSERT`s keyed by it | 22 | 13 | 4 | 5 |

"Clearly harmful", in aggregate, means:

- granting an admin role to any user
- adding to, deducting from or overwriting any user's balance, credits or points
- joining or leaving any project or company
- rewriting another user's profile email and phone
- rewriting payment vouchers
- registering a push subscription on another user's account, so their notifications go
  to the caller
- minting a session token for any user id

"Harmless" means something like creating a default preferences row. In 11 of the 14 apps
at least one reported function is clearly harmful.

Two looser rules were measured and rejected; see *Alternatives considered*.

A user today cannot find this out. The audit reports their tables as clean. Supabase's
advisor would flag every `SECURITY DEFINER` function it can see, but it needs a database
connection, and it cannot tell the helper `has_role(uid, role)`, which is safe to expose,
from `add_points(p_user_id, n)`, which is not.

## Proposal

### The replay learns functions

RFC-0003 moves the migration replay to `detect/` and records its result as
`Datastore.schema`. This RFC adds two things to that `Schema`: the functions defined in
`public`, and who may execute them.

```
Schema
  ...
  functions     [Function]?       # null when a statement touching functions was not
                                  # understood. Tracked apart from tables, so an
                                  # unparsed function never costs the RLS checks
                                  # their answer, and vice versa

Function
  name          str               # "public.add_points"
  signature     str               # "public.add_points(uuid, integer)"; identity
  params        [str]             # names, in order; "" for an unnamed parameter
  definer       bool              # SECURITY DEFINER
  trigger       bool              # RETURNS trigger: not callable over the API
  executable_by [str]             # subset of ["anon", "authenticated"]
  writes        [Write]?          # null when the body could not be read (see below)
  asks_caller   bool              # the body references auth.uid(), auth.jwt() or
                                  # auth.role(), or current_setting('request.jwt...')
  evidence      str               # path:line of the defining statement

Write
  verb          insert | update | delete
  table         str               # as written, schema-qualified when it was
  uses          [str]             # which params appear in the statement
  line          int               # line in the migration file
```

The replay tracks these statements, in filename order as today:

- `create [or replace] function` in any clause order. The body is read from its
  dollar-quoted or single-quoted string. `language sql` and `plpgsql` are read; any other
  language is recorded with `writes: null`.
- `drop function`
- `alter function … security definer | security invoker | rename to | set schema`
- `grant execute` / `revoke execute` on a named function
- `alter default privileges … grant | revoke execute on functions`. This changes
  `executable_by` for functions created after it, which is how PostgreSQL behaves.

`executable_by` starts as `["anon", "authenticated"]`, the Supabase default. A revoke from
`public` alone does not remove it: Supabase's grants to `anon` and `authenticated` are
explicit, and they survive. Overloads are distinct functions keyed by `signature`. A
`grant` or `revoke` that names no argument list applies to the one function with that
name, and it is unparseable if there are several.

**Reading a body is deliberately shallow.** The replay does not parse PL/pgSQL. It masks
nested strings and comments, as it already does for statements. It then finds each
`insert into`, `update … set` and `delete from` up to the next top-level `;`, and records
which parameter names appear in it as whole words. A body containing `execute` (dynamic
SQL), `format(` feeding `execute`, or a call to `dblink` is recorded with `writes: null`.
The checker reports a function it could not read as the reason for `could_not_assess`,
and never counts it as clean.

A function body runs later, not at migration time, so bodies are never *replayed*. They
are only read. Nothing about the tables changes. This RFC does not alter the six statement
forms RFC-0002's replay understands for tables, policies and RLS.

### The check

`supabase.function.trusts_caller_id` reports a function when all of these hold:

1. it is in `public`, `definer` is true and `trigger` is false;
2. `executable_by` is not empty;
3. one of its parameters is a **user identifier** (see below);
4. `writes` contains a statement whose `uses` includes that parameter;
5. `asks_caller` is false;
6. no parameter is named like a capability. These are `token`, `code`, `secret`, `otp`,
   `key`, `hash`, `nonce`, or names containing them. An invitation or one-time-code flow
   is how a caller legitimately acts before they have a session, and the corpus showed
   these are intended.

A **user identifier** is a parameter whose name, once `p_`, `_`, `in_` or `target_` is
removed, is `user_id`, `userid`, `uid`, or one of `admin`, `owner`, `member`,
`profile`, `customer`, `account`, `player`, `student`, `client` followed by `_id` or
`id`. The list is data, in `checks/known.py`, and every entry is confirmed against the
corpus before it is added, by RFC-0002's rule.

This is the one place the check uses a name as evidence, and it is honest about it. A
name decides only *which parameter* to follow. What the check claims, "this function
writes rows keyed by an argument the caller chooses, and never asks who the caller is", is
true of every function it reports whatever the name means. Precision above is measured
with exactly this list.

**Severity follows the write, never a table name:**

- **high** if a reported `update` or `delete` uses the parameter. The caller can change or
  remove another user's rows.
- **medium** if only `insert`s do. The caller can create rows attributed to another
  user. That is sometimes harmless, and sometimes a role grant; the finding asks.

The **finding**, for `public.add_points(p_user_id, p_amount)`:

```
### supabase.function.trusts_caller_id.public.add-points
Anyone can call `public.add_points` and choose whose rows it changes
`public.add_points` runs with full database rights (SECURITY DEFINER), so row-level
security does not apply inside it. Anyone with the app's public key can call it, and it
updates `user_points` for whatever `p_user_id` the caller sends. It never checks who the
caller is.
- evidence: `supabase/migrations/20260101000000_points.sql:12` (definition),
  `…:18` (UPDATE using p_user_id)
- remedy: inside the function, use the signed-in user (`auth.uid()`) instead of taking
  the user id as an argument. If only your server should call it, revoke EXECUTE on it
  from `anon`, `authenticated` and `public`.
```

The remedy names no vendor. It gives two concrete fixes, and the user picks the one that
matches their intent. When `executable_by` is `["authenticated"]` the text says "any
signed-in user" instead of "anyone", and the severity does not change: the caller still
chooses the victim.

**Assessment**, in the order RFC-0002 checks for the RLS checks:

| Condition | Status |
|---|---|
| the app does not use Supabase | `not_applicable` |
| no migrations; migrations unordered; `functions` is null (a function statement was not understood) | `could_not_assess`, with the reason |
| some candidate function has `writes: null` | `could_not_assess`, naming the first such function. It is never `clean`: an unread body might be the one |
| otherwise | `found` or `clean` |

A candidate is any function that passes conditions 1–3, 5 and 6. So an unreadable body
blocks a `clean` only when the function could have been reported.

### Build order

This lands after RFC-0003's first step, #22, which makes the replay a detector with a
`Schema`.

1. `Function` and `Write` in the `Schema`; the replay reads function statements and
   grants.
2. The check, its registration, and `known.py` entries.
3. The fixture and goldens below, then the corpus precision run, reported in aggregate in
   the PR.

## Alternatives considered

**Report every `SECURITY DEFINER` function callable by `anon`**, as Supabase lint 0028
does. It is the simplest rule and it is exactly right about reachability. It lost on
precision. It reports the RLS helpers that the recommended pattern creates on purpose,
such as `has_role(uid, role)`, and the report would teach its reader to ignore the
section. It is also Supabase's advisor's job, and the report already points to it.

**Report definer functions that write and never ask for `auth.uid()`**, without the
user-identifier condition. Measured: 24 of 100 apps, with about half of the sampled hits
clearly harmful. The rest were maintenance functions with no arguments, such as purging
expired links, telemetry inserts, and recomputing a counter. The parameter condition is
what turns "can be called" into "can be aimed at someone".

**Include edge functions** (`supabase/functions/*`) that use the service-role key
without checking the caller. It is the same failure through a different door, and it is
at least as common (17 of 125 Lovable apps in the same measurement). It lost on scope.
Detecting it means reading TypeScript, not the SQL this replay already reads. Its
evidence, precision and failure modes are different enough to deserve their own review,
so it is its own RFC.

**Probe the live database** by calling the function with a test id. It is rejected for
RFC-0002's reasons: network, a live system, and consent the tool cannot establish.

**Do nothing, and rely on the "not visible from the repository" section's pointer to
Supabase's advisor.** That pointer stays, because functions created in the dashboard are
invisible here. It is not enough on its own. The advisor cannot rank these above the
dozens of helper functions it also flags, and a user who has not connected it gets a
report that calls their app clean.

## Impact on generated output

Additive.

- **`expected_audit/report.json` and `report.md`, every fixture.** Each report gains one
  assessment for the new check. It is `not_applicable` for the two Emergent fixtures, and
  `clean` or `could_not_assess` for the existing Lovable fixtures, with no findings, since
  none of them defines such a function. `report.md`'s check count moves by one.
- **`expected_bare/appspec.json`**, for every fixture with a replayed `Schema`: it gains
  `functions`. The RLS checks read the same `Schema` and their output does not change.
- **Gap counts do not move.** A finding is not a gap, per RFC-0002.

Someone who ran `audit` before sees one more line in "Checked and clean", or a new
finding if their app has the problem.

## Testing

- **A new fixture, `lovable-rpc-functions`**, with a hand-written `truth.yaml` listing
  its findings exactly. It carries the awkward cases on purpose:
  - an `UPDATE` keyed by `p_user_id`, which fires high;
  - an `INSERT`-only function, which fires medium;
  - the same shape with `auth.uid()` in the body, which is clean;
  - the same shape with `EXECUTE` revoked from `anon`, `authenticated` and `public`,
    which is clean;
  - revoked from `public` only, which still fires, because Supabase's explicit grants
    survive;
  - revoked from `anon` only, which fires with "any signed-in user";
  - a trigger function that reads `new.user_id`, which is ignored;
  - a function with a `p_token` parameter, which is ignored;
  - a function redefined in a later migration to use `auth.uid()`, which is clean;
  - a function that is later dropped, which is clean;
  - a `SECURITY INVOKER` function, which is ignored;
  - a function in a non-`public` schema, which is ignored;
  - an `alter default privileges … revoke execute on functions from anon, authenticated,
    public` before a function, which is clean, and one after it, which fires;
  - two overloads of one name with an unqualified `revoke`, which makes the replay
    unparseable and the check `could_not_assess`.
- **A second fixture, or a variant, whose only candidate uses `execute format(...)`.**
  The check must report `could_not_assess`, never `clean`. This guards the tempting bug
  RFC-0002 names.
- **Every check fires somewhere and stays quiet somewhere.** This is the existing test,
  now covering the new check.
- **Values never appear.** Function bodies are code, not secrets, but the evidence rule
  still holds: `path:line`, the function signature and parameter names, never body text.
- **Corpus precision, before the README mentions the check.** The implementing PR re-runs
  the check on the recall corpus, in aggregate only. It reports apps and functions per
  tier, and a hand-labelled sample of at least 20 findings per tier. A high-tier false
  positive, meaning a function whose write could not affect another user, blocks the
  release until the rule is tightened. The numbers in *Problem* are the baseline, and they
  came from a throwaway detector, not from this implementation.

## Declarative constraint

This proposal complies. The check reads files through `walk.py` and the replay. It opens
no database connection, makes no network request, and needs no credential. Its remedy is
text for a human to act on. Generating the migration that revokes `EXECUTE` or rewrites
the function is a renderer, and it is out of scope here.

## Out of scope

- **Edge functions** that use the service-role key without checking the caller. They are
  a separate RFC; see *Alternatives considered*.
- **Definer functions that only read.** They can leak rows past RLS. In the corpus, about
  7 in 10 of those sampled returned data that was public or a role lookup, and telling
  the rest apart needs knowing what the rows mean.
- **`SECURITY INVOKER` functions.** RLS applies inside them, so they are covered by the
  existing policy checks.
- **A mutable `search_path`** on definer functions (lint 0011). It is hardening: exploiting
  it needs `CREATE` on a schema in the path, which the API roles do not have.
- **Functions created in the dashboard.** They are invisible, as tables are, and the "not
  visible from the repository" section already says so.
- **Following calls.** A definer function that passes `p_user_id` to another function
  that writes is not reported. That is a false negative, accepted for a replay that does
  not parse PL/pgSQL.
- **Fixing it.** RFC-0003's renderer could learn to emit the `revoke`, and that is a
  later RFC.

## Open questions

1. **Role-grant inserts.** An `INSERT` into `user_roles` keyed by `p_user_id` is critical
   in practice, but this RFC rates it medium because severity follows the verb. Raising it
   would need a table-name list, which RFC-0003 refuses as evidence for ownership. Is a
   name acceptable as evidence for *severity* only, when the finding fires regardless? In
   the corpus that would have moved 8 more functions to high: 6 clearly harmful, 1 low
   impact and 1 harmless.
2. **`authenticated`-only functions.** They are reported at the same severity as
   anonymous ones, because the caller still picks the victim. Should "any signed-in user"
   be one step lower when sign-up is closed? The tool cannot see that setting.
3. **The user-identifier list.** Is a name list, confirmed against the corpus and kept in
   `known.py`, the right governance? The alternative is any `uuid` parameter used as a
   write key. It is broader, and it would report `p_project_id`, which is often a real
   finding too (joining another tenant's project), and often not.
