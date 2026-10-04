"""
Import CDSCO's official lists of prohibited / restricted fixed-dose
combinations (FDCs) into data/cdsco_prohibited_fdc.json.

    pip install -r requirements-ingest.txt     # pdfplumber
    python tools/import_cdsco_fdc.py

Source: the CDSCO "Fixed Dose Combination (FDC)" page,
https://cdsco.gov.in/opencms/opencms/en/Drugs/FDC/ - the PDFs listed there
(each is a gazette-notification table: S.No., drug combination, S.O. number
and date). CDSCO publishes no API for this; this importer reads the public
PDFs once and records, for every row, the PDF it came from and the date it
was retrieved. Re-run it when CDSCO publishes a new list.

Honest limits: the lists name ingredient COMBINATIONS (sometimes with a
strength or dosage form). A product is only flagged when its ingredient
combination matches; whether a specific brand falls under a notification
must be checked against the notification itself.
"""

import json
import os
import re
import sys
from datetime import date

import httpx
import pdfplumber

BASE = "https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadNewsFiles/"
PAGE = "https://cdsco.gov.in/opencms/opencms/en/Drugs/FDC/"
LISTS = [
    {"file": "List of Prohibited FDC drugs_ 156 FDCs_ dated 02.08.2024.pdf",
     "title": "List of Prohibited FDC drugs - 156 FDCs", "status": "prohibited"},
    {"file": "List of Prohibited FDC drugs_ 14 FDCs_ dated 02.06.2023.pdf",
     "title": "List of Prohibited FDC drugs - 14 FDCs", "status": "prohibited"},
    {"file": "List of 16 FDCs banned dated 11.06.2026.pdf",
     "title": "List of 16 FDCs banned", "status": "prohibited"},
    {"file": "List of 02 Restricted FDC drugs_ dated 12.08.2024.pdf",
     "title": "List of 02 Restricted FDC drugs", "status": "restricted"},
]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cdsco_prohibited_fdc.json")


def main() -> int:
    entries = []
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for lst in LISTS:
            r = client.get(BASE + lst["file"].replace(" ", "%20"))
            r.raise_for_status()
            tmp = os.path.join(os.path.dirname(OUT), "_tmp.pdf")
            with open(tmp, "wb") as f:
                f.write(r.content)
            with pdfplumber.open(tmp) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables():
                        for row in table:
                            if not row or len(row) < 3 or not (row[0] or "").strip().rstrip(".").isdigit():
                                continue
                            name = re.sub(r"\s+", " ", (row[1] or "").replace("\n", " ")).strip()
                            notif = re.sub(r"\s+", " ", (row[2] or "").replace("\n", " ")).strip()
                            entries.append({
                                "combination": name,
                                "status": lst["status"],
                                "notification": notif,
                                "list_title": lst["title"],
                                "source_url": BASE + lst["file"].replace(" ", "%20"),
                            })
            os.remove(tmp)
            print(f"{lst['title']}: {sum(1 for e in entries if e['list_title'] == lst['title'])} rows")
    doc = {
        "source": "CDSCO - Fixed Dose Combination (FDC) page",
        "source_page": PAGE,
        "retrieved_on": date.today().isoformat(),
        "note": "Names are ingredient combinations as printed in the gazette tables; a match is by ingredient "
                "combination only. Check the cited notification for the exact product/strength/dosage form.",
        "entries": entries,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print(f"wrote {len(entries)} entries -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
