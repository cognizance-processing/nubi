-- Create a function to handle new user creation
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, username, full_name, updated_at)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data->>'full_name',
    now()
  );
  return new;
end;
$$ language plpgsql security definer;

-- Drop trigger if it exists, then create it
drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
