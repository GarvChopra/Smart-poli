# Emergency Card Secure Token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the guessable public `/emergency/{patient_id}` page with `/emergency/{random token}`, add token revocation, and show "last updated" on the card.

**Architecture:** A new table `emergency_card_tokens` plus a tiny module `emergency_tokens.py` own token create/rotate/lookup. `serializers.emergency_card_data` gains `card_path` and `last_updated`, so every caller (patient app, caregiver view, WhatsApp bot) gets the token link from one place. The public page route switches from int id to token.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite (dev/tests) / Postgres (Render), vanilla JS frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-emergency-card-token-design.md`

## Global Constraints

- New table only — never add columns to existing tables (Render Postgres is not migrated by `init_db()`).
- Token: `secrets.token_urlsafe(16)`.
- Unknown / revoked token and old integer URLs → 404 with **no patient data** in the body; no redirect from old URLs.
- Public page sends `Referrer-Policy: no-referrer` and `<meta name="robots" content="noindex">`.
- Frontend must not use `window.confirm` / `alert` for the revoke confirmation.
- Git commits: no Claude co-author trailer (user preference).
- Run all tests from `backend/`: `python -m pytest -q`. Baseline: 134 passed.

## Review Focus

1. A patient with **two** patient profiles → each has its own token; revoking one leaves the other working (test in Task 1).
2. Token lookup must be exact-match — a prefix or case-changed token must 404 (test in Task 2).
3. Caregiver overview's `emergency_card` payload now contains `card_path` → the caregiver page link must use it, not the patient id (Task 3, manual check + existing caregiver tests still pass).
4. Calling `emergency_card_data` repeatedly must not create a new token each time (test in Task 1).
5. Revoke by a caregiver/doctor (read-only access) → 403 (test in Task 2).

---

## File Structure

- Create `backend/emergency_tokens.py` — token create / rotate / lookup; nothing else.
- Modify `backend/db.py` — add `EmergencyCardToken` model.
- Modify `backend/serializers.py` — `emergency_card_data` adds `card_path`, `last_updated`.
- Modify `backend/main.py` — QR uses token, public route by token, revoke endpoint, page hardening.
- Modify `backend/whatsapp_bot.py` — token URL.
- Modify `backend/static/app.js`, `backend/static/caregiver.js` — use `card_path`; revoke button.
- Tests: create `backend/tests/test_emergency_tokens.py`; modify `backend/tests/test_emergency_card.py`, `backend/tests/test_whatsapp_bot.py`.

---

### Task 1: Token model and module, wired into card data

**Files:**
- Modify: `backend/db.py` (add model after `AuditLog`, ~line 222)
- Create: `backend/emergency_tokens.py`
- Modify: `backend/serializers.py:212-240`
- Test: `backend/tests/test_emergency_tokens.py`

**Interfaces:**
- Produces:
  - `db.EmergencyCardToken` (columns: `id`, `patient_id`, `token`, `created_at`, `revoked_at`)
  - `emergency_tokens.get_or_create_active_token(db: Session, patient_id: int) -> str`
  - `emergency_tokens.rotate_token(db: Session, patient_id: int) -> str`
  - `emergency_tokens.patient_id_for_token(db: Session, token: str) -> Optional[int]`
  - `emergency_card_data(db, patient_id)` result gains `"card_path": "/emergency/<token>"` and `"last_updated": "<ISO datetime>"`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_emergency_tokens.py`:

