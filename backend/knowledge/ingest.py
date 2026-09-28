"""
SmartPoli — build the clinical knowledge index (run offline, commit the output).

    python knowledge/ingest.py            # from backend/

What was inspected before writing this (2026-09-28):
- MoHFW / DGHS Standard Treatment Guidelines: NO API — published only as PDFs
  on clinicalestablishments.mohfw.gov.in. The PDFs are text (Word exports,
  2017), not scans, and every condition follows the same sections:
  I. WHEN TO SUSPECT/RECOGNIZE, … IV. PREVENTION AND COUNSELING,
  V. OPTIMAL DIAGNOSTIC CRITERIA, INVESTIGATIONS, TREATMENT & REFERRAL
  CRITERIA (with a "REFER IMMEDIATELY" block), VI. WHO DOES WHAT …
  They are written for clinicians, so only patient-safe parts are indexed:
  the recognition overview, prevention & counseling, and referral / refer-
  immediately criteria. Diagnostic and drug-treatment sections are NOT
  indexed — SmartPoli never gives patients treatment from them.
- WHO SMART Guidelines: real machine-readable artifacts (FHIR IGs, DAK JSON
  schemas; the "DAK API" is schema documentation, not a live API), but only
  for antenatal care, family planning, HIV, immunization — nothing for the
  general adult symptoms SmartPoli handles, so nothing is ingested from it.
- MedlinePlus: a real web service (wsearch.nlm.nih.gov/ws/query, XML, max 85
  requests/min, cache results). Patient-friendly topic summaries with a
  date-created; attributed to MedlinePlus.gov as its terms require.

Output: knowledge/index.json — every item keeps source, publisher, document,
version/date, url, topic, kind and text.
"""

import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "index.json")
CACHE = os.path.join(HERE, ".cache")

STG_BASE = "https://clinicalestablishments.mohfw.gov.in/sites/default/files/standard-treatment-guidelines/"
STG_LISTING = "https://clinicalestablishments.mohfw.gov.in/en/standard-treatment-guidelines"
STG_LISTING_UPDATED = "2025-03-18"  # "Last Update" shown on the listing page when inspected

# Chosen for the symptoms SmartPoli handles (file id → document name)
STG_DOCS = {
    "3261": "Medicine (Respiratory)",
    "9721": "Gastroenterological Diseases",
    "3601": "Endocrinology",
    "4251": "ENT",
}
# Hypertension quick reference: numbered recommendations, not the I–VII layout
STG_HYPERTENSION = ("6591", "Hypertension — Quick Reference Guide")
# Inspected and deliberately not ingested:
#   3811 "Cardiovascular Diseases" is a paediatric congenital-heart-disease consensus, not adult chest pain;
#   1621 "Neurology" has no consistent condition/section layout (mostly reference lists).

MEDLINEPLUS_TOPICS = [
    "headache", "fever", "cough", "shortness of breath", "chest pain", "nausea and vomiting",
    "dizziness and vertigo", "abdominal pain", "rashes", "bleeding", "sore throat", "diarrhea",
    "asthma", "copd", "high blood pressure", "diabetes", "heartburn", "common cold", "back pain",
    "fatigue", "migraine", "indigestion",
]

# Section headings, with or without the Roman numeral (ENT omits it)
SECTIONS = [
    ("I", re.compile(r"^(I\.\s*)?WHEN TO SUSPECT", re.I)),
    ("II", re.compile(r"^(II\.\s*)?INCIDENCE", re.I)),
    ("III", re.compile(r"^(III\.\s*)?DIFFERENTIAL DIAGNOSIS", re.I)),
    ("IV", re.compile(r"^(IV\.\s*)?PREVENTION AND COUNSEL", re.I)),
    ("V", re.compile(r"^(V\.\s*)?OPTIMAL DIAGNOSTIC", re.I)),
    ("VI", re.compile(r"^(VI\.\s*)?WHO DOES WHAT", re.I)),
    ("VII", re.compile(r"^(VII\.\s*)?(FURTHER READING|RESOURCES REQUIRED)", re.I)),
]
PATIENT_SECTIONS = {"I": "overview", "IV": "prevention_counselling"}
# In section V, only the referral part — found after the section title itself
REFER_BLOCK = re.compile(r"(REFER IMMEDIATELY|Refer immediately|Indications for referral|Referral criteria\s*:|"
                         r"Refer (the patient )?to (a )?(higher|tertiary|specialist))", re.I)


def _section_of(line: str):
    for sid, pat in SECTIONS:
        if pat.match(line):
            return sid
    return None


