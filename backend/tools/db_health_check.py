"""
Daily database health check - READ ONLY, reads no patient data (only counts, sizes and statistics).

    SMARTPOLI_DATABASE_URL=postgresql://... python tools/db_health_check.py

Prints a short report and exits 1 if something needs attention, so a scheduled GitHub Action fails and GitHub emails
the repository owner. Checks: connections in use, database size against the free-plan limit, slow queries, big tables
read without an index, and that Supabase's public web API cannot read the tables (row level security on, no rights
for the anon/authenticated roles).
"""
import os
import sys

from sqlalchemy import create_engine, text

MAX_CONNECTION_SHARE = 0.7          # of max_connections
MAX_DB_MB = 400                     # Supabase free plan allows 500 MB
SLOW_QUERY_MS = 500                 # mean time of an app query called at least 50 times
SEQ_SCAN_ROWS = 10_000              # a table this big should be read through an index


def main() -> int:
    url = os.environ.get("SMARTPOLI_DATABASE_URL", "")
    if not url.startswith("postgres"):
        print("SMARTPOLI_DATABASE_URL must point at the Postgres database.")
        return 2
    problems, notes = [], []
    eng = create_engine(url, pool_size=1, max_overflow=0)
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        q = lambda s: c.execute(text(s)).all()  # noqa: E731

        used = q("select count(*) from pg_stat_activity where datname = current_database()")[0][0]
        cap = int(q("show max_connections")[0][0])
        notes.append(f"connections {used}/{cap}")
        if used > MAX_CONNECTION_SHARE * cap:
            problems.append(f"{used} of {cap} connections in use")

        mb = q("select pg_database_size(current_database()) / 1048576.0")[0][0]
        notes.append(f"size {mb:.0f} MB")
        if mb > MAX_DB_MB:
            problems.append(f"database is {mb:.0f} MB (free plan limit 500 MB)")

        try:
            slow = q(f"""select calls, round(mean_exec_time) as ms, left(regexp_replace(query, '\\s+', ' ', 'g'), 80)
                         from pg_stat_statements
                         where calls >= 50 and mean_exec_time > {SLOW_QUERY_MS}
                           and query !~* '(pgbouncer|pg_catalog|pg_stat|information_schema|pg_timezone)'
                         order by mean_exec_time desc limit 5""")
            for calls, ms, sql in slow:
                problems.append(f"slow query: {ms} ms on average over {calls} calls: {sql}")
        except Exception:  # noqa: BLE001 - the extension may be off; the rest of the check still runs
            c.rollback()
            c.execute(text("SET TRANSACTION READ ONLY"))
            notes.append("pg_stat_statements not available")

        for table, rows, seq, idx in q(f"""select relname, n_live_tup, seq_scan, coalesce(idx_scan, 0) from pg_stat_user_tables
                                           where n_live_tup > {SEQ_SCAN_ROWS} and seq_scan > coalesce(idx_scan, 0)"""):
            problems.append(f"table {table} ({rows} rows) is read without an index more often than with one "
                            f"({seq} vs {idx} times)")

        open_tables = [r[0] for r in q("""select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                                          where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity""")]
        api_roles = q("select count(*) from pg_roles where rolname in ('anon', 'authenticated')")[0][0]
        if api_roles and open_tables:
            problems.append(f"{len(open_tables)} table(s) readable through Supabase's public API (row level security "
                            f"off) - run backend/tools/supabase_lock_public_api.sql")
        if api_roles:
            grants = q("""select count(*) from information_schema.role_table_grants
                          where table_schema = 'public' and grantee in ('anon', 'authenticated')""")[0][0]
            if grants:
                problems.append(f"the public API roles still have {grants} table right(s)")

    print("SmartPoli database health: " + ", ".join(notes))
    for p in problems:
        print("  PROBLEM: " + p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