```python
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from db import SessionLocal  # noqa: E402
from conftest import register_and_login  # noqa: E402
import emergency_tokens  # noqa: E402
from serializers import emergency_card_data  # noqa: E402


def _make_patient(client, name="Token Test"):
    return client.post("/patients", json={"name": name}).json()["id"]


def test_token_is_stable_until_rotated():
    with TestClient(app) as client:
        register_and_login(client)
        pid = _make_patient(client)
    db = SessionLocal()
    try:
        t1 = emergency_tokens.get_or_create_active_token(db, pid)
        t2 = emergency_tokens.get_or_create_active_token(db, pid)
        assert t1 == t2
        assert len(t1) >= 20
        assert str(pid) != t1
        assert emergency_tokens.patient_id_for_token(db, t1) == pid

        t3 = emergency_tokens.rotate_token(db, pid)
        assert t3 != t1
        assert emergency_tokens.patient_id_for_token(db, t1) is None
        assert emergency_tokens.patient_id_for_token(db, t3) == pid
        assert emergency_tokens.get_or_create_active_token(db, pid) == t3
    finally:
        db.close()


def test_unknown_token_returns_none():
    db = SessionLocal()
    try:
        assert emergency_tokens.patient_id_for_token(db, "does-not-exist") is None
        assert emergency_tokens.patient_id_for_token(db, "") is None
    finally:
        db.close()


def test_two_profiles_have_independent_tokens():
    with TestClient(app) as client:
        register_and_login(client)
        a = _make_patient(client, "Profile A")
        b = _make_patient(client, "Profile B")
    db = SessionLocal()
    try:
        ta = emergency_tokens.get_or_create_active_token(db, a)
        tb = emergency_tokens.get_or_create_active_token(db, b)
        assert ta != tb
        emergency_tokens.rotate_token(db, a)
        assert emergency_tokens.patient_id_for_token(db, tb) == b
    finally:
        db.close()


def test_card_data_has_path_and_last_updated_and_is_idempotent():
    with TestClient(app) as client:
        register_and_login(client)
        pid = _make_patient(client)
        db = SessionLocal()
        try:
            d1 = emergency_card_data(db, pid)
            d2 = emergency_card_data(db, pid)
            assert d1["card_path"] == d2["card_path"]
            assert d1["card_path"].startswith("/emergency/")
            assert d1["card_path"] != f"/emergency/{pid}"
            first_updated = d1["last_updated"]
        finally:
            db.close()

        time.sleep(0.01)
        client.patch(f"/patients/{pid}", json={"allergies": "Sulfa"})
        db = SessionLocal()
        try:
            assert emergency_card_data(db, pid)["last_updated"] > first_updated
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_emergency_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'emergency_tokens'`

- [ ] **Step 3: Add the model to `backend/db.py`**

Insert after the `AuditLog` class:

```python
class EmergencyCardToken(Base):
    """The random, revocable token a public emergency-card URL/QR carries —
    never the sequential patient id, which anyone could enumerate. A new
    table (not columns on `patients`) because init_db() can create tables
    on Postgres but not alter existing ones. At most one active
    (revoked_at IS NULL) row per patient; revoked rows are kept for audit."""
    __tablename__ = "emergency_card_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Create `backend/emergency_tokens.py`**

```python
"""
SmartPoli — emergency-card tokens.

The public, no-login emergency page is addressed by a random token, not the
patient id, so the page can't be enumerated and a lost QR/card can be
revoked by rotating the token. This module is the only place tokens are
created, rotated or resolved.
"""

import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from db import EmergencyCardToken


def _active(db: Session, patient_id: int) -> Optional[EmergencyCardToken]:
    return (db.query(EmergencyCardToken)
            .filter(EmergencyCardToken.patient_id == patient_id,
                    EmergencyCardToken.revoked_at.is_(None))
            .first())


def get_or_create_active_token(db: Session, patient_id: int) -> str:
    row = _active(db, patient_id)
    if row:
        return row.token
    row = EmergencyCardToken(patient_id=patient_id, token=secrets.token_urlsafe(16))
    db.add(row)
    db.commit()
    return row.token


def rotate_token(db: Session, patient_id: int) -> str:
    row = _active(db, patient_id)
    if row:
        row.revoked_at = datetime.utcnow()
        db.commit()
    return get_or_create_active_token(db, patient_id)


def patient_id_for_token(db: Session, token: str) -> Optional[int]:
    if not token:
        return None
    row = (db.query(EmergencyCardToken)
           .filter(EmergencyCardToken.token == token,
                   EmergencyCardToken.revoked_at.is_(None))
           .first())
    return row.patient_id if row else None
```

- [ ] **Step 5: Extend `emergency_card_data` in `backend/serializers.py`**

Add imports at the top of `serializers.py` (merge with the existing `from db import ...` line; add `AuditLog` if not present, and `func` from sqlalchemy):

```python
from sqlalchemy import func
from db import AuditLog
from emergency_tokens import get_or_create_active_token
```

In `emergency_card_data`, before the `return`, add:

```python
    latest_audit = db.query(func.max(AuditLog.at)).filter(AuditLog.patient_id == patient_id).scalar()
    candidates = [patient.created_at, latest_audit] + [pres.created_at for pres in prescriptions]
    last_updated = max(c for c in candidates if c is not None)