def _get(url: str, name: str) -> bytes:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return open(path, "rb").read()
    req = urllib.request.Request(url, headers={"User-Agent": "SmartPoli-knowledge-ingest/1.0"})
    data = urllib.request.urlopen(req, timeout=120).read()
    open(path, "wb").write(data)
    return data


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Anything about medicines, doses or therapy is for clinicians only: SmartPoli
# never gives patients treatment from these guidelines (their own prescription
# is the only medicine source), so such sentences never enter a patient item.
_CLINICIAN_ONLY = re.compile(
    r"\b(mg|mcg|µg|ml/kg|mg/kg|dose|doses|dosage|tablets?|tab\.|capsules?|injections?|i\.?v\.?|intravenous|"
    r"intramuscular|inhaled|inhalers?|nebuli[sz]\w*|spacer|antibiotics?|steroids?|corticosteroids?|drugs?|"
    r"pharmaco\w*|therapy|prescri\w*|regimen|infusion|bronchodilators?|antihistamines?|decongestants?|"
    r"medications?|medicines?|agonists?|beta-agonists?|LABA|SABA|ICS|LAMA|inhalation|oxygen|ventilat\w*|"
    r"insulin|metformin|salbutamol|aspirin|paracetamol|ibuprofen|antacids?|ppis?|proton pump)\b", re.I)


def _patient_safe(text: str) -> str:
    sentences = re.split(r"(?<=[.;:!?])\s+|\s+(?=\d+\.\s)|\s*[•·�]\s*", _clean(text))
    return " ".join(x for x in sentences if x and not _CLINICIAN_ONLY.search(x)).strip()


