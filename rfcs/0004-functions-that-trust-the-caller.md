---
rfc: 0004
title: "Audit check: database functions that trust a caller-supplied user id"
status: accepted
authors: [devyansh-dg]
created: 2026-09-28
updated: 2026-10-02
---

> Adds one audit check, `supabase.function.trusts_caller_id`. It finds `SECURITY DEFINER`
> functions in an API-exposed schema (`public` by default) that anyone with the app's
> public key can call, that take a user id as an argument, and that write rows keyed by it
> without checking that the caller is that user. The migration replay learns functions and
> `EXECUTE` grants to support it. Nothing else changes.

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

RFC-0003 moved the migration replay to `detect/` (#22), and its result is
`Datastore.schema`. This RFC adds two things to that `Schema`: the functions the
migrations define, and who may execute them.

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
  owner         str?              # null: the role that ran the migrations, which owns
                                  # the tables and bypasses their RLS
  grants        [str]             # who holds EXECUTE: any of "public", "anon",
                                  # "authenticated"
  executable_by [str]             # derived from grants: "anon" when "public" or "anon"
                                  # holds EXECUTE, "authenticated" when "public" or
                                  # "authenticated" does. Every role inherits "public"
  writes        [Write]?          # null when the body could not be read (see below)
  compares_caller [str]           # params the body compares with the caller:
                                  # auth.uid() with =, <>, != or is [not] distinct from
  role_check    bool              # the body passes auth.uid() to a call (has_role,
                                  # is_admin, ...), or reads auth.role() or auth.jwt()
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
  language is recorded with `writes: null`. A `language sql` body written as
  `begin atomic … end` (PostgreSQL 14 and later) is not a string. The statement splitter
  keeps it whole, despite the `;` inside it, and the body is recorded with `writes: null`.
- `drop function`
- `alter function … security definer | security invoker | rename to | set schema |
  owner to`
- `grant execute` / `revoke execute` on a named function
- `grant execute` / `revoke execute on all functions in schema <s>`. This applies to the
  functions that exist when it runs. Supabase migrations often use it to lock functions
  down in bulk.
- `alter default privileges [for role …] [in schema …] grant | revoke execute on
  functions`. This applies to functions created after it, which is how PostgreSQL
  behaves.

**Who may execute a function** is tracked per grantee, because PostgreSQL's rules make
`public` special. Every new function starts with EXECUTE granted to `public` (PostgreSQL's
built-in default), and to `anon` and `authenticated` (Supabase's default privileges). So
`grants` starts as `["public", "anon", "authenticated"]`, adjusted by any `alter default
privileges` seen earlier. Every role inherits what `public` holds. Revoking from `anon`
alone therefore leaves anonymous callers able to execute the function through `public`.
Revoking from `public` alone leaves Supabase's explicit grants in place. Only revoking from
both `public` and a role takes that role away. This is why Supabase's own guidance revokes
from `public` and `anon` together.

**Grant lifetime follows PostgreSQL.** `create or replace function` keeps the function's
existing grants and owner. `drop function` followed by `create function` starts again
from the defaults.

Overloads are distinct functions keyed by `signature`. A `grant`, `revoke`, `alter` or
`drop` that names no argument list applies to the one function with that name. If there
are several, the statement is not understood and `functions` is null.

**Reading a body is deliberately shallow.** The replay does not parse PL/pgSQL. It masks
nested strings and comments, as it already does for statements. It then finds each
`insert into`, `update … set` and `delete from` up to the next top-level `;`, and records
which parameters appear in it: by name as whole words, or by position as `$1`, `$2` and
so on, which `language sql` bodies often use. A body containing `execute` (dynamic SQL),
`format(` feeding `execute`, or a call to `dblink` is recorded with `writes: null`. The
checker reports a function it could not read as the reason for `could_not_assess`, and
never counts it as clean.

**Asking who the caller is** is read the same shallow way, and it is narrower than
mentioning the caller. `compares_caller` holds each parameter the body compares with
`auth.uid()`. A variable the body assigns from `auth.uid()` (`v_uid := auth.uid()`, or
`select auth.uid() into v_uid`) stands for the caller in those comparisons. `role_check`
is true when the body passes `auth.uid()` to another function, the way a role check such
as `has_role(auth.uid(), 'admin')` does, or reads `auth.role()` or `auth.jwt()`. A body
that only tests `auth.uid() is null`, or records `auth.uid()` as the actor, has asked
whether someone is signed in, not whether they may act on the row. That is the commonest
way these functions go wrong, and it clears nothing.

A function body runs later, not at migration time, so bodies are never *replayed*. They
are only read. Nothing about the tables changes. This RFC does not alter what the replay
records for tables, policies and RLS (RFC-0002's statement forms), or the column tracking
RFC-0003 added.

Function statements follow the rule RFC-0003's replay settled for columns (#22). A
statement the replay does not understand makes `functions` null and nothing else. It
never makes the migration unreplayable, so it never costs the RLS checks their answer.
Inside a `DO` block, a function statement under an existence guard is read as written,
as policies and column changes are. One behind dynamic SQL, or under a condition that is
not an existence check, sets `functions` to null, because its effect cannot be known
without running it.

### The check

`supabase.function.trusts_caller_id` reports a function when all of these hold:

1. it is in a schema the API exposes, `definer` is true, `trigger` is false and `owner`
   is null;
2. `executable_by` is not empty;
3. one of its parameters is a **user identifier** (see below);
4. `writes` contains a statement whose `uses` includes that parameter;
5. that parameter is not in `compares_caller`, and `role_check` is false;
6. no parameter is named like a capability. These are `token`, `code`, `secret`, `otp`,
   `key`, `hash`, `nonce`, or names containing them. An invitation or one-time-code flow
   is how a caller legitimately acts before they have a session, and the corpus showed
   these are intended.

**The schemas the API exposes** are read from `supabase/config.toml`, its `[api] schemas`
list, when the repository has one. Otherwise only `public` counts, which is Supabase's
default. A function in any other schema cannot be called over `/rest/v1/rpc`.

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

Condition 5 is stricter than the rule behind the numbers in *Problem*. That rule cleared
a function whose body mentioned `auth.uid()` at all. So the implementing PR's precision
run reports separately the findings that only the stricter condition adds.

**Severity follows the write, never a table name:**

- **high** if a reported `update` or `delete` uses the parameter. The caller can change or
  remove another user's rows.
- **medium** if only `insert`s do. The caller can create rows attributed to another
  user. That is sometimes harmless, and sometimes a role grant. The finding asks, and its
  text names the second case outright: *"For example, if this adds a role or permission,
  anyone can grant it to any user, themselves included."* A role grant is reported
  either way. See Open question 1.

The **finding**, for `public.add_points(p_user_id, p_amount)`:

```
### supabase.function.trusts_caller_id.public.add-points.uuid-integer
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

The finding's id ends with the function's argument types, because overloads share a name
and are distinct functions. `subject` is `{"function": "public.add_points(uuid,
integer)"}`, the signature, so a later renderer can join the finding to its function
without parsing an id that `dns_label` made lossy. That is RFC-0003's reason for
`subject`.

The remedy names no vendor. It gives two concrete fixes, and the user picks the one that
matches their intent. When `executable_by` is `["authenticated"]`, meaning neither
`public` nor `anon` holds EXECUTE, the text says "any signed-in user" instead of "anyone".
The severity does not change: the caller still chooses the victim.

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

RFC-0003's first step, #22, made the replay a detector with a `Schema`, so nothing
blocks this.

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
- **`fix` (RFC-0003) does not change here, but its claims depend on this check.** A
  definer function that writes a table goes around the owner policies `fix` writes for
  it. `FIXES.md` already names security-definer functions under *What this cannot see*.
  Once this check exists, a follow-up to RFC-0003's renderer should name, for each table
  it fixes, the reported functions that write that table. That follow-up is a change to
  `fix`, in its own PR.

Someone who ran `audit` before sees one more line in "Checked and clean", or a new
finding if their app has the problem.

## Testing

- **A new fixture, `lovable-rpc-functions`**, with a hand-written `truth.yaml` listing
  its findings exactly. It carries the awkward cases on purpose:
  - an `UPDATE` keyed by `p_user_id`, which fires high;
  - an `INSERT`-only function, which fires medium;
  - the same shape comparing `p_user_id` with `auth.uid()` before writing, which is
    clean; the same through a variable assigned `auth.uid()`, which is clean; and one
    gated by `has_role(auth.uid(), 'admin')`, which is clean;
  - the same shape that only checks `auth.uid() is not null`, or records `auth.uid()` as
    the actor, which still fires;
  - the same shape with `EXECUTE` revoked from `anon`, `authenticated` and `public`,
    which is clean;
  - revoked from `public` only, which still fires with "anyone", because Supabase's
    explicit grants survive;
  - revoked from `anon` only, which still fires with "anyone", because `anon` inherits
    `public`'s grant; and revoked from `anon` and `public`, which fires with "any
    signed-in user";
  - `revoke execute on all functions in schema public from anon, authenticated, public`
    after a function, which makes it clean; and a function created after that revoke,
    which fires, because the revoke reached only the functions that existed;
  - a function whose revoke is followed by `create or replace`, which stays clean; and
    one that is dropped and created again, which fires, because its grants start again;
  - a `language sql` function that writes using `$1` for the user id, which fires;
  - a `language sql` function written with `begin atomic`, which is `could_not_assess`;
  - a trigger function that reads `new.user_id`, which is ignored;
  - a function with a `p_token` parameter, which is ignored;
  - a function redefined in a later migration to use `auth.uid()`, which is clean;
  - a function that is later dropped, which is clean;
  - a `SECURITY INVOKER` function, which is ignored;
  - a function whose owner was changed with `alter function … owner to`, which is
    ignored;
  - a function in a schema the API does not expose, which is ignored; and one in a
    second schema that `supabase/config.toml` exposes, which fires;
  - two reported overloads of one name, with distinct finding ids;
  - an `alter default privileges … revoke execute on functions from anon, authenticated,
    public` before a function, which is clean, and one after it, which fires;
  - two overloads of one name with an unqualified `revoke`, which makes `functions`
    null and the check `could_not_assess`, while the RLS checks keep their answer;
  - a guarded `create function` inside a `DO` block, which is read; and one built with
    `execute`, which makes `functions` null.
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
- **Definer functions owned by a role that does not bypass RLS.** After `alter function
  … owner to` such a role, RLS applies inside the function, and whether a write gets
  through depends on that role's policies. Such a function is not reported. This is rare
  in generated code.
- **Callers checked by means the shallow reading cannot see**, such as a comparison
  through a second variable, or a check in a function it calls. These functions are
  reported. A false positive like this is what the precision run measures.
- **Fixing it.** RFC-0003's renderer could learn to emit the `revoke`, and that is a
  later RFC.

## Open questions

1. **Role-grant inserts.** An `INSERT` into `user_roles` keyed by `p_user_id` is critical
   in practice, but this RFC rates it medium because severity follows the verb. Raising it
   would need a table-name list, which RFC-0003 refuses as evidence for ownership. Is a
   name acceptable as evidence for *severity* only, when the finding fires regardless? In
   the corpus that would have moved 8 more functions to high: 6 clearly harmful, 1 low
   impact and 1 harmless.

   **Decided: no.** Severity follows the write verb, and no table name raises it. This
   keeps RFC-0003's rule that a name is never evidence, for severity as well as for
   ownership. Nothing is lost by it: a role grant keyed by a caller-chosen id is still
   reported, and the medium finding's text names the role-grant case outright (see
   *The check*). The reader learns of it either way.
2. **`authenticated`-only functions.** They are reported at the same severity as
   anonymous ones, because the caller still picks the victim. Should "any signed-in user"
   be one step lower when sign-up is closed? The tool cannot see that setting.

   **Decided: same severity.** Whether sign-up is open is a project setting the
   repository does not hold, and the tool does not assume what it cannot see. In the apps
   this check targets, sign-up is usually open, so "any signed-in user" is anyone who
   registers. The finding's text still says "any signed-in user", so a reader whose
   sign-up is closed can weigh it themselves.
3. **The user-identifier list.** Is a name list, confirmed against the corpus and kept in
   `known.py`, the right governance? The alternative is any `uuid` parameter used as a
   write key. It is broader, and it would report `p_project_id`, which is often a real
   finding too (joining another tenant's project), and often not.

   **Decided: the name list**, kept in `known.py`, each entry confirmed against the
   corpus before it is added. The precision in *Problem* was measured with this list, and
   a wider rule would need its own measurement. Following any `uuid` parameter used as a
   write key, which would catch tenant-crossing functions such as `join_project`, is
   left to a later RFC that measures it.
