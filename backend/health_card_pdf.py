"""
SmartPoli — printable wallet health card (PDF).

One A4 page with the card's front and back at real ID-card size
(85.6 x 54 mm), with cut guides, meant to be printed, cut out and kept in a
wallet. Shows only the fields the patient chose to share, same as the
public QR page; the QR itself carries only the token URL.
"""

import io
from datetime import datetime
from typing import Optional

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

CARD_W, CARD_H = 85.6 * mm, 54 * mm
TEAL = HexColor("#12876F")
TEAL_SOFT = HexColor("#DFF1EA")
INK = HexColor("#16241F")
INK_SOFT = HexColor("#5C6E68")
ALARM = HexColor("#D3402A")
ALARM_SOFT = HexColor("#FBE2DD")
LINE = HexColor("#E3EBE7")


def _card_outline(c: canvas.Canvas, x: float, y: float) -> None:
    c.setStrokeColor(LINE)
    c.setFillColor(HexColor("#FFFFFF"))
    c.roundRect(x, y, CARD_W, CARD_H, 3.2 * mm, stroke=1, fill=1)
    # cut guides at the corners, outside the card
    c.setStrokeColor(HexColor("#BBBBBB"))
    c.setLineWidth(0.3)
    for cx, cy in ((x, y), (x + CARD_W, y), (x, y + CARD_H), (x + CARD_W, y + CARD_H)):
        dx = -1 if cx == x else 1
        dy = -1 if cy == y else 1
        c.line(cx + dx * 1.5 * mm, cy, cx + dx * 5 * mm, cy)
        c.line(cx, cy + dy * 1.5 * mm, cx, cy + dy * 5 * mm)
    c.setLineWidth(1)


def _text(c, x, y, s, size=7, color=INK, bold=False):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, s)


def _wrapped(c, x, y, s, width, size=6.5, color=INK, max_lines=2) -> float:
    lines = simpleSplit(s, "Helvetica", size, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip()[:-1] + "…"
    c.setFillColor(color)
    c.setFont("Helvetica", size)
    for line in lines:
        c.drawString(x, y, line)
        y -= size + 1.5
    return y


def _front(c, x, y, data, card_url, photo: Optional[bytes]) -> None:
    p, share = data["patient"], data["profile"]["share"]
    pad = 4 * mm
    _card_outline(c, x, y)

    _text(c, x + pad, y + CARD_H - pad - 2 * mm, "SmartPoli", 8, TEAL, bold=True)
    _text(c, x + pad + 15 * mm, y + CARD_H - pad - 2 * mm, "EMERGENCY HEALTH ID", 5.5, INK_SOFT, bold=True)

    # photo or initials
    ax, ay, ar = x + pad + 7 * mm, y + CARD_H - pad - 14 * mm, 7 * mm
    if photo:
        c.saveState()
        path = c.beginPath()
        path.circle(ax, ay, ar)
        c.clipPath(path, stroke=0)
        c.drawImage(ImageReader(io.BytesIO(photo)), ax - ar, ay - ar, 2 * ar, 2 * ar, mask="auto")
        c.restoreState()
    else:
        c.setFillColor(TEAL_SOFT)
        c.circle(ax, ay, ar, stroke=0, fill=1)
        initials = "".join(w[0] for w in p["name"].split()[:2]).upper()
        c.setFillColor(TEAL)
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(ax, ay - 4, initials)

    tx = x + pad + 16 * mm
    _text(c, tx, y + CARD_H - pad - 11 * mm, p["name"][:26], 9.5, INK, bold=True)
    details = " · ".join(str(v) for v in (p["age"], p["sex"]) if v)
    _text(c, tx, y + CARD_H - pad - 15 * mm, details, 6.5, INK_SOFT)
    if share["blood_group"] and p.get("blood_group"):
        _text(c, tx, y + CARD_H - pad - 19 * mm, f"Blood group  {p['blood_group']}", 6.5, INK, bold=True)

    if share["allergies"]:
        c.setFillColor(ALARM_SOFT if p["allergies"] else TEAL_SOFT)
        c.roundRect(x + pad, y + pad + 5 * mm, 50 * mm, 8 * mm, 1.5 * mm, stroke=0, fill=1)
        label = f"ALLERGY: {p['allergies']}" if p["allergies"] else "No known allergies recorded"
        _wrapped(c, x + pad + 2 * mm, y + pad + 10.2 * mm, label, 46 * mm, 6,
                 ALARM if p["allergies"] else TEAL, max_lines=2)
    if data["has_emergency_triage_history"]:
        _text(c, x + pad, y + pad + 1 * mm, "● Emergency history on record", 5.5, ALARM, bold=True)

    qr_img = qrcode.make(card_url, border=1)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    q = 22 * mm
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), x + CARD_W - pad - q, y + pad + 3 * mm, q, q)
    _text(c, x + CARD_W - pad - q, y + pad, "Scan in an emergency", 5, INK_SOFT)


