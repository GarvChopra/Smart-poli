"""Database protection: housekeeping only removes old technical rows; a local copy refuses the hosted database;
the indexes the app adds are real; the health check and backup tools exist and stay read-only / encrypted."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402
from db import DeadPushEndpoint, ReminderLog, SessionLocal, init_db, prune_old_rows  # noqa: E402

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def test_housekeeping_removes_only_old_technical_rows():
    init_db()
    db = SessionLocal()
    try:
        db.query(ReminderLog).delete(); db.query(DeadPushEndpoint).delete(); db.commit()
        now = datetime(2027, 6, 1)
        db.add_all([ReminderLog(dose_id=1, kind="due", channel="push", sent_at=now - timedelta(days=200)),
                    ReminderLog(dose_id=2, kind="due", channel="push", sent_at=now - timedelta(days=10)),
                    DeadPushEndpoint(endpoint_hash="a" * 64, patient_id=1, died_at=now - timedelta(days=400)),
                    DeadPushEndpoint(endpoint_hash="b" * 64, patient_id=1, died_at=now - timedelta(days=1))])
        db.commit()
        assert prune_old_rows(db, now) == {"reminder_log": 1, "dead_push_endpoints": 1}
        assert [r.dose_id for r in db.query(ReminderLog)] == [2]
        assert [r.endpoint_hash[0] for r in db.query(DeadPushEndpoint)] == ["b"]
    finally:
        db.close()


@pytest.mark.parametrize("url, env, refused", [
    ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres", {}, True),
    ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres", {"RENDER": "true"}, False),
    ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres", {"RENDER_GIT_COMMIT": "abc"}, False),
    ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres", {"SMARTPOLI_ALLOW_PRODUCTION_DB": "1"}, False),
    ("postgresql://app@localhost:5499/smartpoli", {}, False),
    ("sqlite:///./dev.db", {}, False),
])
def test_a_local_copy_refuses_the_hosted_database(monkeypatch, url, env, refused):
    import db
    monkeypatch.setattr(db, "DATABASE_URL", url)
    for k in ("RENDER", "RENDER_SERVICE_ID", "RENDER_GIT_COMMIT", "SMARTPOLI_ALLOW_PRODUCTION_DB"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if refused:
        with pytest.raises(RuntimeError) as e:
            main.refuse_production_db_off_server()
        assert "pw" not in str(e.value)                      # the password is never printed
    else:
        main.refuse_production_db_off_server()


def test_tools_are_safe_by_construction():
    health = open(os.path.join(TOOLS, "db_health_check.py"), encoding="utf-8").read()
    assert "SET TRANSACTION READ ONLY" in health
    backup = open(os.path.join(TOOLS, "db_backup.sh"), encoding="utf-8").read()
    assert "--symmetric --cipher-algo AES256" in backup and 'rm -f "$plain"' in backup
    load = open(os.path.join(TOOLS, "load_test.py"), encoding="utf-8").read()
    assert '("127.0.0.1", "localhost")' in load
    lock = open(os.path.join(TOOLS, "supabase_lock_public_api.sql"), encoding="utf-8").read()
    assert "enable row level security" in lock and "smartpoli_app_all" in lock
    assert os.path.exists(os.path.join(TOOLS, "supabase_unlock_public_api.sql"))