```

and add two keys to the returned dict:

```python
        "card_path": f"/emergency/{get_or_create_active_token(db, patient_id)}",
        "last_updated": last_updated.isoformat(),
```

(`PATCH /patients/{id}` already writes a `patient_updated` audit entry — `main.py:206` — so profile edits move `last_updated` forward.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_emergency_tokens.py -v`
Expected: 4 passed

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass (138)

- [ ] **Step 8: Commit**

```bash
git add backend/db.py backend/emergency_tokens.py backend/serializers.py backend/tests/test_emergency_tokens.py
git commit -m "Emergency card: random revocable token model, card_path and last_updated in card data"
```

---

### Task 2: Public page by token, revoke endpoint, hardening

**Files:**
- Modify: `backend/main.py:754-853` (emergency card section)
- Modify: `backend/tests/test_emergency_card.py`
- Test: `backend/tests/test_emergency_tokens.py` (append)

**Interfaces:**
- Consumes: `emergency_tokens.patient_id_for_token`, `emergency_tokens.rotate_token`, `emergency_card_data(...)["card_path"]`
- Produces:
  - `GET /emergency/{token}` (public HTML)
  - `POST /patients/{patient_id}/emergency-card/revoke` → `{"card_path": "/emergency/<new token>"}`
  - `GET /patients/{id}/emergency-card` JSON now includes `card_path`, `last_updated` (from Task 1)

- [ ] **Step 1: Update existing tests in `backend/tests/test_emergency_card.py`**

In `test_emergency_card_data_and_public_page`, replace

```python
        r = client.get(f"/emergency/{patient_id}")
        assert r.status_code == 200
```

with

```python
        card_path = data["card_path"]
        r = client.get(card_path)
        assert r.status_code == 200
        assert r.headers["referrer-policy"] == "no-referrer"
        assert 'name="robots" content="noindex"' in r.text
        assert "Last updated" in r.text
```

In `test_emergency_flag_appears_after_emergency_triage`, replace

```python
        r = client.get(f"/emergency/{patient_id}")
```

with

```python
        r = client.get(client.get(f"/patients/{patient_id}/emergency-card").json()["card_path"])
```

- [ ] **Step 2: Append new failing tests to `backend/tests/test_emergency_tokens.py`**

```python
def test_old_integer_url_is_gone():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Old Url", "allergies": "Penicillin"}).json()["id"]
        client.headers.pop("Authorization")
        r = client.get(f"/emergency/{pid}")
        assert r.status_code == 404
        assert "Penicillin" not in r.text
        assert "Old Url" not in r.text


def test_revoke_kills_old_link_and_qr_uses_new_one():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Revoke Me", "allergies": "Latex"}).json()["id"]
        old_path = client.get(f"/patients/{pid}/emergency-card").json()["card_path"]
        assert client.get(old_path).status_code == 200

        r = client.post(f"/patients/{pid}/emergency-card/revoke")
        assert r.status_code == 200
        new_path = r.json()["card_path"]
        assert new_path != old_path

        r = client.get(old_path)
        assert r.status_code == 404
        assert "Latex" not in r.text
        assert "Revoke Me" not in r.text
        assert "Latex" in client.get(new_path).text
        assert client.get(f"/patients/{pid}/emergency-card").json()["card_path"] == new_path


def test_token_lookup_is_exact():
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Exact"}).json()["id"]
        path = client.get(f"/patients/{pid}/emergency-card").json()["card_path"]
        token = path.rsplit("/", 1)[1]
        assert client.get(f"/emergency/{token[:-1]}").status_code == 404
        assert client.get(f"/emergency/{token.swapcase()}").status_code == 404


def test_revoke_requires_write_access():
    with TestClient(app) as owner:
        register_and_login(owner)
        pid = owner.post("/patients", json={"name": "Not Yours"}).json()["id"]
    with TestClient(app) as other:
        register_and_login(other)
        assert other.post(f"/patients/{pid}/emergency-card/revoke").status_code == 403
```

