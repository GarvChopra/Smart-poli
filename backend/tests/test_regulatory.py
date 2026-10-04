"""
Regulatory transparency: CDSCO prohibited-FDC matching and openFDA adapters.

openFDA is mocked with fixed sample responses - the tests never touch the
network and use no real patient data. They check the HONESTY rules: a failed
lookup is never a restriction, a missing record is never "unsafe", an
ingredient match is never presented as a verified product.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import regulatory as reg  # noqa: E402
from db import SessionLocal, RegulatoryLookupCache  # noqa: E402
from main import app  # noqa: E402
from conftest import register_and_login  # noqa: E402

NOW = datetime(2026, 10, 4, 12, 0)

DRUGSFDA = {"results": [{
    "application_number": "ANDA201991", "sponsor_name": "EXAMPLE PHARMA",
    "products": [{"brand_name": "METFORMIN HYDROCHLORIDE", "dosage_form": "TABLET", "route": "ORAL",
                  "marketing_status": "Prescription",
                  "active_ingredients": [{"name": "METFORMIN HYDROCHLORIDE", "strength": "500MG"}]}]}]}
ENFORCEMENT = {"results": [{
    "recall_number": "D-0328-2025", "classification": "Class II", "status": "Ongoing",
    "reason_for_recall": "CGMP Deviations", "report_date": "20250416",
    "recalling_firm": "Example Pharma Inc.", "product_description": "Metformin ER Tablets 1000mg"}]}


@pytest.fixture()
def db():
    from db import init_db
    init_db()
    s = SessionLocal()
    s.query(RegulatoryLookupCache).delete()
    s.commit()
    yield s
    s.close()


class Fetcher:
    def __init__(self, drugsfda=DRUGSFDA, enforcement=ENFORCEMENT, fail=False):
        self.calls = []
        self.drugsfda, self.enforcement, self.fail = drugsfda, enforcement, fail

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if self.fail:
            raise ConnectionError("network down")
        return self.drugsfda if "drugsfda" in url else self.enforcement


# ---------------------------------------------------------------- CDSCO lists

def test_cdsco_data_matches_the_official_list_sizes():
    doc = reg.load_cdsco_fdc()
    assert len(doc["entries"]) == 188          # 156 + 14 + 16 prohibited, 2 restricted (CDSCO FDC page)
    by_status = {}
    for e in doc["entries"]:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
        assert e["source_url"].startswith("https://cdsco.gov.in/") and e["notification"]
    assert by_status == {"prohibited": 186, "restricted": 2}
    assert doc["retrieved_on"] and doc["source_page"].startswith("https://cdsco.gov.in/")


def test_prohibited_combination_is_identified_with_its_notification():
    r = reg.check_cdsco_fdc("Nimesulide + Paracetamol")
    assert r["status"] == "restricted_status_identified" and r["jurisdiction"] == "IN"
    assert r["matches"][0]["notification"].startswith("S.O. 2394")
    assert "ingredient combination only" in r["message"] and "pharmacist" in r["message"]
    assert r["source"]["url"].startswith("https://cdsco.gov.in/")


def test_matching_ignores_salts_strengths_and_case():
    r = reg.check_cdsco_fdc("tab NIMESULIDE 100mg + paracetamol 325 mg dispersible tablets")
    assert r["status"] == "restricted_status_identified"


def test_restricted_list_is_reported_as_restricted_not_prohibited():
    r = reg.check_cdsco_fdc("Naproxen + Pantoprazole")
    assert r["status"] == "restricted_status_identified"
    assert r["matches"][0]["list_status"] == "restricted"


def test_a_combination_not_on_the_lists_is_no_matching_record_not_approved():
    r = reg.check_cdsco_fdc("Amoxicillin + Clavulanic acid")
    assert r["status"] == "no_matching_record"
    assert "does not confirm" in r["message"]


def test_single_ingredient_medicines_are_outside_the_fdc_lists():
    r = reg.check_cdsco_fdc("Paracetamol")
    assert r["status"] == "no_matching_record" and "single-ingredient" in r["message"]


def test_combination_can_be_recognised_from_the_raw_prescription_line():
    r = reg.check_cdsco_fdc("Brandname", raw_text="Tab Nimesulide + Paracetamol 1-0-1 PC x5d")
    assert r["status"] == "restricted_status_identified"


def test_missing_cdsco_file_is_source_unavailable_not_a_restriction(tmp_path, monkeypatch):
    reg._cdsco_cache.clear()
    assert reg.load_cdsco_fdc(str(tmp_path / "nope.json")) is None
    monkeypatch.setattr(reg, "load_cdsco_fdc", lambda path=None: None)
    r = reg.check_cdsco_fdc("Nimesulide + Paracetamol")
    assert r["status"] == "source_unavailable"
    reg._cdsco_cache.clear()


def test_india_approval_is_never_guessed():
    r = reg.india_approval_check()
    assert r["status"] == "verification_pending" and "cdscoonline.gov.in" in r["source"]["url"]


# ---------------------------------------------------------------- openFDA approvals

def test_exact_ingredient_and_strength_is_a_verified_us_record():
    r = reg.check_us_fda_approval(None, "metformin", None, "500mg", Fetcher(), NOW)
    assert r["status"] == "verified_record_found" and r["jurisdiction"] == "US"
    assert r["records"][0]["application_number"] == "ANDA201991"
    assert "US status only" in r["message"] and "India" in r["message"]


def test_an_indian_brand_is_never_verified_by_its_ingredient_alone():
    r = reg.check_us_fda_approval(None, "metformin", "Glycomet", "500mg", Fetcher(), NOW)
    assert r["status"] == "potential_match" and "'Glycomet' itself is not a US product" in r["message"]
    # the same strength under a name the US register actually lists can be verified
    ok = reg.check_us_fda_approval(None, "metformin", "Metformin Hydrochloride", "500mg", Fetcher(), NOW)
    assert ok["status"] == "verified_record_found"
    out = reg.lookup_medicine(None, "Glycomet", None, "500mg", fetch=Fetcher(), now=NOW)
    assert out["generic"] == "metformin" and out["checks"]["us_fda_approval"]["status"] == "potential_match"


def test_ingredient_only_is_a_potential_match_never_verified():
    r = reg.check_us_fda_approval(None, "metformin", None, "850mg", Fetcher(), NOW)
    assert r["status"] == "potential_match"
    assert "exact product and strength were not confirmed" in r["message"]
    r2 = reg.check_us_fda_approval(None, "metformin", None, None, Fetcher(), NOW)
    assert r2["status"] == "potential_match"


def test_no_us_record_is_not_a_statement_about_india_or_safety():
    r = reg.check_us_fda_approval(None, "obscuredrug", None, None, Fetcher(drugsfda={"results": []}), NOW)
    assert r["status"] == "no_matching_record"
    assert "India" in r["message"]


def test_lookup_failure_is_source_unavailable_and_explicitly_not_a_restriction(db):
    r = reg.check_us_fda_approval(db, "metformin", None, None, Fetcher(fail=True), NOW)
    assert r["status"] == "source_unavailable"
    assert "not a restriction" in r["message"]
    r2 = reg.check_us_fda_recalls(db, "metformin", Fetcher(fail=True), NOW)
    assert r2["status"] == "source_unavailable" and "not a restriction" in r2["message"]


def test_openfda_404_means_zero_results_not_an_error(monkeypatch):
    class R:
        status_code = 404
        def raise_for_status(self): raise AssertionError("must not be called for 404")
        def json(self): return {}
    monkeypatch.setattr(reg.httpx, "get", lambda *a, **k: R())
    assert reg.default_fetch("https://api.fda.gov/drug/drugsfda.json", {"search": "x"}) == {"results": []}


def test_results_are_cached_and_second_call_does_not_hit_the_network(db):
    f = Fetcher()
    a = reg.check_us_fda_approval(db, "metformin", None, "500mg", f, NOW)
    b = reg.check_us_fda_approval(db, "metformin", None, "500mg", f, NOW + timedelta(hours=1))
    assert len(f.calls) == 1
    assert a["status"] == b["status"] == "verified_record_found"
    assert b["checked_at"] == a["checked_at"]            # still reports when it was actually checked


def test_expired_cache_refetches(db):
    f = Fetcher()
    reg.check_us_fda_approval(db, "metformin", None, None, f, NOW)
    reg.check_us_fda_approval(db, "metformin", None, None, f, NOW + timedelta(hours=25))
    assert len(f.calls) == 2


def test_stale_cache_is_used_and_labelled_when_the_source_goes_down(db):
    reg.check_us_fda_approval(db, "metformin", None, "500mg", Fetcher(), NOW)
    r = reg.check_us_fda_approval(db, "metformin", None, "500mg", Fetcher(fail=True), NOW + timedelta(days=3))
    assert r["status"] == "verified_record_found" and r["from_stale_cache"] is True


# ---------------------------------------------------------------- openFDA recalls

def test_ongoing_recall_is_reported_as_an_alert_with_batch_caveat(db):
    r = reg.check_us_fda_recalls(db, "metformin", Fetcher(), NOW)
    assert r["status"] == "regulatory_alert_found"
    assert r["alerts"][0]["classification"] == "Class II" and r["alerts"][0]["recall_number"] == "D-0328-2025"
    assert "specific batches" in r["message"]


def test_no_recall_is_not_a_clean_bill_of_health(db):
    r = reg.check_us_fda_recalls(db, "metformin", Fetcher(enforcement={"results": []}), NOW)
    assert r["status"] == "no_matching_record" and "says nothing about Indian products" in r["message"]


# ---------------------------------------------------------------- query safety, combos

def test_query_terms_are_sanitised_before_reaching_openfda():
    assert reg._safe_term('metformin" OR status:"x') == "metformin OR status x"
    assert reg._safe_term("a;b&c=d") == "a b c d"
    assert reg._safe_term("") is None and reg._safe_term("!!!") is None
    f = Fetcher()
    reg.check_us_fda_approval(None, 'metformin" OR 1:"1', None, None, f, NOW)
    assert '"' not in f.calls[0][1]["search"].split(":", 1)[1].strip('"')


def test_combination_products_are_not_looked_up_as_if_single_ingredient():
    f = Fetcher()
    out = reg.lookup_medicine(None, "Nimesulide + Paracetamol", fetch=f, now=NOW)
    assert out["checks"]["us_fda_approval"]["status"] == "verification_pending"
    assert out["checks"]["us_fda_recalls"]["status"] == "verification_pending"
    assert out["checks"]["cdsco_prohibited_fdc"]["status"] == "restricted_status_identified"
    assert f.calls == []


def test_us_and_india_results_are_kept_in_separate_jurisdictions():
    out = reg.lookup_medicine(None, "Metformin", None, "500mg", fetch=Fetcher(), now=NOW)
    j = {k: v["jurisdiction"] for k, v in out["checks"].items()}
    assert j == {"cdsco_prohibited_fdc": "IN", "india_approval": "IN", "us_fda_approval": "US", "us_fda_recalls": "US"}
    assert "does not mean" in out["disclaimer"] or "not mean" in out["disclaimer"]


# ---------------------------------------------------------------- endpoints

def test_regulatory_endpoint_for_a_patients_medicines_and_access_control(monkeypatch):
    monkeypatch.setattr(reg, "default_fetch", Fetcher())
    with TestClient(app) as client:
        register_and_login(client)
        pid = client.post("/patients", json={"name": "Reg Test"}).json()["id"]
        pres = client.post("/prescriptions", json={"patient_id": pid, "lines": ["Tab Metformin 500mg 1-0-1 x5d"]}).json()
        client.post(f"/prescriptions/{pres['prescription_id']}/confirm")
        r = client.get(f"/patients/{pid}/regulatory")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["medicines"] and body["disclaimer"]
        checks = body["medicines"][0]["checks"]
        assert set(checks) == {"cdsco_prohibited_fdc", "india_approval", "us_fda_approval", "us_fda_recalls"}

        # someone else cannot read this patient's medicines
        other = TestClient(app)
        register_and_login(other)
        assert other.get(f"/patients/{pid}/regulatory").status_code == 403
        # the free-text lookup needs an account and rejects junk
        assert TestClient(app).get("/regulatory/lookup", params={"name": "x"}).status_code == 401
        assert client.get("/regulatory/lookup", params={"name": "  "}).status_code == 422
        assert client.get("/regulatory/lookup", params={"name": "Nimesulide + Paracetamol"}).status_code == 200


# ---------------------------------------------------------------- prefetch (concurrent warm-up)

def test_prefetch_fetches_each_unique_request_once_then_everything_is_a_cache_hit(db):
    f = Fetcher()
    n = reg.prefetch(db, ["Metformin", "metformin", "Telma", "Nimesulide + Paracetamol", ""], fetch=f, now=NOW)
    assert n == 4                                      # metformin + telmisartan, approval + recall each; combo and blank skipped
    assert len(f.calls) == 4
    # the per-medicine checks that follow make no further calls
    reg.lookup_medicine(db, "Metformin", None, "500mg", fetch=f, now=NOW)
    reg.lookup_medicine(db, "Telma", None, None, fetch=f, now=NOW)
    assert len(f.calls) == 4
    assert reg.prefetch(db, ["Metformin", "Telma"], fetch=f, now=NOW + timedelta(hours=1)) == 0


def test_prefetch_failures_are_not_cached_and_do_not_raise(db):
    assert reg.prefetch(db, ["Metformin"], fetch=Fetcher(fail=True), now=NOW) == 0
    assert db.query(RegulatoryLookupCache).count() == 0
    r = reg.check_us_fda_approval(db, "metformin", None, None, Fetcher(fail=True), NOW)
    assert r["status"] == "source_unavailable"
