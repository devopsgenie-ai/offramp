# Row-level-security fixes: team-journal (lovable)

4 fixed · 5 need your answer · 1 left for your decision

`offramp fix` wrote `supabase/migrations/20260305120001_offramp_rls.sql` for you to review. It is a staging copy: nothing has been applied, and nothing in your repository or your database has changed.

## What the migration changes

### `public.comments`

- Closes: Policy "Anyone can do anything with comments" lets anyone change rows in `public.comments` (`supabase.rls.permissive_write.public.comments.anyone-can-do-anything-with-comments`)
- Removes the policy "Anyone can do anything with comments" (supabase/migrations/20260302100000_tables.sql:44). To undo, re-create it from that line.
- The owner of a row is the user in `profile_id`, because it references public.profiles.id, whose primary key references auth.users.id, and defaults to auth.uid().
- Signed-in users can add, change and delete only rows whose `profile_id` is their own.
- Reads are unchanged: the read half of "Anyone can do anything with comments" is kept as "offramp: anyone can read".

### `public.journal`

- Closes: Table `public.journal` has no row-level security (`supabase.rls.disabled.public.journal`)
- Turns row-level security on.
- The owner of a row is the user in `user_id`, because it references auth.users.id and defaults to auth.uid().
- Signed-in users can add, change and delete only rows whose `user_id` is their own.
- Reads are unchanged: anyone can still read every row, as today, through "offramp: anyone can read". The audit will now ask whether that is intended.

### `public.projects`

- Closes: Policy "Members can update projects" lets any signed-in user change rows in `public.projects` (`supabase.rls.permissive_write.public.projects.members-can-update-projects`)
- Removes the policy "Members can update projects" (supabase/migrations/20260302100000_tables.sql:96). To undo, re-create it from that line.
- The owner of a row is the user in `owner_id`, because policy "Owners can update projects" compares it to auth.uid() (supabase/migrations/20260302100000_tables.sql:93); and policy "Owners can view projects" compares it to auth.uid() (supabase/migrations/20260302100000_tables.sql:91); and it references auth.users.id.
- "Owners can update projects" already limits updates to the owner, so it is kept and nothing is added for update.

### `public.reminders`

- Closes: Table `public.reminders` has no row-level security (`supabase.rls.disabled.public.reminders`)
- Turns row-level security on.
- These policies were written earlier but do nothing while row-level security is off. They take effect now: "Users see their reminders" (supabase/migrations/20260302100000_tables.sql:26).
- The owner of a row is the user in `user_id`, because policy "Users see their reminders" compares it to auth.uid() (supabase/migrations/20260302100000_tables.sql:26).
- Signed-in users can add, change and delete only rows whose `user_id` is their own.
- Reads are unchanged: anyone can still read every row, as today, through "offramp: anyone can read". The audit will now ask whether that is intended.
- `user_id` allows NULL. A row with no owner becomes unreachable from the app. Count them before applying: `select count(*) from public.reminders where user_id is null;`
- `user_id` has no default of `auth.uid()`, so the app must set it to the signed-in user's id on every insert, or the insert is refused. `alter table public.reminders alter column user_id set default auth.uid();` does it for you.

## Needs your answer

### `public.assignments`

More than one column could be the owner, and nothing says which: `assignee_id` (it references auth.users.id); `created_by` (it references auth.users.id). `created_by` is proposed from its name alone, which is not evidence.

- Policy "Signed-in users can update assignments" lets any signed-in user change rows in `public.assignments` (`supabase.rls.permissive_write.public.assignments.signed-in-users-can-update-assignments`)
- Answer the gap `datastore.supabase.table.public.assignments.write_scope` with the owner column, or `server_only` if only your server writes to this table. Until then the migration holds only a commented-out block for it.

### `public.bookmarks`

`user_id` references auth.users.id, but nothing says the user it names is the one who writes the row: no policy compares it to auth.uid(), and it has no `default auth.uid()`. It is proposed, which is not evidence.

- Table `public.bookmarks` has no row-level security (`supabase.rls.disabled.public.bookmarks`)
- Answer the gap `datastore.supabase.table.public.bookmarks.write_scope` with the owner column, or `server_only` if only your server writes to this table. Until then the migration holds only a commented-out block for it.

### `public.drafts`

The policy that would prove the owner was written before a column it names was renamed, so the migrations no longer say which column it means: policy "Authors can read their drafts" names `author` (supabase/migrations/20260302100000_tables.sql:79). `author_id` is proposed from its name alone, which is not evidence.

- Policy "Anyone can delete drafts" lets anyone change rows in `public.drafts` (`supabase.rls.permissive_write.public.drafts.anyone-can-delete-drafts`)
- Answer the gap `datastore.supabase.table.public.drafts.write_scope` with the owner column, or `server_only` if only your server writes to this table. Until then the migration holds only a commented-out block for it.

### `public.invoices`

More than one column could be the owner, and nothing says which: `customer_id` (policy "Customers can view their invoices" compares it to auth.uid() (supabase/migrations/20260302100000_tables.sql:67)); `issuer_id` (it references auth.users.id).

- Policy "Anyone signed in can delete invoices" lets any signed-in user change rows in `public.invoices` (`supabase.rls.permissive_write.public.invoices.anyone-signed-in-can-delete-invoices`)
- Answer the gap `datastore.supabase.table.public.invoices.write_scope` with the owner column, or `server_only` if only your server writes to this table. Until then the migration holds only a commented-out block for it.

### `public.notes`

No column is proven to identify a row's owner. `user_id` is proposed from its name alone, which is not evidence.

- Table `public.notes` has no row-level security (`supabase.rls.disabled.public.notes`)
- Answer the gap `datastore.supabase.table.public.notes.write_scope` with the owner column, or `server_only` if only your server writes to this table. Until then the migration holds only a commented-out block for it.

## Deliberately not changed

- Policy "Anyone can join the waitlist" lets anyone add rows in `public.waitlist` (`supabase.rls.permissive_write.public.waitlist.anyone-can-join-the-waitlist`). Anyone may add rows. That is usually a form, a waitlist or a contact form, so it is a decision for you: consider rate limits and what a row may contain.

## Applying it

1. Read `supabase/migrations/20260305120001_offramp_rls.sql` block by block. Each says what it closes and why.
2. Copy it, unchanged, into your repository's `supabase/migrations/`.
3. Apply it through your project's usual migration process, as one migration, so no table is ever between a dropped policy and its replacement.
4. Re-run `audit`. Every finding listed as fixed must be gone. Re-run `fix` as well: it writes a new file only for what remains.

## What this cannot see

- **Grants.** Policies decide which rows; grants decide whether a role may run the operation at all. On a project that no longer grants table access to `authenticated` by default, the owner policies are only reached once that grant exists.
- **Views and security-definer functions.** Both can read a table without its policies. A view over a fixed table needs `security_invoker`.
- **Policies made in the Supabase dashboard.** They never reach `supabase/migrations/`. Run the dashboard's Security Advisor after applying.

---

Generated by [offramp](https://github.com/devopsgenie-ai/offramp) fix. Re-run it on the same commit to reproduce these files.
