# Precision of `fix` on the recall corpus

RFC-0003, Testing, "Precision on the corpus before any claim": run `fix` over the
RLS-assessable Lovable apps in the recall corpus, report in aggregate how many RLS
findings got an executable fix and how many got a gap, and hand-label a sample of
detected owner columns by reading how each app writes those rows. *"A single wrong owner
in the sample blocks release until the evidence rule is tightened."*

Run 2026-09-28 on the 125 Lovable repositories of RFC-0002's corpus (#16). As RFC-0002
requires, no repository is named here. Per-app detail stayed in local scratch.

**Result: the evidence rule as RFC-0003 wrote it failed the gate.** Ten of the twelve
tables it fixed had a wrong owner. A tightened rule, proposed below and implemented in
this change, fixes two tables, both with correct owners. The README still does not
mention `fix`. Release stays blocked until the reviewer accepts the tightened rule (or
a stricter one) into RFC-0003.

## Run 1: the rule as written

The foreign-key basis accepts any column that references `auth.users.id`, directly or one
hop through a `profiles`-style table.

| | |
|---|---|
| Lovable apps with at least one RLS finding | 58 |
| RLS findings | 478 |
| … fixed | 13 (10 critical `rls.disabled`, 3 high `permissive_write`) |
| … gap (no owner proven) | 242 |
| … left for a decision (public reads, anonymous inserts) | 223 |
| Tables fixed | 12, in 3 apps (56 statements) |
| Round-trip refusals | 0 once the version bug below was fixed |

**Hand labels: all 12 fixed tables, each checked against the app's code for how its rows
are written.**

| Tables | Owner the rule chose | What the app does | Label |
|---|---|---|---|
| 9, in one ERP-style app | `created_by`, `requested_by`, `return_by` (foreign keys to `profiles` or `auth.users`) | Never sets these columns. They are nullable, have no default, and one defaults to `gen_random_uuid()`. Any staff member edits rows by `id`. | **Wrong.** After the fix every insert fails and no existing row can be edited: a total lockout. |
| 1 | a roles table's `user_id` | Rows are written from an admin screen, for other users. | **Wrong.** The admin is locked out, and a user could still edit their own role row. |
| 1 | `profiles.id` | Users update their own profile, and the app's own (inactive) policies say so. | Correct. |
| 1 | a subscriptions table's `user_id` | Written by server functions for the signed-in user. | Correct owner. |

The cause is the same in all ten wrong cases. A foreign key to `auth.users` proves a
column *names* a user. It does not prove that user *writes* the row. Attribution columns
(`created_by`, `requested_by`), assignment columns and admin-managed tables all reference
users.

## The tightened rule

A column is an owner only when the author has declared it holds the signed-in writer:

1. **Policy basis** (unchanged). An existing policy compares the column, and nothing else,
   to `auth.uid()`.
2. **The row is the user** (unchanged, now its own basis). The table's primary key
   references `auth.users.id`, as in Supabase's `profiles` pattern.
3. **Foreign-key basis, tightened.** The column references `auth.users.id` (directly, or
   one hop through a `profiles`-style table) **and** defaults to `auth.uid()`. The database
   then records whoever inserts the row as its owner.

A foreign key without the default is still a *candidate*. It can contradict another
basis, and it becomes the gap's proposed answer at medium confidence, but it is never
rendered. This change also recognises pg_dump's spelling of the policy basis,
`"auth"."uid"()` and `( SELECT "auth"."uid"() AS "uid")`. Without it, one app's existing
owner policies were invisible, and the fix would have duplicated them.

## Run 2: the tightened rule

| | |
|---|---|
| RLS findings fixed | 2 (1 critical, 1 high) |
| … gap | 253 |
| … left for a decision | 223 |
| Tables fixed | 2, in 2 apps (5 statements) |
| Round-trip refusals | 0 |

Both fixed tables have a correct owner:

- **`profiles`** is the ERP-style app's table with RLS never enabled. The app's own
  inactive policies already scope insert and update to the owner, so the fix enables
  RLS, reuses them, and adds only an owner-scoped DELETE and the preserved read.
  Note: enabling RLS activates the app's own owner-only UPDATE policy, so the app's
  admin screen that edits other users' profiles stops working. That is the app's
  declared policy taking effect, and `FIXES.md` names every policy that will activate.
- **A subscriptions table** has an UPDATE `true` policy. It is dropped and replaced by an
  owner-scoped UPDATE. The owner is right, and the server functions that write it
  write the signed-in user's row, so there is no lockout.

## Sample of detected owners (any table)

With only two tables fixed, the owner detector itself was also labelled. There were 12
tables with a detected owner, one per app, sampled at random from 282 detections across
78 apps. For each, the app's code was read for how rows are written.

| Label | Tables |
|---|---|
| Correct, and the app writes as the owner (wishlists, posts, devices, profiles) | 5 |
| Correct by the table's own owner policies; no client or function writes to read | 3 |
| Correct owner, but a server writes the rows (a points ledger, affiliate conversions) | 2 |
| The column is the row's subject, not its writer (notifications inserted *for* another user; a roles table written by admins) | 2 |

The last two rows are the limit of the tightened rule. A policy that lets an owner
*read* (`"Users can view their own notifications"`) proves who the row is about, not
who writes it. Run 2 fixed neither table, because neither is open. Had either been
open, an owner-scoped replacement would have broken the app's cross-user inserts
(notifications) or let a user write their own ledger rows. A second tightening would
close this: accept the policy basis for a command only when the existing policy is
itself for a write command. It is proposed as an open question on RFC-0003, not
implemented, because it would also make the RFC's own example (`tasks`, owned through a
SELECT policy) a gap.

## A defect the run found

The first run refused four apps. Two of them had nothing to fix, because the version was
resolved before knowing whether a migration would be written. In the other two,
`latest + 1` did not sort last by file name: a repository mixed version widths, such as
`20260127_x.sql` after `20260118172347_y.sql`, and migrations run in file-name order. Both
are fixed here. The version is now resolved only when a migration is written, and the
default is the smallest version as wide as the latest that sorts after every file.

## Conclusion

The fix is safe but rarely confident. Of 478 RLS findings in 58 apps, 2 get an
executable fix, 253 get a gap, and 223 are decisions the audit keeps asking about. That
is the conservative end of the trade RFC-0003 argues for: *"an evidence rule strict
enough that the fix is rarely confident"*. The yield rises only when a human answers the
gaps, which needs RFC-0001's `apply`. It must not rise by relaxing the evidence rule
again.
