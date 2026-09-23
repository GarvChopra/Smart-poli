import itertools
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before `db` (and therefore `main`) is ever imported by any test,
# so integration tests never touch the real smartpoli.db file.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["SMARTPOLI_DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

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
