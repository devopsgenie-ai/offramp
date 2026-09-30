-- Identifiers are data. Each name below is legal PostgreSQL, and each is built to break
-- a renderer that interpolates names raw or writes them into a comment unescaped.

-- Quotes, a semicolon and a comment marker in the table name; a space in the owner.
CREATE TABLE public."Journal ""Entries""; DROP TABLE public.profiles; --" (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "Owner ID" UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id),
  body TEXT
);

-- A reserved word as the table name and as the owner column.
CREATE TABLE public."order" (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "user" UUID NOT NULL REFERENCES auth.users(id),
  total_cents INTEGER NOT NULL
);

-- A newline in the table name. Written into a -- comment unescaped, the second line
-- would become SQL.
CREATE TABLE public."notes
alter table public.profiles disable row level security; --" (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id),
  body TEXT
);
ALTER TABLE public."notes
alter table public.profiles disable row level security; --" ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can edit""; DROP TABLE public.profiles; /* */ --" ON public."notes
alter table public.profiles disable row level security; --"
  FOR UPDATE TO authenticated USING (true);
