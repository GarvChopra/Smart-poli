"""
HTTP-level tests for dose recording, timing-safety endpoints, push
subscription, uploads and the Android/PWA plumbing. Deterministic fixtures,
no real patient data, no outbound network.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import clock  # noqa: E402
import main  # noqa: E402
from conftest import register_and_login  # noqa: E402
from db import AuditLog, Dose, PushSubscription, ReminderLog, SessionLocal, init_db  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def base_time():
    """A patient-local time two hours from now (tests pin the default zone to UTC)."""
    return clock.local_now("UTC").replace(second=0, microsecond=0) + timedelta(hours=2)


def audit_actions(pid):
    db = SessionLocal()
    try:
        return [a.action for a in db.query(AuditLog).filter_by(patient_id=pid).all()]
    finally:
        db.close()


def dose_row(dose_id):
    db = SessionLocal()
    try:
        d = db.query(Dose).get(dose_id)
        return d.state, d.scheduled_at, d.acted_at
    finally:
        db.close()


# ---------------------------------------------------------------- dose recording

def test_taking_a_dose_twice_is_idempotent_and_keeps_the_first_time():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [base_time()])
        first = client.post(f"/doses/{dose_id}/take").json()
        second = client.post(f"/doses/{dose_id}/take").json()
        assert first["state"] == second["state"] == "taken"
        assert first["acted_at"] == second["acted_at"]
        assert audit_actions(pid).count("dose_taken") + audit_actions(pid).count("dose_taken_late") == 1


def test_a_late_dose_is_recorded_with_its_real_time_and_audited_as_late():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        scheduled = clock.local_now("UTC").replace(microsecond=0) - timedelta(hours=1)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [scheduled], states=["missed"])
        r = client.post(f"/doses/{dose_id}/take").json()
        assert r["state"] == "taken"
        acted = datetime.fromisoformat(r["acted_at"])
        assert acted - scheduled >= timedelta(minutes=59)          # actual time, not the scheduled time
        assert "dose_taken_late" in audit_actions(pid)


def test_state_transitions_that_make_no_sense_are_refused():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (taken, missed) = add_medicine(pid, "Telmisartan", [base_time(), base_time() + timedelta(hours=8)],
                                          states=["taken", "missed"])
        assert client.post(f"/doses/{taken}/miss").status_code == 409
        assert client.post(f"/doses/{taken}/skip", json={"reason": "x"}).status_code == 409
        assert client.post(f"/doses/{missed}/snooze").status_code == 409
        assert client.post(f"/doses/{missed}/miss").status_code == 200       # idempotent
        assert client.post(f"/doses/{missed}/skip", json={"reason": " "}).status_code == 400


def test_dose_changes_by_another_user_are_forbidden():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (dose_id,) = add_medicine(pid, "Telmisartan", [base_time()])
        other = TestClient(app)
        register_and_login(other)
        for path, body in ((f"/doses/{dose_id}/take", None), (f"/doses/{dose_id}/snooze", None),
                           (f"/doses/{dose_id}/skip", {"reason": "x"}), (f"/doses/{dose_id}/miss", None)):
            assert other.post(path, json=body).status_code == 403, path
        assert dose_row(dose_id)[0] == "pending"


# ---------------------------------------------------------------- schedule conflicts + reschedule

def _levo_calcium(client):
    pid, _ = new_patient(client)
    b = base_time()
    add_medicine(pid, "Levothyroxine", [b])
    _, (ca_first, ca_second) = add_medicine(pid, "Calcium carbonate", [b, b + timedelta(hours=12)])
    return pid, b, ca_first


def test_conflicts_endpoint_reports_the_rule_its_source_and_the_unverified_caveat():
    with TestClient(app) as client:
        pid, b, ca_first = _levo_calcium(client)
        body = client.get(f"/patients/{pid}/schedule-conflicts").json()
        assert "UNREVIEWED" in body["review_status"] and "NOT been shown to be safe" in body["not_verified_note"]
        c = next(x for x in body["conflicts"] if x["kind"] == "timing")
        assert c["required_hours"] == 4 and c["medicines"] == ["Levothyroxine", "Calcium carbonate"]
        assert c["source"]["url"].startswith("https://dailymed.nlm.nih.gov/") and c["source"]["quote"]
        assert c["proposal"]["dose_id"] == ca_first
        assert datetime.fromisoformat(c["proposal"]["to"]) == b + timedelta(hours=4)


def test_accepting_a_proposal_moves_only_that_dose_audits_it_and_is_idempotent():
    with TestClient(app) as client:
        pid, b, ca_first = _levo_calcium(client)
        db = SessionLocal()
        db.add(ReminderLog(dose_id=ca_first, kind="lead", channel="push"))
        db.commit()
        db.close()

        # earlier than the rule allows: refused, nothing changes
        r = client.post(f"/doses/{ca_first}/reschedule", json={"to": (b + timedelta(hours=3)).isoformat()})
        assert r.status_code == 422 and "earlier" in r.json()["detail"]
        assert dose_row(ca_first)[1] == b

        target = (b + timedelta(hours=4)).isoformat()
        assert client.post(f"/doses/{ca_first}/reschedule", json={"to": target}).status_code == 200
        assert dose_row(ca_first)[1] == b + timedelta(hours=4)
        assert client.post(f"/doses/{ca_first}/reschedule", json={"to": target}).status_code == 200   # retry is a no-op

        assert "dose_rescheduled_by_rule" in audit_actions(pid)
        db = SessionLocal()
        try:
            entry = db.query(AuditLog).filter_by(patient_id=pid, action="dose_rescheduled_by_rule").one()
            detail = json.loads(entry.detail)
            assert detail["ruleset_version"] and detail["inputs"]["from"] == b.isoformat()      # original time preserved
            assert db.query(ReminderLog).filter_by(dose_id=ca_first).count() == 0                 # new time re-arms reminders
        finally:
            db.close()
        assert not [c for c in client.get(f"/patients/{pid}/schedule-conflicts").json()["conflicts"]
                    if c["kind"] == "timing"]


def test_the_server_does_not_trust_a_client_chosen_time_or_dose():
    with TestClient(app) as client:
        pid, b, ca_first = _levo_calcium(client)
        # past the same medicine's next dose
        assert client.post(f"/doses/{ca_first}/reschedule",
                           json={"to": (b + timedelta(hours=13)).isoformat()}).status_code == 422
        # garbage
        assert client.post(f"/doses/{ca_first}/reschedule", json={"to": "tomorrow"}).status_code == 400
        # a dose no rule supports moving
        _, (other,) = add_medicine(pid, "Telmisartan", [b + timedelta(hours=1)])
        assert client.post(f"/doses/{other}/reschedule",
                           json={"to": (b + timedelta(hours=9)).isoformat()}).status_code == 422
        # someone else's account
        stranger = TestClient(app)
        register_and_login(stranger)
        assert stranger.post(f"/doses/{ca_first}/reschedule", json={"to": (b + timedelta(hours=4)).isoformat()}).status_code == 403
        assert stranger.get(f"/patients/{pid}/schedule-conflicts").status_code == 403


def test_a_taken_dose_cannot_be_rescheduled():
    with TestClient(app) as client:
        pid, b, ca_first = _levo_calcium(client)
        client.post(f"/doses/{ca_first}/take")
        assert client.post(f"/doses/{ca_first}/reschedule", json={"to": (b + timedelta(hours=4)).isoformat()}).status_code == 409


# ---------------------------------------------------------------- missed-dose guidance endpoint

def test_missed_guidance_endpoint_returns_label_text_next_dose_and_never_double():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        now = clock.local_now("UTC")
        _, (missed, nxt) = add_medicine(pid, "Ciprofloxacin", [now - timedelta(hours=3), now + timedelta(hours=9)],
                                        states=["missed", "pending"])
        g = client.get(f"/doses/{missed}/missed-guidance").json()
        assert g["action"] == "take_now_ok" and g["never_double"] is True
        assert "6 hours prior to the next scheduled dose" in g["label_quote"]
        assert g["next_dose_at"] and g["ruleset_version"] and g["reviewed"] is False
        assert client.get(f"/doses/{nxt}/missed-guidance").status_code == 200       # a still-pending dose can be asked about


def test_missed_guidance_for_a_high_risk_medicine_has_no_catch_up_time():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        now = clock.local_now("UTC")
        _, (missed, _n) = add_medicine(pid, "Insulin glargine", [now - timedelta(hours=3), now + timedelta(hours=9)],
                                       states=["missed", "pending"])
        g = client.get(f"/doses/{missed}/missed-guidance").json()
        assert g["action"] == "contact_provider" and g["earliest_safe_time"] is None


def test_missed_guidance_access_control_and_state_check():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        _, (taken,) = add_medicine(pid, "Metformin", [base_time()], states=["taken"])
        assert client.get(f"/doses/{taken}/missed-guidance").status_code == 409
        stranger = TestClient(app)
        register_and_login(stranger)
        assert stranger.get(f"/doses/{taken}/missed-guidance").status_code == 403


# ---------------------------------------------------------------- push subscription

def test_push_public_key_reports_whether_push_is_configured(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.delenv("SMARTPOLI_VAPID_PRIVATE_KEY", raising=False)
        assert client.get("/push/public-key").json() == {"configured": False, "public_key": None}
        monkeypatch.setenv("SMARTPOLI_VAPID_PRIVATE_KEY", "priv")
        monkeypatch.setenv("SMARTPOLI_VAPID_PUBLIC_KEY", "pub")
        assert client.get("/push/public-key").json() == {"configured": True, "public_key": "pub"}


def test_push_subscribe_validates_deduplicates_and_is_owner_only():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        sub = {"endpoint": "https://push.example.test/x1", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}}
        assert client.post(f"/patients/{pid}/push/subscribe",
                           json={**sub, "endpoint": "http://insecure.example.test/x"}).status_code == 422
        assert client.post(f"/patients/{pid}/push/subscribe", json={"endpoint": "https://x.test/aaaa"}).status_code == 422
        assert client.post(f"/patients/{pid}/push/subscribe", json=sub).json()["count"] == 1
        assert client.post(f"/patients/{pid}/push/subscribe", json=sub).json()["count"] == 1      # same device: no duplicate
        stranger = TestClient(app)
        register_and_login(stranger)
        assert stranger.post(f"/patients/{pid}/push/subscribe", json=sub).status_code == 403
        assert client.post(f"/patients/{pid}/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json() == {"subscribed": False}
        db = SessionLocal()
        try:
            assert db.query(PushSubscription).filter_by(patient_id=pid).count() == 0
        finally:
            db.close()


def test_push_test_endpoint_needs_configuration_and_is_rate_limited(monkeypatch):
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        monkeypatch.delenv("SMARTPOLI_VAPID_PRIVATE_KEY", raising=False)
        assert client.post(f"/patients/{pid}/push/test").status_code == 503
        monkeypatch.setenv("SMARTPOLI_VAPID_PRIVATE_KEY", "priv")
        monkeypatch.setenv("SMARTPOLI_VAPID_PUBLIC_KEY", "pub")
        sent = []
        monkeypatch.setattr(main.webpush_service, "send", lambda e, p, a, payload: sent.append(payload) or "sent")
        client.post(f"/patients/{pid}/push/subscribe",
                    json={"endpoint": "https://push.example.test/t1", "keys": {"p256dh": "k" * 20, "auth": "a" * 12}})
        r = client.post(f"/patients/{pid}/push/test")
        assert r.status_code == 200 and r.json() == {"devices": 1, "sent": 1} and sent[0]["kind"] == "test"
        assert client.post(f"/patients/{pid}/push/test").status_code == 429


# ---------------------------------------------------------------- uploads

PNG = b"\x89PNG\r\n\x1a\n"


def test_prescription_upload_rejects_non_images_and_oversize_files(monkeypatch):
    monkeypatch.setattr(main, "read_prescription_image", lambda b: [{"text": "Tab Dolo 650mg 1-0-1 PC x5d", "confidence": 0.9}])
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        bad = client.post("/prescriptions/from-image", data={"patient_id": pid},
                          files={"file": ("rx.png", b"MZ\x90\x00 not an image", "image/png")})
        assert bad.status_code == 415
        html = client.post("/prescriptions/from-image", data={"patient_id": pid},
                           files={"file": ("rx.jpg", b"<html><script>x</script>", "image/jpeg")})
        assert html.status_code == 415
        monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 64)
        big = client.post("/prescriptions/from-image", data={"patient_id": pid},
                          files={"file": ("rx.png", PNG + b"0" * 200, "image/png")})
        assert big.status_code == 413
        monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 10 * 1024 * 1024)
        ok = client.post("/prescriptions/from-image", data={"patient_id": pid},
                         files={"file": ("rx.png", PNG + b"body", "image/png")})
        assert ok.status_code == 200
        empty = client.post("/prescriptions/from-image", data={"patient_id": pid},
                            files={"file": ("rx.png", b"", "image/png")})
        assert empty.status_code == 400


def test_uploading_for_someone_elses_patient_is_forbidden(monkeypatch):
    monkeypatch.setattr(main, "read_prescription_image", lambda b: [{"text": "Tab Dolo 650mg 1-0-1", "confidence": 0.9}])
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        stranger = TestClient(app)
        register_and_login(stranger)
        r = stranger.post("/prescriptions/from-image", data={"patient_id": pid},
                          files={"file": ("rx.png", PNG + b"body", "image/png")})
        assert r.status_code == 403


# ---------------------------------------------------------------- PWA / Android plumbing

def test_asset_links_are_empty_until_package_and_fingerprint_are_configured(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.delenv("SMARTPOLI_ANDROID_PACKAGE", raising=False)
        monkeypatch.delenv("SMARTPOLI_ANDROID_SHA256", raising=False)
        r = client.get("/.well-known/assetlinks.json")
        assert r.status_code == 200 and r.json() == [] and r.headers["content-type"].startswith("application/json")
        monkeypatch.setenv("SMARTPOLI_ANDROID_PACKAGE", "app.smartpoli.twa")
        monkeypatch.setenv("SMARTPOLI_ANDROID_SHA256", "aa:bb:cc, DD:EE:FF")
        links = client.get("/.well-known/assetlinks.json").json()
        assert links == [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": "app.smartpoli.twa",
                       "sha256_cert_fingerprints": ["AA:BB:CC", "DD:EE:FF"]}}]


def test_web_manifest_meets_the_twa_requirements():
    with TestClient(app) as client:
        r = client.get("/manifest.webmanifest")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
        m = r.json()
        assert m["name"] == "SmartPoli" and m["display"] == "standalone"
        assert m["scope"] == "/" and m["start_url"].startswith("/") and m["id"] == "/smartpoli"
        assert m["theme_color"].startswith("#") and m["background_color"].startswith("#")
        sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
        assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
        for icon in m["icons"]:                       # every declared icon is really served
            assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_index_page_links_the_manifest_and_push_script():
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'name="theme-color"' in html and "/static/push.js" in html and 'id="settingsNotifications"' in html


def test_service_worker_is_served_from_the_root_uncached_and_shows_push_notifications():
    with TestClient(app) as client:
        r = client.get("/sw-push.js")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-cache"
        assert "addEventListener('push'" in r.text and "showNotification" in r.text and "notificationclick" in r.text
        # It may only pass page loads straight through (Chrome needs a fetch handler to treat the site as installable);
        # it must never cache anything or answer from a cache.
        assert "caches." not in r.text and "cache.put" not in r.text and "respondWith(fetch(event.request))" in r.text


# ---------------------------------------------------------------- external cron trigger

def test_internal_sweep_endpoint_is_hidden_unless_the_secret_matches(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_reminder_sweep_job", lambda: calls.append(1))
    with TestClient(app) as client:
        monkeypatch.delenv("SMARTPOLI_CRON_SECRET", raising=False)
        assert client.post("/internal/reminder-sweep", headers={"X-Cron-Secret": "anything"}).status_code == 404   # not configured
        monkeypatch.setenv("SMARTPOLI_CRON_SECRET", "s3cret-value")
        assert client.post("/internal/reminder-sweep").status_code == 404                                         # no header
        assert client.post("/internal/reminder-sweep", headers={"X-Cron-Secret": "wrong"}).status_code == 404     # wrong
        assert client.get("/internal/reminder-sweep", headers={"X-Cron-Secret": "s3cret-value"}).status_code == 405
        assert calls == []
        ok = client.post("/internal/reminder-sweep", headers={"X-Cron-Secret": "s3cret-value"})
        assert ok.status_code == 200 and ok.json() == {"ok": True} and calls == [1]
        assert "/internal/reminder-sweep" not in client.get("/openapi.json").text        # not advertised


# ---------------------------------------------------------------- the dashboard shows TODAY, not 30 days

def test_dashboard_lists_only_todays_doses_and_the_next_day_starts_fresh(monkeypatch):
    day = (datetime.utcnow() + timedelta(days=40)).replace(hour=0, minute=0, second=0, microsecond=0)   # a future day: the real clock never sweeps it
    at = lambda d, h, m=0: day.replace(hour=h, minute=m) + timedelta(days=d)  # noqa: E731
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        # 30 generated days: yesterday (taken), today (taken, missed, pending, pending), and the next 29 days
        times = [at(-1, 8), at(0, 8), at(0, 12), at(0, 18), at(0, 21)] + [at(d, 8) for d in range(1, 30)]
        states = ["taken", "taken", "missed", "pending", "pending"] + ["pending"] * 29
        add_medicine(pid, "Telmisartan", times, states=states)

        monkeypatch.setattr(main, "patient_now", lambda db, patient_id, utc_now=None: at(0, 14))
        d = client.get(f"/patients/{pid}/dashboard").json()
        assert d["today"] == day.strftime("%Y-%m-%d")
        assert [x["scheduled_at"][11:16] for x in d["today_doses"]] == ["08:00", "12:00", "18:00", "21:00"]
        assert [x["state"] for x in d["today_doses"]] == ["taken", "missed", "pending", "pending"]
        assert d["left_today"] == 2 and d["today_doses"][0]["medicine_name"] == "Telmisartan"
        assert all(x["scheduled_at"].startswith(day.strftime("%Y-%m-%d")) for x in d["today_doses"])   # no yesterday, no tomorrow

        # the next day the same screen is simply that day's doses
        monkeypatch.setattr(main, "patient_now", lambda db, patient_id, utc_now=None: at(1, 7, 30))
        d2 = client.get(f"/patients/{pid}/dashboard").json()
        assert d2["today"] == (day + timedelta(days=1)).strftime("%Y-%m-%d") and [x["scheduled_at"][11:16] for x in d2["today_doses"]] == ["08:00"]
        assert d2["left_today"] == 1


def test_a_day_with_nothing_scheduled_returns_an_empty_today_not_the_whole_course(monkeypatch):
    day = (datetime.utcnow() + timedelta(days=40)).replace(hour=0, minute=0, second=0, microsecond=0)
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        add_medicine(pid, "Telmisartan", [day.replace(hour=8) + timedelta(days=n) for n in range(3, 10)])
        monkeypatch.setattr(main, "patient_now", lambda db, patient_id, utc_now=None: day.replace(hour=10))
        d = client.get(f"/patients/{pid}/dashboard").json()
        assert d["today_doses"] == [] and d["left_today"] == 0
        assert d["upcoming_doses"]                         # still available so the screen can say "next dose: …"


# ---------------------------------------------------------------- "Mark taken" must respect when a dose is due

def test_a_dose_a_day_away_cannot_be_marked_taken_and_nothing_changes():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        now = clock.local_now("UTC").replace(second=0, microsecond=0)
        _, (soon, tomorrow, next_week) = add_medicine(
            pid, "Metformin", [now + timedelta(hours=1), now + timedelta(hours=24), now + timedelta(days=7)])
        r = client.post(f"/doses/{tomorrow}/take")
        assert r.status_code == 409 and r.json()["detail"].startswith("Too early: this dose is for")
        assert "You can mark it taken from" in r.json()["detail"] and "tomorrow" in r.json()["detail"]
        assert dose_row(tomorrow)[0] == "pending" and dose_row(tomorrow)[2] is None
        assert client.post(f"/doses/{next_week}/take").status_code == 409
        assert "dose_taken" not in audit_actions(pid)
        # tapping repeatedly cannot chew through the course: only the dose that is due goes through
        assert client.post(f"/doses/{soon}/take").status_code == 200
        assert client.post(f"/doses/{tomorrow}/take").status_code == 409
        assert dose_row(tomorrow)[0] == "pending"


def test_the_take_window_edges():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        now = clock.local_now("UTC").replace(second=0, microsecond=0)
        _, (early_ok, early_no, late_ok, late_no) = add_medicine(
            pid, "Metformin", [now + timedelta(hours=1, minutes=59), now + timedelta(hours=2, minutes=30),
                               now - timedelta(hours=11), now - timedelta(hours=13)])
        assert client.post(f"/doses/{early_ok}/take").status_code == 200       # up to ~2 h early
        assert client.post(f"/doses/{early_no}/take").status_code == 409
        assert client.post(f"/doses/{late_ok}/take").status_code == 200        # a late tap still records the real time
        too_old = client.post(f"/doses/{late_no}/take")
        assert too_old.status_code == 409 and "too old" in too_old.json()["detail"]


def test_a_dose_that_is_not_due_cannot_be_snoozed_either():
    with TestClient(app) as client:
        pid, _ = new_patient(client)
        now = clock.local_now("UTC").replace(second=0, microsecond=0)
        _, (far, due) = add_medicine(pid, "Metformin", [now + timedelta(hours=24), now + timedelta(minutes=5)])
        assert client.post(f"/doses/{far}/snooze").status_code == 409
        assert client.post(f"/doses/{due}/snooze").status_code == 200


def test_voice_and_buttons_share_one_take_window():
    import scheduler
    import voice_tools
    assert voice_tools.TAKE_WINDOW_AFTER == scheduler.TAKE_EARLY_WINDOW == timedelta(hours=2)
    assert voice_tools.TAKE_WINDOW_BEFORE == scheduler.TAKE_LATE_WINDOW == timedelta(hours=12)


def test_the_too_early_message_names_the_day_the_dose_is_for():
    import scheduler
    from types import SimpleNamespace
    now = datetime(2026, 10, 5, 20, 0)
    dose = lambda dt: SimpleNamespace(scheduled_at=dt)  # noqa: E731
    assert scheduler.take_window_error(dose(datetime(2026, 10, 6, 1, 46)), now) == \
        "Too early: this dose is for tomorrow 01:46. You can mark it taken from 23:46."
    assert scheduler.take_window_error(dose(datetime(2026, 10, 6, 20, 0)), now) == \
        "Too early: this dose is for tomorrow 20:00. You can mark it taken from tomorrow 18:00."
    assert scheduler.take_window_error(dose(datetime(2026, 10, 9, 8, 0)), now).startswith("Too early: this dose is for 09 Oct 08:00.")
    assert scheduler.take_window_error(dose(datetime(2026, 10, 5, 21, 30)), now) is None            # 1.5 h early: fine
    assert scheduler.take_window_error(dose(datetime(2026, 10, 5, 22, 30)), now) == \
        "Too early: this dose is for 22:30. You can mark it taken from 20:30."
