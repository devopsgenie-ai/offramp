alter table public.orders enable row level security;

create policy "Customers see their orders" on public.orders
  for select to authenticated using ( auth.uid() = customer_id );
