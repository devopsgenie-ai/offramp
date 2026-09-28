CREATE TABLE public.bookings (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  slot TEXT NOT NULL
);

ALTER TABLE public.bookings ENABLE ROW LEVEL SECURITY;