Note for `test_token_lookup_is_exact`: if `token.swapcase() == token` (no letters — practically impossible for 22 url-safe chars) the assertion is still valid only if the token has letters; `secrets.token_urlsafe(16)` output with zero letters has negligible probability.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_emergency_tokens.py tests/test_emergency_card.py -v`
Expected: FAIL — old int URL returns 200; revoke returns 404/405; no `referrer-policy` header.

- [ ] **Step 4: Implement in `backend/main.py`**

Add import near the other local imports:

```python
from emergency_tokens import patient_id_for_token, rotate_token
```

Replace the `emergency_card_qr` URL line:

```python
    url = str(request.base_url).rstrip("/") + f"/emergency/{patient_id}"
```

with

```python
    url = str(request.base_url).rstrip("/") + _emergency_card_data(db, patient_id)["card_path"]
```

Add the revoke endpoint after `emergency_card_qr`:

```python
@app.post("/patients/{patient_id}/emergency-card/revoke")
def revoke_emergency_card(patient_id: int, user: User = Depends(require_patient_write_access),
                          db: Session = Depends(get_db_session)):
    """Lost phone or printed card: the old QR/link stops working immediately."""
    get_patient_or_404(db, patient_id)
    token = rotate_token(db, patient_id)
    log_audit(db, patient_id, f"patient:{user.id}", "emergency_card_revoked", "token rotated")
    return {"card_path": f"/emergency/{token}"}
```

Change the public route signature and lookup. Replace

```python
@app.get("/emergency/{patient_id}", response_class=HTMLResponse, include_in_schema=False)
def emergency_card_page(patient_id: int, db: Session = Depends(get_db_session)):
    """Public, standalone, no-login card — what a QR scan opens. Deliberately
    plain server-rendered HTML, not the SPA, so it works even on a phone
    browser with nothing cached and renders instantly."""
    data = _emergency_card_data(db, patient_id)
