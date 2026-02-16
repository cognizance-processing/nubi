-- Chats: linked to boards only
create table if not exists public.chats (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id) on delete cascade not null,
  board_id uuid references public.boards(id) on delete cascade,
  title text not null default 'New chat',
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

create index if not exists idx_chats_user_id
  on public.chats (user_id, updated_at desc);

create index if not exists idx_chats_board_id
  on public.chats (board_id, updated_at desc);

-- Chat messages
create table if not exists public.chat_messages (
  id uuid default gen_random_uuid() primary key,
  chat_id uuid references public.chats(id) on delete cascade not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

create index if not exists idx_chat_messages_chat_created
  on public.chat_messages (chat_id, created_at asc);

-- Trigger to bump chats.updated_at when a message is inserted
create or replace function public.bump_chat_updated_at()
returns trigger as $$
begin
  update public.chats set updated_at = timezone('utc'::text, now()) where id = new.chat_id;
  return new;
end;
$$ language plpgsql;

create trigger chat_messages_updated_at
  after insert on public.chat_messages
  for each row execute function public.bump_chat_updated_at();
