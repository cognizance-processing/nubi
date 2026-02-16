-- Create profiles table
create table if not exists public.profiles (
  id uuid references auth.users not null primary key,
  username text unique,
  full_name text,
  avatar_url text,
  website text,
  updated_at timestamp with time zone,
  constraint username_length check (char_length(username) >= 3)
);

-- Create organizations table
create table if not exists public.organizations (
  id uuid default gen_random_uuid() primary key,
  name text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create boards table (dashboards)
create table if not exists public.boards (
  id uuid default gen_random_uuid() primary key,
  organization_id uuid references public.organizations(id) on delete cascade,
  profile_id uuid references public.profiles(id) on delete set null,
  name text not null,
  description text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create board_code table (versions of board code)
create table if not exists public.board_code (
  id uuid default gen_random_uuid() primary key,
  board_id uuid references public.boards(id) on delete cascade not null,
  version integer not null,
  code text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  unique(board_id, version)
);

-- Create board_components table (code templates)
create table if not exists public.board_components (
  id uuid default gen_random_uuid() primary key,
  name text not null,
  type text not null,
  code_template text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- No RLS enabled as requested.
