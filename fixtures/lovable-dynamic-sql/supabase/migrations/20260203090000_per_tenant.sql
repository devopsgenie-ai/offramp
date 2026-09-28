-- One table per tenant, created dynamically. The replay cannot know what this does,
-- so every RLS check must say "could not assess" rather than "clean".
DO $$
DECLARE tenant TEXT;
BEGIN
  FOREACH tenant IN ARRAY ARRAY['acme', 'globex'] LOOP
    EXECUTE format('CREATE TABLE IF NOT EXISTS public.%I_bookings (LIKE public.bookings)', tenant);
  END LOOP;
END
$$;