def _cap(text: str, n: int) -> str:
    text = _clean(text)
    if len(text) <= n:
        return text
    cut = text[:n]
    return cut[:max(cut.rfind(". "), n // 2) + 1].strip()


# ---------------------------------------------------------------- MoHFW STGs

def ingest_stg(file_id: str, doc_name: str) -> list[dict]:
    from pypdf import PdfReader  # only needed for ingestion — see requirements-ingest.txt
    pdf = PdfReader(io.BytesIO(_get(STG_BASE + f"{file_id}.pdf", f"stg_{file_id}.pdf")))
    created = ""
    if pdf.metadata and pdf.metadata.get("/CreationDate"):
        m = re.match(r"D:(\d{4})(\d{2})(\d{2})", pdf.metadata["/CreationDate"])
        created = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
    lines = []  # (page_number, line)
    for i, page in enumerate(pdf.pages, start=1):
        for line in (page.extract_text() or "").splitlines():
            if line.strip():
                lines.append((i, line.strip()))

    items, condition, section, buf, start_page = [], None, None, [], None

    def flush():
        if not (condition and section and buf):
            return
        text = " ".join(buf)
        base = {
            "source": "mohfw_stg", "publisher": "Ministry of Health & Family Welfare (DGHS), Government of India",
            "document": f"Standard Treatment Guidelines — {doc_name}",
            "version": f"PDF {created or 'undated'}; listing updated {STG_LISTING_UPDATED}",
            "url": STG_BASE + f"{file_id}.pdf", "listing_url": STG_LISTING,
            "topic": condition.title(), "page": start_page, "audience": "patient",
        }
        if section in PATIENT_SECTIONS:
            safe = _patient_safe(text)
            if len(safe) >= 60:
                items.append({**base, "kind": PATIENT_SECTIONS[section], "text": _cap(safe, 1100)})
        elif section == "V":
            # only the referral part of section V; diagnosis and drug treatment stay out
            # an explicit "Referral criteria:" list is the clearest; else the other referral wording
            m = re.search(r"Referral criteria\s*:", text[120:], re.I)
            m = re.search(r"Referral criteria\s*:", text, re.I) if m else REFER_BLOCK.search(text, 120)
            if m:
                safe = _patient_safe(text[m.start(): m.start() + 2500])
                if len(safe) >= 60:
                    items.append({**base, "kind": "when_to_seek_care", "text": _cap(safe, 900)})

    for idx, (page, line) in enumerate(lines):
        sid = _section_of(line)
        if sid == "I":
            flush()
            # the condition name is the nearest ALL-CAPS line above
            for back in range(idx - 1, max(idx - 6, -1), -1):
                prev = lines[back][1].replace("NAME OF CONDITION:", "").strip()
                if prev and prev.upper() == prev and 3 < len(prev) < 90 and not _section_of(prev):
                    condition = prev
                    break
            section, buf, start_page = "I", [], page
            continue
        if sid and condition:
            flush()
            section, buf, start_page = sid, [], page
            continue
        if section:
            buf.append(line)
    flush()
    return items


def ingest_hypertension() -> list[dict]:
    """The hypertension quick reference is numbered recommendations; its patient
    education / lifestyle recommendations are the patient-safe part."""
    from pypdf import PdfReader
    fid, name = STG_HYPERTENSION
    pdf = PdfReader(io.BytesIO(_get(STG_BASE + f"{fid}.pdf", f"stg_{fid}.pdf")))
    text = "\n".join((p.extract_text() or "") for p in pdf.pages[:20])
    base = {"source": "mohfw_stg", "publisher": "Ministry of Health & Family Welfare (DGHS), Government of India",
            "document": f"Standard Treatment Guidelines — {name}", "version": f"listing updated {STG_LISTING_UPDATED}",
            "url": STG_BASE + f"{fid}.pdf", "listing_url": STG_LISTING, "topic": "Hypertension", "audience": "patient"}
    items = []
    for kind, start, end in (
        ("prevention_counselling", r"3\.\s*PATIENT EDUCATION", r"\n4\.\s*[A-Z]"),
        ("prevention_counselling", r"LIFESTYLE MODIFICATION", r"DRUG THERAPY|PHARMACOLOGICAL"),
    ):
        # skip the table-of-contents entry (dotted leaders), take the real section
        m = next((x for x in re.finditer(start, text) if "...." not in text[x.end(): x.end() + 80]), None)
        if not m:
            continue
        e = re.search(end, text[m.end():])
        chunk = _patient_safe(text[m.start(): m.end() + (e.start() if e else 1500)])
        if len(chunk) >= 60:
            items.append({**base, "kind": kind, "page": None, "text": _cap(chunk, 1100)})
    return items


# ---------------------------------------------------------------- MedlinePlus

SEEK_CARE = re.compile(r"(right away|emergency|call 911|seek medical|see (a|your) (doctor|health care)|"
                       r"health care provider|contact your|get medical help)", re.I)


def ingest_medlineplus(term: str) -> list[dict]:
    url = ("https://wsearch.nlm.nih.gov/ws/query?" +
           urllib.parse.urlencode({"db": "healthTopics", "term": term, "retmax": 1, "rettype": "topic",
                                   "tool": "smartpoli-knowledge-ingest"}))
    xml = _get(url, "mlp_" + re.sub(r"\W+", "_", term) + ".xml")
    root = ET.fromstring(xml)
    topic = root.find(".//health-topic")
    if topic is None:
        return []
    summary_html = html.unescape(topic.findtext("full-summary") or "")
    text = _clean(re.sub(r"<[^>]+>", " ", summary_html))
    text = re.sub(r"\s*(NIH|CDC):[^.]*$", "", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    seek = " ".join(s for s in sentences if SEEK_CARE.search(s))
    overview = " ".join(s for s in sentences if not SEEK_CARE.search(s))
    created = topic.get("date-created", "")
    if re.match(r"\d{2}/\d{2}/\d{4}", created):
        mm, dd, yyyy = created.split("/")
        created = f"{yyyy}-{mm}-{dd}"
    base = {"source": "medlineplus", "publisher": "MedlinePlus.gov (U.S. National Library of Medicine)",
            "document": f"MedlinePlus health topic: {topic.get('title')}", "version": f"topic created {created}; retrieved {date.today().isoformat()}",
            "url": topic.get("url"), "topic": topic.get("title"), "audience": "patient", "search_term": term}
    items = [{**base, "kind": "overview", "text": _cap(overview, 900)}] if overview else []
    if seek:
        items.append({**base, "kind": "when_to_seek_care", "text": _cap(seek, 700)})
    return items


def main():
    items = []
    for fid, name in STG_DOCS.items():
        got = ingest_stg(fid, name)
        print(f"MoHFW {name}: {len(got)} items", file=sys.stderr)
        items += got
    got = ingest_hypertension()
    print(f"MoHFW hypertension: {len(got)} items", file=sys.stderr)
    items += got
    for term in MEDLINEPLUS_TOPICS:
        got = ingest_medlineplus(term)
        print(f"MedlinePlus {term}: {len(got)} items", file=sys.stderr)
        items += got
        time.sleep(0.8)  # well under 85 requests/minute
    # de-duplicate (the same MedlinePlus topic can answer two search terms)
    seen, unique = set(), []
    for it in items:
        key = (it["source"], it["url"], it["topic"], it["kind"], it["text"][:80])
        if key not in seen:
            seen.add(key)
            unique.append(it)
    for i, it in enumerate(unique):
        it["id"] = f"k{i:04d}"
    index = {"built": date.today().isoformat(), "items": unique,
             "not_ingested": {"mohfw_3811": "Cardiovascular STG is a paediatric congenital heart disease consensus.",
                              "mohfw_1621": "Neurology STG has no consistent condition/section layout.",
                              "who_smart": "Only antenatal care, family planning, HIV and immunization guides are "
                                          "published as machine-readable DAK/FHIR artifacts; none cover the adult "
                                          "symptoms SmartPoli handles."}}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(unique)} items to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
