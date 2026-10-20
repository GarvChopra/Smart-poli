-- SmartPoli: a least-privilege database account for the app (Phase 2).
--
-- Today the app connects as "postgres", Supabase's admin account, which can also drop tables, read other schemas and
-- change roles. This creates "smartpoli_app", which can only read and write rows in SmartPoli's tables - nothing else.
-- Creating tables / columns / indexes at start-up keeps using the owner account through a second variable.
--
-- 1. Supabase dashboard -> SQL Editor: replace CHANGE_ME below with a long random password, paste, Run.
-- 2. Render -> Environment:
--      SMARTPOLI_MIGRATION_DATABASE_URL = the CURRENT value of SMARTPOLI_DATABASE_URL (the postgres account)
--      SMARTPOLI_DATABASE_URL           = the same URL with user  smartpoli_app.<project-ref>  and the new password
--    Save -> Render redeploys. Nothing else changes; no row is touched.
-- Undo: set SMARTPOLI_DATABASE_URL back to the old value and delete SMARTPOLI_MIGRATION_DATABASE_URL.

create role smartpoli_app login password 'CHANGE_ME' noinherit;

grant usage on schema public to smartpoli_app;
grant select, insert, update, delete on all tables in schema public to smartpoli_app;
grant usage, select on all sequences in schema public to smartpoli_app;

-- tables / sequences the app creates later (as postgres, at start-up) get the same rights
alter default privileges for role postgres in schema public grant select, insert, update, delete on tables to smartpoli_app;
alter default privileges for role postgres in schema public grant usage, select on sequences to smartpoli_app;

-- With row level security on (supabase_lock_public_api.sql), a non-owner sees no rows unless a policy lets it.
-- This account may see every row - access per patient is enforced by the app, exactly as now.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('drop policy if exists smartpoli_app_all on public.%I', t);
    execute format('create policy smartpoli_app_all on public.%I to smartpoli_app using (true) with check (true)', t);
  end loop;
end $$;

-- statement limit for this account, as a second line of defence next to the app's own
alter role smartpoli_app set statement_timeout = '15s';
