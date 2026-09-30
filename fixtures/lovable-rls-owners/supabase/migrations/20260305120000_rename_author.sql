-- PostgreSQL rewrites "Authors can read their drafts" to name author_id. The replay
-- keeps the text as written, so that policy is no longer evidence of anything.
ALTER TABLE public.drafts RENAME COLUMN author TO author_id;
