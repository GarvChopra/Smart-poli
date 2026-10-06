"""
Delete one user and everything that hangs off them, in the order the database's foreign keys require.

Supabase (or any database) refuses to delete a row in `users` while another table still points at it - for example
`patients.user_id` - and that patient in turn is pointed at by prescriptions -> medicines -> doses -> reminder log, notes,
settings, push subscriptions ... This walks that tree children-first, in ONE transaction: it all happens or none of it does.

    python tools/delete_user.py --user-id 8                                  # DRY RUN: who it is and how many rows would go
    python tools/delete_user.py --user-id 8 --print-sql                      # the same deletion as SQL for the Supabase SQL editor
    python tools/delete_user.py --user-id 8 --apply --confirm-host <part of the host shown>   # really do it

What is removed for the user:
  * their patient profile(s) and ALL of that patient's data (prescriptions, medicines, doses, reminders, notes, settings,
    emergency card, symptom checks, voice history, audit log, links, phone subscriptions, WhatsApp sessions);
  * anything THEY wrote or hold as a caregiver / doctor: notes they authored, their links to other patients, alert switches,
    feedback they sent, doctor templates and medicine corrections they made, their phone subscriptions.
Other people's own data (a patient this caregiver looked after) is NOT deleted - only this user's link to it.

Safety: dry run by default; --apply refuses unless --confirm-host matches the database host printed; it never touches the
shared lookup caches. DELETING A USER CANNOT BE UNDONE: take a backup first (Supabase dashboard -> Database -> Backups).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

# Sub-selects that find the user's data. :u is the user id.
P = "(SELECT id FROM patients WHERE user_id = :u)"
RX = f"(SELECT id FROM prescriptions WHERE patient_id IN {P})"
M = f"(SELECT id FROM medicines WHERE prescription_id IN {RX})"
D = f"(SELECT id FROM doses WHERE medicine_id IN {M})"

# (table, WHERE) - children first. The order is what makes the foreign keys happy.
STEPS = [
    ("reminder_log", f"dose_id IN {D}"),
    ("doses", f"medicine_id IN {M}"),
    ("medicine_days", f"medicine_id IN {M}"),
    ("medicine_corrections", f"medicine_id IN {M} OR doctor_user_id = :u"),
    ("care_notes", f"patient_id IN {P} OR author_user_id = :u"),
    ("medicines", f"prescription_id IN {RX}"),
    ("prescriptions", f"patient_id IN {P}"),
    ("audit_log", f"patient_id IN {P}"),
    ("caregiver_links", f"patient_id IN {P} OR caregiver_user_id = :u"),
    ("caregiver_prefs", f"patient_id IN {P} OR user_id = :u"),
    ("doctor_links", f"patient_id IN {P} OR doctor_user_id = :u"),
    ("emergency_card_profiles", f"patient_id IN {P}"),
    ("emergency_card_tokens", f"patient_id IN {P}"),
    ("patient_routine", f"patient_id IN {P}"),
    ("patient_settings", f"patient_id IN {P}"),
    ("push_subscriptions", f"patient_id IN {P} OR user_id = :u"),
    ("symptom_checks", f"patient_id IN {P}"),
    ("voice_messages", f"patient_id IN {P}"),
    ("whatsapp_sessions", f"patient_id IN {P} OR user_id = :u"),
    ("feedback", "user_id = :u"),
    ("prescription_templates", "doctor_user_id = :u"),
    ("user_push_subscriptions", "user_id = :u"),
    ("patients", "user_id = :u"),
    ("users", "id = :u"),
]


def describe_user(conn, user_id: int):
    return conn.execute(text("SELECT id, email, name, role FROM users WHERE id = :u"), {"u": user_id}).first()


def counts(conn, user_id: int) -> list:
    """[(table, rows that would be deleted)] for the steps that would delete something."""
    out = []
    for table, where in STEPS:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), {"u": user_id}).scalar()
        if n:
            out.append((table, n))
    return out


def delete_user(conn, user_id: int) -> dict:
    """Run every step on this connection (the caller owns the transaction). Returns {table: rows deleted}."""
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("PRAGMA foreign_keys = ON")           # make SQLite enforce the same rules as Postgres
    deleted = {}
    for table, where in STEPS:
        n = conn.execute(text(f"DELETE FROM {table} WHERE {where}"), {"u": user_id}).rowcount
        if n:
            deleted[table] = n
    return deleted


def sql_script(user_id: int) -> str:
    uid = int(user_id)
    lines = [f"-- Delete user {uid} and everything that depends on them. Run in the Supabase SQL editor. CANNOT BE UNDONE - back up first.",
             "BEGIN;"]
    for table, where in STEPS:
        lines.append(f"DELETE FROM {table} WHERE {where.replace(':u', str(uid))};")
    lines.append("COMMIT;")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-id", type=int, required=True, help="the id in the users table (the one the error message names)")
    ap.add_argument("--print-sql", action="store_true", help="print the deletion as SQL (for the Supabase SQL editor) and stop")
    ap.add_argument("--apply", action="store_true", help="really delete (default is a dry run)")
    ap.add_argument("--confirm-host", default="", help="a fragment of the database host printed below; required with --apply")
    args = ap.parse_args()

    if args.print_sql:
        print(sql_script(args.user_id))
        return 0

    import db as dbmod
    url = dbmod.DATABASE_URL
    host = url.split("@")[-1] if "@" in url else url
    print(f"Database: {host}")
    with dbmod.engine.connect() as conn:
        who = describe_user(conn, args.user_id)
        if who is None:
            print(f"No user with id {args.user_id}.")
            return 1
        print(f"User {who.id}: {who.name} <{who.email}> (role: {who.role})")
        plan = counts(conn, args.user_id)
    for table, n in plan:
        print(f"  {table:26s} {n} row(s)")
    if not args.apply:
        print("Dry run: nothing deleted. Re-run with --apply --confirm-host <host fragment> to delete (cannot be undone).")
        return 0
    if not args.confirm_host or args.confirm_host not in host:
        print("Refusing to delete: --confirm-host must match the database host shown above.")
        return 2
    with dbmod.engine.begin() as conn:                                  # one transaction: all or nothing
        deleted = delete_user(conn, args.user_id)
    print("Deleted:", ", ".join(f"{t}={n}" for t, n in deleted.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