```

with

```python
_INACTIVE_CARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Emergency card not active</title></head>
<body style="font-family:system-ui,sans-serif;padding:24px;max-width:480px;">
<h1 style="color:#AE2B22;font-size:22px;">This emergency card is no longer active</h1>
<p>The link may have been replaced by a newer card. In an emergency, call 112.</p>
</body></html>"""


@app.get("/emergency/{token}", response_class=HTMLResponse, include_in_schema=False)
def emergency_card_page(token: str, db: Session = Depends(get_db_session)):
    """Public, standalone, no-login card — what a QR scan opens. Addressed by
    a random revocable token (emergency_tokens.py), never the patient id.
    Deliberately plain server-rendered HTML, not the SPA, so it works even on
    a phone browser with nothing cached and renders instantly."""
    patient_id = patient_id_for_token(db, token)
    if patient_id is None:
        return HTMLResponse(content=_INACTIVE_CARD_HTML, status_code=404,
                            headers={"Referrer-Policy": "no-referrer"})
    data = _emergency_card_data(db, patient_id)
```

Inside the HTML `<head>` of the card page, after the viewport meta, add:

```html
<meta name="robots" content="noindex">
```

In the `<footer>` line, replace `<footer>{data['disclaimer']}</footer>` with:

```python
  <footer>Last updated {datetime.fromisoformat(data['last_updated']).strftime('%d %b %Y, %I:%M %p')} UTC<br>{data['disclaimer']}</footer>
```

Change the final line `return HTMLResponse(content=html)` to:

```python
    return HTMLResponse(content=html, headers={"Referrer-Policy": "no-referrer"})
```

Update the section comment above (`# but /emergency/{patient_id} — what the printed QR code actually opens —`) to say `/emergency/{token}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_emergency_tokens.py tests/test_emergency_card.py -v`
Expected: all pass

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: one failure — `test_whatsapp_bot.py::test_emergency_reply_includes_allergies_and_link` (fixed in Task 3). Everything else passes.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_emergency_card.py backend/tests/test_emergency_tokens.py
git commit -m "Emergency card: public page by token, revoke endpoint, noindex/no-referrer, last updated"
```

---

### Task 3: WhatsApp bot, patient app and caregiver view use the token link

**Files:**
- Modify: `backend/whatsapp_bot.py:277-290` (`_handle_emergency`)
- Modify: `backend/tests/test_whatsapp_bot.py:101-116`
- Modify: `backend/static/app.js:1073-1165` (`renderEmergencyCard`)
- Modify: `backend/static/caregiver.js:165`
- Modify: `backend/static/style.css` (append small block)

**Interfaces:**
- Consumes: `emergency_card_data(...)["card_path"]`, `["last_updated"]`; `POST /patients/{id}/emergency-card/revoke` → `{card_path}`

- [ ] **Step 1: Update the failing WhatsApp test**

In `backend/tests/test_whatsapp_bot.py`, replace

```python
        assert f"/emergency/{patient.id}" in reply
```

with

```python
        from serializers import emergency_card_data
        card_path = emergency_card_data(db, patient.id)["card_path"]
        assert card_path != f"/emergency/{patient.id}"
        assert card_path in reply
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_whatsapp_bot.py::test_emergency_reply_includes_allergies_and_link -v`
Expected: FAIL — reply contains `/emergency/<id>`

- [ ] **Step 3: Fix `whatsapp_bot.py::_handle_emergency`**

Replace

```python
    url = f"{PUBLIC_BASE_URL}/emergency/{patient_id}"
```

with

```python
    url = f"{PUBLIC_BASE_URL}{data['card_path']}"
```

- [ ] **Step 4: Run it to verify it passes, then the full suite**

Run: `python -m pytest -q`
Expected: all pass (142)

- [ ] **Step 5: Patient app — `backend/static/app.js` `renderEmergencyCard`**

(This emergency-tab UI will later be replaced by the separate 3D Digital Health Card project. The change here is deliberately minimal so the security fix ships complete on its own; the 3D card reuses the same `card_path`, `last_updated` and revoke endpoint.)

Replace

```js
  const cardUrl = `${window.location.origin}/emergency/${state.patientId}`;
```

with

```js
  const cardUrl = `${window.location.origin}${data.card_path}`;
  const lastUpdated = new Date(data.last_updated + 'Z').toLocaleString([], { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
```

Replace

```js
        <div style="margin-top:14px;"><a href="${cardUrl}" target="_blank">${cardUrl}</a></div>
```

with

```js
        <div style="margin-top:14px;"><a href="${cardUrl}" target="_blank" rel="noreferrer">${cardUrl}</a></div>
        <div class="card-updated">Last updated ${lastUpdated}</div>
        <div id="revokeArea" style="margin-top:12px;">
          <button class="ghost small" id="revokeCardBtn">Revoke &amp; make new QR</button>
        </div>
```

After the `editContactBtn` listener, add:

```js
  document.getElementById('revokeCardBtn').addEventListener('click', () => {
    document.getElementById('revokeArea').innerHTML = `
      <div class="revoke-confirm">
        The old QR code and any printed card will stop working. Continue?
        <div style="margin-top:8px;display:flex;gap:8px;">
          <button class="small" id="revokeYesBtn">Yes, revoke</button>
          <button class="ghost small" id="revokeNoBtn">Cancel</button>
        </div>
      </div>`;
    document.getElementById('revokeNoBtn').addEventListener('click', () => renderEmergencyCard());
    document.getElementById('revokeYesBtn').addEventListener('click', async () => {
      await api('POST', `/patients/${state.patientId}/emergency-card/revoke`);
      renderEmergencyCard();
    });
  });
```

(`data.last_updated` is a naive UTC ISO string from `datetime.utcnow()`, hence the appended `'Z'`.)

- [ ] **Step 6: Caregiver view — `backend/static/caregiver.js:165`**

Replace

```js
  const emergencyUrl = `${window.location.origin}/emergency/${state.patientId}`;
```

with

```js
  const emergencyUrl = `${window.location.origin}${ec.card_path}`;
```

- [ ] **Step 7: Styles — append to `backend/static/style.css`**

```css
.card-updated { font-size: 12px; color: var(--ink-soft); margin-top: 6px; }
.revoke-confirm { font-size: 13px; background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 10px 12px; }
```

- [ ] **Step 8: Manual check in the browser**

Run: `python -m uvicorn main:app --port 8000` (from `backend/`), log in as the demo patient, open **Emergency card**:
- Link shows `/emergency/<long random string>`, "Last updated …" visible, QR scans to the same link.
- Click **Revoke & make new QR** → inline confirm → **Yes, revoke** → link and QR change; opening the old link shows "This emergency card is no longer active".
- Log in as a linked caregiver → the emergency link in the overview uses the new token.

- [ ] **Step 9: Commit**

```bash
git add backend/whatsapp_bot.py backend/tests/test_whatsapp_bot.py backend/static/app.js backend/static/caregiver.js backend/static/style.css
git commit -m "Emergency card: token link everywhere, revoke button, last-updated in app"
```
