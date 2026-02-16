-- Board Queries: Python queries that belong to boards
create table if not exists public.board_queries (
  id uuid default gen_random_uuid() primary key,
  board_id uuid references public.boards(id) on delete cascade not null,
  name text not null,
  description text,
  python_code text not null default '',
  ui_map jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

create index if not exists idx_board_queries_board_id
  on public.board_queries (board_id, updated_at desc);

-- Trigger to auto-update updated_at
create or replace function public.update_board_query_updated_at()
returns trigger as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$ language plpgsql;

create trigger board_queries_updated_at
  before update on public.board_queries
  for each row execute function public.update_board_query_updated_at();
