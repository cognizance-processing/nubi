-- Datastores (connections to BigQuery, Postgres, etc.)
create table if not exists public.datastores (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id) on delete cascade not null,
  name text not null,
  type text not null,
  config jsonb default '{}'::jsonb not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

create index if not exists idx_datastores_user_id
  on public.datastores (user_id);
