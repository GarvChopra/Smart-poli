-- Undo supabase_lock_public_api.sql: back to Supabase's defaults (row level security off, API roles' rights restored).
-- Supabase dashboard -> SQL Editor -> paste -> Run.

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I disable row level security', t);
    execute format('grant all on table public.%I to anon, authenticated', t);
  end loop;
end $$;

grant all on all sequences in schema public to anon, authenticated;
alter default privileges for role postgres in schema public grant all on tables to anon, authenticated;
alter default privileges for role postgres in schema public grant all on sequences to anon, authenticated;
