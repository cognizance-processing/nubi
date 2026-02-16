-- Create a bucket for secret files if it doesn't exist
insert into storage.buckets (id, name, public)
values ('secret-files', 'secret-files', false)
on conflict (id) do nothing;

-- Set up RLS for the secret-files bucket
-- Allow authenticated users to upload files to their own folder
create policy "Users can upload secret files to their own folder"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'secret-files' AND
  (storage.foldername(name))[1] = auth.uid()::text
);

-- Allow authenticated users to see their own files
create policy "Users can view their own secret files"
on storage.objects for select
to authenticated
using (
  bucket_id = 'secret-files' AND
  (storage.foldername(name))[1] = auth.uid()::text
);

-- Allow authenticated users to update their own files
create policy "Users can update their own secret files"
on storage.objects for update
to authenticated
using (
  bucket_id = 'secret-files' AND
  (storage.foldername(name))[1] = auth.uid()::text
);

-- Allow authenticated users to delete their own files
create policy "Users can delete their own secret files"
on storage.objects for delete
to authenticated
using (
  bucket_id = 'secret-files' AND
  (storage.foldername(name))[1] = auth.uid()::text
);
