-- SmartPoli: switch off Supabase's public web API (PostgREST) for SmartPoli's tables.
--
-- Why: Supabase serves every table in "public" through a web API that anyone with the project's "anon" key can call,
-- and every SmartPoli table currently allows it (row level security off, anon/authenticated have full rights).
-- SmartPoli never uses that API - the server connects to the database directly as "postgres", which bypasses row
-- level security - so the app keeps working exactly as before. No row is changed or deleted.
--
-- How: Supabase dashboard -> SQL Editor -> paste this whole file -> Run. Safe to run again.
-- Undo: supabase_unlock_public_api.sql (next to this file).

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from anon, authenticated', t);
    -- if the app already uses the least-privilege account (supabase_create_app_role.sql), keep its access
    if exists (select 1 from pg_roles where rolname = 'smartpoli_app') then
      execute format('drop policy if exists smartpoli_app_all on public.%I', t);
      execute format('create policy smartpoli_app_all on public.%I to smartpoli_app using (true) with check (true)', t);
    end if;
  end loop;
end $$;

revoke all on all sequences in schema public from anon, authenticated;

-- tables the app creates later must not get API rights either
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;

-- check: every row should say rls_on = true and api_rights = 0
select c.relname as table_name, c.relrowsecurity as rls_on,
       (select count(*) from information_schema.role_table_grants g
         where g.table_schema = 'public' and g.table_name = c.relname and g.grantee in ('anon', 'authenticated')) as api_rights
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by 1;