def _back(c, x, y, data) -> None:
    p, profile = data["patient"], data["profile"]
    share = profile["share"]
    pad = 4 * mm
    _card_outline(c, x, y)
    col_w = (CARD_W - 2 * pad - 3 * mm) / 2

    meds = [m["name"] for m in data["scheduled_medicines"] + data["as_needed_medicines"]]
    med_text = ", ".join(meds[:4]) + (f" +{len(meds) - 4} more" if len(meds) > 4 else "") if meds else "—"
    blocks = [
        ("EMERGENCY CONTACT", p["emergency_contact"] or "—", share["emergency_contact"]),
        ("CAREGIVER", ", ".join(data["care_team"]["caregivers"]) or "—", share["caregiver"]),
        ("DOCTOR / CARE TEAM", ", ".join(data["care_team"]["doctors"]) or "—", share["doctor"]),
        ("CONDITIONS", profile["conditions"] or "—", share["conditions"]),
        ("CURRENT MEDICINES", med_text, share["medicines"]),
        ("INSTRUCTIONS", profile["instructions"] or "—", share["instructions"]),
    ]
    blocks = [b for b in blocks if b[2]]
    for i, (label, value, _) in enumerate(blocks):
        bx = x + pad + (i % 2) * (col_w + 3 * mm)
        by = y + CARD_H - pad - 3 * mm - (i // 2) * 12 * mm
        _text(c, bx, by, label, 5, TEAL, bold=True)
        _wrapped(c, bx, by - 7, value, col_w, 6.2, INK, max_lines=2)

    updated = datetime.fromisoformat(data["last_updated"]).strftime("%d %b %Y")
    c.setStrokeColor(LINE)
    c.line(x + pad, y + pad + 4 * mm, x + CARD_W - pad, y + pad + 4 * mm)
    _text(c, x + pad, y + pad + 0.5 * mm, f"Last updated {updated}", 5.5, INK_SOFT)
    card_id = data["card_path"].rsplit("/", 1)[-1][-4:].upper()
    _text(c, x + CARD_W - pad - 38 * mm, y + pad + 0.5 * mm,
          f"Active · verified by SmartPoli · ID …{card_id}", 5.5, TEAL, bold=True)


def build_wallet_card_pdf(data: dict, card_url: str, photo: Optional[bytes]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    c.setTitle("SmartPoli emergency health card")

    _text(c, 20 * mm, page_h - 20 * mm, "SmartPoli — Emergency health card", 14, INK, bold=True)
    _text(c, 20 * mm, page_h - 27 * mm,
          "Print at 100% scale, cut along the corner marks, fold along the middle, keep in your wallet.",
          9, INK_SOFT)

    x = (page_w - 2 * CARD_W) / 2
    y = page_h - 40 * mm - CARD_H
    _front(c, x, y, data, card_url, photo)
    _back(c, x + CARD_W, y, data)

    _text(c, 20 * mm, y - 12 * mm,
          "The QR opens only the emergency information you chose to share. Revoke it in the app if this card is lost.",
          8, INK_SOFT)
    c.showPage()
    c.save()
    return buf.getvalue()
