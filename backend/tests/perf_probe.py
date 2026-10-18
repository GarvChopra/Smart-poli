"""Not a test: counts SQL statements per request on a realistic patient (run: python tests/perf_probe.py)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SMARTPOLI_DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.update(SMARTPOLI_DISABLE_SCHEDULER="1", SMARTPOLI_AI_INFO="0", SMARTPOLI_AI_GAPS="0", SMARTPOLI_AI_RX="0",
                  SMARTPOLI_DISABLE_RATE_LIMIT="1", SMARTPOLI_DEFAULT_TZ="Asia/Kolkata")
from datetime import datetime, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

import db as dbm  # noqa: E402
from main import app  # noqa: E402

dbm.init_db()
c = TestClient(app)
tok = c.post("/auth/register", json={"email": "p@x.com", "password": "password123", "name": "P", "role": "patient"}).json()["token"]
c.headers["Authorization"] = f"Bearer {tok}"
pid = c.post("/patients", json={"name": "P"}).json()["id"]
for n in ["Metformin", "Amlodipine", "Levothyroxine", "Calcium carbonate", "Atorvastatin", "Aspirin"]:
    c.post("/medicines/manual", json={"patient_id": pid, "name": n, "times": ["08:00", "20:00"]})
s = dbm.SessionLocal()
dose = s.query(dbm.Dose).filter(dbm.Dose.state == "pending").order_by(dbm.Dose.scheduled_at).first()
dose.scheduled_at = datetime.utcnow() + timedelta(hours=5, minutes=20)
s.commit()
did = dose.id
s.close()

count = [0]
event.listen(dbm.engine, "before_cursor_execute", lambda *a, **k: count.__setitem__(0, count[0] + 1))


def probe(label, fn):
    count[0] = 0
    r = fn()
    print(f"{label:45s} {r.status_code}  {count[0]:5d} queries")


probe("GET dashboard", lambda: c.get(f"/patients/{pid}/dashboard"))
probe("GET open-warnings", lambda: c.get(f"/patients/{pid}/open-warnings"))
probe("POST take", lambda: c.post(f"/doses/{did}/take"))
probe("GET timeline", lambda: c.get(f"/patients/{pid}/timeline"))
probe("GET safety-center", lambda: c.get(f"/patients/{pid}/safety-center"))
probe("GET pair-check", lambda: c.get(f"/patients/{pid}/pair-check"))
import main  # noqa: E402
count[0] = 0
main._reminder_sweep_job()
print(f"{'reminder sweep (every minute)':45s}      {count[0]:5d} queries")
