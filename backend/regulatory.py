"""
SmartPoli - medicine regulatory transparency.

Shows what can be VERIFIED from official sources, per jurisdiction, and says
plainly what could not be. It never declares a medicine legal, illegal or
safe. Four independent checks - one passing says nothing about the others:

  cdsco_prohibited_fdc  India: is this ingredient COMBINATION on a CDSCO
                        prohibited/restricted FDC list? (data/cdsco_prohibited_fdc.json,
                        imported from CDSCO's own gazette PDFs by
                        tools/import_cdsco_fdc.py - CDSCO has no API.)
  india_approval        India: CDSCO's approved-drug register (Drugs@CDSCO) has
                        no public data feed, so this is always
                        'verification_pending' with a link. Not guessed.
  us_fda_approval       US: openFDA Drugs@FDA (approval records).
  us_fda_recalls        US: openFDA drug enforcement reports (recalls).

Statuses (the only ones this module emits):
  verified_record_found           exact product evidence in the cited source
  potential_match                 only the ingredient (or a related product) matched
  regulatory_alert_found          an official recall/alert record exists
  restricted_status_identified    on a cited jurisdiction's prohibited/restricted list
  no_matching_record              source reachable, nothing matched - NOT proof of anything
  source_unavailable              lookup failed - NOT a restriction
  verification_pending            cannot be checked automatically

US FDA status is never presented as Indian status, or the reverse. A lookup
failure is never reported as a restriction. Results are cached and every
answer carries the date it was last checked.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cdsco_prohibited_fdc.json")
OPENFDA_BASE = "https://api.fda.gov/drug"
CACHE_TTL = timedelta(hours=24)
HTTP_TIMEOUT = 8.0

DISCLAIMER = (
    "This shows what could be checked in the cited official sources on the date shown. "
    "A missing record does not mean a medicine is illegal or unsafe, and an approval record does not mean "
    "a medicine is right for you. Ask your doctor or pharmacist."
)

# ---------------------------------------------------------------- CDSCO FDC lists

_DROP_WORDS = {
    "hydrochloride", "hcl", "sodium", "potassium", "magnesium", "calcium", "maleate", "besylate", "sulphate",
    "sulfate", "phosphate", "tartrate", "citrate", "mesylate", "succinate", "acetate", "bromide", "chloride",
    "trihydrate", "monohydrate", "dihydrate", "anhydrous", "ip", "bp", "usp", "eq", "to", "as", "tablet",
    "tablets", "capsule", "capsules", "injection", "dispersible", "hard", "gelatin", "or", "enteric", "coated",
    "film", "extended", "release", "sustained", "syrup", "suspension", "drops", "oral", "and", "with", "flavored",
    "flavoured", "of", "the", "form", "mg", "mcg", "g", "ml", "iu",
    # prescription shorthand that can trail a raw line ("Tab X + Y 1-0-1 PC x5d")
    "tab", "cap", "caps", "inj", "syp", "od", "bd", "tds", "qid", "hs", "sos", "prn", "pc", "ac", "stat",
    "x", "d", "day", "days", "daily", "continue", "after", "before", "food", "meals",
}


def _norm_component(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|%|gm)?", " ", t)
    words = [w for w in re.findall(r"[a-z][a-z\-]*", t) if w not in _DROP_WORDS]
    return " ".join(words).strip()


def _components(text: str) -> frozenset:
    parts = [_norm_component(p) for p in re.split(r"\s*\+\s*", text or "")]
    return frozenset(p for p in parts if p)


_cdsco_cache: dict = {}


def load_cdsco_fdc(path: Optional[str] = None) -> dict:
    p = path or _DATA
    if p not in _cdsco_cache:
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
            for e in doc["entries"]:
                e["_components"] = _components(e["combination"])
            _cdsco_cache[p] = doc
        except (OSError, ValueError, KeyError):
            _cdsco_cache[p] = None
    return _cdsco_cache[p]


def check_cdsco_fdc(name: str, raw_text: Optional[str] = None, data: Optional[dict] = None) -> dict:
    doc = data if data is not None else load_cdsco_fdc()
    base = {"jurisdiction": "IN", "check": "cdsco_prohibited_fdc"}
    if not doc:
        return {**base, "status": "source_unavailable",
                "message": "The CDSCO prohibited-combination list is not loaded on this server."}
    source = {"title": "CDSCO - Fixed Dose Combination (FDC) lists", "url": doc["source_page"],
              "data_as_of": doc["retrieved_on"]}
    comps = _components(name)
    if len(comps) < 2 and raw_text:
        comps = _components(raw_text)
    if len(comps) < 2:
        return {**base, "status": "no_matching_record", "source": source,
                "message": "This looks like a single-ingredient medicine, so the combination (FDC) lists do not apply "
                           "to it. This is not a statement about its approval."}
    matches = [e for e in doc["entries"] if e["_components"] == comps]
    if matches:
        return {
            **base, "status": "restricted_status_identified", "source": source,
            "message": "The ingredient combination in this medicine's name appears on a CDSCO "
                       f"{matches[0]['status']} fixed-dose-combination list. The match is by ingredient combination "
                       "only - check the cited notification for the exact product, strength and form, "
                       "and ask your doctor or pharmacist.",
            "matches": [{"combination": m["combination"], "list_status": m["status"], "notification": m["notification"],
                         "list_title": m["list_title"], "source_url": m["source_url"]} for m in matches],
        }
    return {**base, "status": "no_matching_record", "source": source,
            "message": f"No match for this ingredient combination in the CDSCO lists loaded on {doc['retrieved_on']}. "
                       "This does not confirm the product is approved or safe."}


def india_approval_check() -> dict:
    return {
        "jurisdiction": "IN", "check": "india_approval", "status": "verification_pending",
        "message": "CDSCO's register of approved drugs (Drugs@CDSCO) has no public data feed, so SmartPoli cannot "
                   "check it automatically. Search it yourself using the link.",
        "source": {"title": "Drugs@CDSCO (CDSCO approved drugs)", "url": "https://cdscoonline.gov.in/CDSCO/cdscoDrugs"},
    }


# ---------------------------------------------------------------- openFDA

def _safe_term(term: str) -> Optional[str]:
    """Only plain words reach the openFDA query string (no query-syntax injection)."""
    t = re.sub(r"[^A-Za-z0-9 \-]", " ", term or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60] if t else None


def default_fetch(url: str, params: dict) -> Optional[dict]:
    """GET -> parsed JSON. openFDA answers 404 for 'zero results'; that is a valid
    empty answer ({'results': []}). Network errors / 5xx / 429 raise."""
    key = os.getenv("OPENFDA_API_KEY")
    if key:
        params = {**params, "api_key": key}
    r = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
    if r.status_code == 404:
        return {"results": []}
    r.raise_for_status()
    return r.json()


def _cached_fetch(db, source: str, key: str, url: str, params: dict, fetch: Callable,
                  now: Optional[datetime] = None) -> tuple[Optional[dict], Optional[datetime], bool]:
    """(payload, fetched_at, stale). payload None = no data and none cached."""
    from db import RegulatoryLookupCache
    now = now or datetime.utcnow()
    row = None
    if db is not None:
        row = (db.query(RegulatoryLookupCache)
               .filter(RegulatoryLookupCache.source == source, RegulatoryLookupCache.query_key == key)
               .order_by(RegulatoryLookupCache.fetched_at.desc()).first())
        if row and now - row.fetched_at < CACHE_TTL:
            return json.loads(row.payload), row.fetched_at, False
    try:
        payload = fetch(url, params)
    except Exception as e:  # noqa: BLE001 - any failure must degrade, never raise
        logger.warning("openFDA lookup failed (%s): %s", source, type(e).__name__)
        if row:
            return json.loads(row.payload), row.fetched_at, True
        return None, None, False
    if db is not None:
        db.add(RegulatoryLookupCache(source=source, query_key=key, payload=json.dumps(payload), fetched_at=now))
        db.commit()
    return payload, now, False


def _strength_key(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _approval_request(term: str, is_generic: bool) -> tuple:
    field = "products.active_ingredients.name" if is_generic else "products.brand_name"
    return ("openfda_drugsfda", f"{field}:{term.lower()}", f"{OPENFDA_BASE}/drugsfda.json",
            {"search": f'{field}:"{term}"', "limit": 25})


def _recall_request(term: str) -> tuple:
    return ("openfda_enforcement", f"generic:{term.lower()}", f"{OPENFDA_BASE}/enforcement.json",
            {"search": f'openfda.generic_name:"{term}" AND status:"Ongoing"', "limit": 5, "sort": "report_date:desc"})


def prefetch(db, names: list[str], fetch: Optional[Callable] = None, now: Optional[datetime] = None,
             max_workers: int = 6) -> int:
    """Warm the cache for several medicines at once. The lookups are slow network
    calls, so run the uncached ones concurrently (DB access stays on the calling
    thread), then the per-medicine checks that follow are instant cache hits.
    Failures are simply not cached - the checks later report source_unavailable.
    Returns how many requests were fetched."""
    from concurrent.futures import ThreadPoolExecutor
    from db import RegulatoryLookupCache
    from interactions import normalize_for_interactions
    fetch = fetch or default_fetch
    now = now or datetime.utcnow()
    wanted, seen = [], set()
    for name in names:
        if not name or "+" in name:
            continue
        generic = normalize_for_interactions(name)
        term = _safe_term(generic)
        if not term:
            continue
        for req in (_approval_request(term, True), _recall_request(term)):
            if (req[0], req[1]) not in seen:
                seen.add((req[0], req[1]))
                wanted.append(req)
    missing = []
    for req in wanted:
        row = (db.query(RegulatoryLookupCache)
               .filter(RegulatoryLookupCache.source == req[0], RegulatoryLookupCache.query_key == req[1])
               .order_by(RegulatoryLookupCache.fetched_at.desc()).first())
        if not (row and now - row.fetched_at < CACHE_TTL):
            missing.append(req)
    if not missing:
        return 0

    def run(req):
        try:
            return req, fetch(req[2], dict(req[3]))
        except Exception:  # noqa: BLE001
            return req, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(run, missing))
    fetched = 0
    for req, payload in results:
        if payload is not None:
            db.add(RegulatoryLookupCache(source=req[0], query_key=req[1], payload=json.dumps(payload), fetched_at=now))
            fetched += 1
    db.commit()
    return fetched


def check_us_fda_approval(db, generic: Optional[str], brand: Optional[str], strength: Optional[str],
                          fetch: Optional[Callable] = None, now: Optional[datetime] = None) -> dict:
    fetch = fetch or default_fetch
    base = {"jurisdiction": "US", "check": "us_fda_approval"}
    src = {"title": "openFDA Drugs@FDA", "url": "https://open.fda.gov/apis/drug/drugsfda/"}
    term = _safe_term(generic or brand or "")
    if not term:
        return {**base, "status": "no_matching_record", "source": src,
                "message": "No medicine name to look up."}
    source_name, key, url, params = _approval_request(term, bool(generic))
    payload, fetched_at, stale = _cached_fetch(db, source_name, key, url, params, fetch, now)
    if payload is None:
        return {**base, "status": "source_unavailable", "source": src,
                "message": "The US FDA approvals lookup could not be reached just now. This is not a restriction - try again later."}
    meta = {"checked_at": fetched_at.isoformat() if fetched_at else None, "from_stale_cache": stale}
    results = payload.get("results", [])
    if not results:
        return {**base, **meta, "status": "no_matching_record", "source": src,
                "message": f"No US FDA approval record found for '{term}'. This does not say anything about "
                           "its status in India or its safety."}
    want = _strength_key(strength)
    exact, related = [], []
    for app in results:
        for prod in app.get("products", []):
            ings = prod.get("active_ingredients", [])
            rec = {"application_number": app.get("application_number"), "sponsor": app.get("sponsor_name"),
                   "brand_name": prod.get("brand_name"), "dosage_form": prod.get("dosage_form"),
                   "route": prod.get("route"), "marketing_status": prod.get("marketing_status"),
                   "ingredients": [{"name": i.get("name"), "strength": i.get("strength")} for i in ings]}
            same_ingredient = (not generic) or any(term.lower() in (i.get("name") or "").lower() for i in ings)
            strength_hit = bool(want) and any(_strength_key(i.get("strength")) == want for i in ings)
            (exact if (same_ingredient and strength_hit) else related).append(rec)
    # A brand name (e.g. an Indian brand mapped to its ingredient) is not itself a US product:
    # ingredient + strength can only ever be a potential match for it, never a verified one.
    if exact and brand and not any((r["brand_name"] or "").strip().lower() == brand.strip().lower() for r in exact):
        return {**base, **meta, "status": "potential_match", "source": src, "records": exact[:5],
                "message": f"The US FDA has approved {term} at this strength, but '{brand}' itself is not a US product, "
                           "so the exact product was not confirmed. This is US status only - it says nothing about the "
                           "Indian brand."}
    if exact:
        return {**base, **meta, "status": "verified_record_found", "source": src, "records": exact[:5],
                "message": "A US FDA record matches this ingredient and strength. This is US status only - it does not "
                           "mean this exact product is approved in India."}
    return {**base, **meta, "status": "potential_match", "source": src, "records": related[:5],
            "message": "The US FDA has approved products containing this ingredient, but the exact product and "
                       "strength were not confirmed. This is US status only."}


def check_us_fda_recalls(db, generic: Optional[str], fetch: Optional[Callable] = None,
                         now: Optional[datetime] = None) -> dict:
    fetch = fetch or default_fetch
    base = {"jurisdiction": "US", "check": "us_fda_recalls"}
    src = {"title": "openFDA drug enforcement (recall) reports", "url": "https://open.fda.gov/apis/drug/enforcement/"}
    term = _safe_term(generic or "")
    if not term:
        return {**base, "status": "no_matching_record", "source": src, "message": "No ingredient to look up."}
    source_name, key, url, params = _recall_request(term)
    payload, fetched_at, stale = _cached_fetch(db, source_name, key, url, params, fetch, now)
    if payload is None:
        return {**base, "status": "source_unavailable", "source": src,
                "message": "The US FDA recall lookup could not be reached just now. This is not a restriction - try again later."}
    meta = {"checked_at": fetched_at.isoformat() if fetched_at else None, "from_stale_cache": stale}
    results = payload.get("results", [])
    if not results:
        return {**base, **meta, "status": "no_matching_record", "source": src,
                "message": f"No ongoing US FDA recall found for '{term}' on the date checked. Recalls are batch- and "
                           "manufacturer-specific, and this says nothing about Indian products."}
    return {**base, **meta, "status": "regulatory_alert_found", "source": src,
            "alerts": [{"recall_number": r.get("recall_number"), "classification": r.get("classification"),
                        "reason": r.get("reason_for_recall"), "report_date": r.get("report_date"),
                        "firm": r.get("recalling_firm"), "product": (r.get("product_description") or "")[:200]}
                       for r in results],
            "message": "There are ongoing US FDA recall records involving this ingredient. Recalls apply to specific "
                       "batches from specific manufacturers - they are not a statement about every product."}


# ---------------------------------------------------------------- combined lookup

def lookup_medicine(db, name: str, raw_text: Optional[str] = None, strength: Optional[str] = None,
                    fetch: Optional[Callable] = None, now: Optional[datetime] = None) -> dict:
    from interactions import normalize_for_interactions
    generic = normalize_for_interactions(name) if name else None
    brand = name if (name and generic and generic.lower() != name.strip().lower()) else None
    # A name that is just a '+' combination has no single ingredient to look up in the US lists.
    combo = bool(name and "+" in name)
    return {
        "medicine": name,
        "generic": None if combo else generic,
        "checks": {
            "cdsco_prohibited_fdc": check_cdsco_fdc(name or "", raw_text),
            "india_approval": india_approval_check(),
            "us_fda_approval": (check_us_fda_approval(db, None if combo else generic, brand, strength, fetch, now)
                                if not combo else {"jurisdiction": "US", "check": "us_fda_approval",
                                                   "status": "verification_pending",
                                                   "message": "Combination product: look up each ingredient separately."}),
            "us_fda_recalls": (check_us_fda_recalls(db, generic, fetch, now) if not combo else
                               {"jurisdiction": "US", "check": "us_fda_recalls", "status": "verification_pending",
                                "message": "Combination product: look up each ingredient separately."}),
        },
        "disclaimer": DISCLAIMER,
    }
