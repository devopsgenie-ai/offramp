-- Profiles: world-readable on purpose, which the audit must still ask about.
CREATE TABLE public.profiles (
  id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
  display_name TEXT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Profiles are viewable by everyone"
  ON public.profiles FOR SELECT
  USING (true);

CREATE POLICY "Users can update their own profile"
  ON public.profiles FOR UPDATE
  USING (auth.uid() = id);

-- Tasks: RLS on, but any signed-in user may change any row.
CREATE TABLE public.tasks (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  owner_id UUID NOT NULL,
  title TEXT NOT NULL,
  done BOOLEAN NOT NULL DEFAULT false
);

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Owners can view their tasks" ON public.tasks
  FOR SELECT TO authenticated USING (auth.uid() = owner_id);

CREATE POLICY "Authenticated users can update tasks" ON public.tasks
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- Orders: RLS is enabled two migrations later. Must not be reported.
CREATE TABLE public.orders (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  customer_id UUID NOT NULL,
  total_cents INTEGER NOT NULL
);

-- A scratch table that a later migration drops. Must not be reported.
CREATE TABLE public.tmp_import (line TEXT);

-- Notes: never gets RLS. This is the finding.
CREATE TABLE public.notes (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  body TEXT NOT NULL
);

-- Not exposed through the API: tables outside `public` are out of scope.
CREATE SCHEMA IF NOT EXISTS private;
CREATE TABLE private.audit_log (entry TEXT);
