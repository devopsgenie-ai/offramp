-- Each table below carries one awkward case for the fix renderer (RFC-0003, Testing).

-- RLS never enabled; the owner is an inline reference to auth.users with a default.
-- A full fix.
CREATE TABLE public.journal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id),
  entry TEXT NOT NULL
);

-- RLS never enabled; a nullable owner with no default. Fixed, with both notes.
CREATE TABLE public.bookmarks (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id UUID REFERENCES auth.users,
  url TEXT NOT NULL
);

-- RLS never enabled, and only a name suggests an owner: a gap proposing user_id.
CREATE TABLE public.notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  body TEXT NOT NULL
);

-- An ALL policy of `true`, on a table owned one hop through profiles(id). The fix
-- replaces the write half and keeps the read half as it was.
CREATE TABLE public.comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES public.profiles(id),
  body TEXT NOT NULL
);
ALTER TABLE public.comments ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can do anything with comments" ON public.comments
  USING (true);

-- Two columns reference auth.users. Which one owns the row is not in the schema.
CREATE TABLE public.assignments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_by UUID NOT NULL REFERENCES auth.users(id),
  assignee_id UUID REFERENCES auth.users(id),
  title TEXT NOT NULL
);
ALTER TABLE public.assignments ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Signed-in users can update assignments" ON public.assignments
  FOR UPDATE TO authenticated USING (true);

-- A policy names customer_id as an owner; the foreign key names issuer_id. They
-- disagree, so neither is proven.
CREATE TABLE public.invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  issuer_id UUID NOT NULL REFERENCES auth.users(id),
  customer_id UUID NOT NULL,
  amount_cents INTEGER NOT NULL
);
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Customers can view their invoices" ON public.invoices
  FOR SELECT TO authenticated USING (auth.uid() = customer_id);
CREATE POLICY "Anyone signed in can delete invoices" ON public.invoices
  FOR DELETE TO authenticated USING (true);

-- The policy that would prove the owner names `author`, renamed in a later migration.
CREATE TABLE public.drafts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author UUID NOT NULL,
  body TEXT
);
ALTER TABLE public.drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Authors can read their drafts" ON public.drafts
  FOR SELECT TO authenticated USING (auth.uid() = author);
CREATE POLICY "Anyone can delete drafts" ON public.drafts
  FOR DELETE USING (true);

-- An owner-scoped UPDATE already exists beside a `true` one: the fix only drops.
CREATE TABLE public.projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  name TEXT NOT NULL
);
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Owners can view projects" ON public.projects
  FOR SELECT TO authenticated USING ((select auth.uid()) = owner_id);
CREATE POLICY "Owners can update projects" ON public.projects
  FOR UPDATE TO authenticated
  USING ((select auth.uid()) = owner_id) WITH CHECK ((select auth.uid()) = owner_id);
CREATE POLICY "Members can update projects" ON public.projects
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- An anonymous INSERT of `true`: a waitlist form. A decision, not a defect; untouched.
CREATE TABLE public.waitlist (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email TEXT NOT NULL
);
ALTER TABLE public.waitlist ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can join the waitlist" ON public.waitlist
  FOR INSERT TO anon WITH CHECK (true);
