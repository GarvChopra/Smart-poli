import itertools
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before `db` (and therefore `main`) is ever imported by any test,
# so integration tests never touch the real smartpoli.db file.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["SMARTPOLI_DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
# Test fixtures build dose times from datetime.utcnow(); pin the default
# patient timezone to UTC so they stay valid. Timezone behaviour itself is
# tested explicitly (tests/test_timezone_and_reminders.py) with named zones.
os.environ["SMARTPOLI_DEFAULT_TZ"] = "UTC"
os.environ["SMARTPOLI_DISABLE_SCHEDULER"] = "1"
os.environ["SMARTPOLI_AI_INFO"] = "0"
os.environ["SMARTPOLI_AI_SCAN"] = "0"   # no real vision calls in tests; they are mocked where needed
os.environ["SMARTPOLI_DISABLE_RATE_LIMIT"] = "1"   # tests register many users from one IP; test_web_security turns it on
# AI dose-gap lookups call Groq / openFDA; tests inject fakes where they want them.
os.environ["SMARTPOLI_AI_GAPS"] = "0"
# No real outbound push/WhatsApp from tests.
os.environ.pop("SMARTPOLI_VAPID_PRIVATE_KEY", None)

_email_counter = itertools.count()


def register_and_login(client, role="patient", name="Test User"):
    """
    Every endpoint now requires a real logged-in account (auth.py) instead
    of trusting a bare patient_id in the URL. Tests exercise the real
    register -> login -> Authorization header flow rather than bypassing
    it — otherwise the test suite would stop proving the authorization
    actually works. Sets the token as a default header on `client` so every
    later request in the test is authenticated automatically.

    Returns the created user's dict (id, email, name, role).
    """
    email = f"test{next(_email_counter)}@example.com"
    r = client.post("/auth/register", json={
        "email": email, "password": "testpassword123", "name": name, "role": role,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    client.headers["Authorization"] = f"Bearer {body['token']}"
    return body["user"]
