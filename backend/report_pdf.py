"""
SmartPoli — Feature 4: combined Doctor/Caregiver PDF report.

A real, downloadable PDF (ReportLab), not just the browser's Print-to-PDF.
Structure follows CLAUDE.md section 11 exactly: patient/prescription in
plain language, adherence, missed doses, symptom + triage history, drug
interactions, food warnings, doctor/caregiver notes, alerts, footer
disclaimer — colour-coded status, header band, footer disclaimer, written
against ReportLab's API.
"""

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

INK = colors.HexColor("#1F2E4A")
MOSS = colors.HexColor("#45664A")
MOSS_SOFT = colors.HexColor("#E1EAE0")
AMBER = colors.HexColor("#8C6420")
AMBER_SOFT = colors.HexColor("#EFE6D2")
ALARM = colors.HexColor("#AE2B22")
ALARM_SOFT = colors.HexColor("#F5DBD7")
LINE = colors.HexColor("#D3D8CC")

_STATUS_COLORS = {
    "verified": (MOSS, MOSS_SOFT), "low": (MOSS, MOSS_SOFT),
    "review": (AMBER, AMBER_SOFT), "moderate": (AMBER, AMBER_SOFT), "minor": (AMBER, AMBER_SOFT),
    "needs_confirmation": (ALARM, ALARM_SOFT), "emergency": (ALARM, ALARM_SOFT), "critical": (ALARM, ALARM_SOFT),
}


def _status_chip(styles, label: str) -> Paragraph:
    fg, bg = _STATUS_COLORS.get(label.lower(), (INK, colors.whitesmoke))
    style = ParagraphStyle("chip", parent=styles["Normal"], fontSize=8, textColor=fg,
                            backColor=bg, alignment=TA_CENTER, borderPadding=3)
    return Paragraph(label.replace("_", " ").upper(), style)


def build_report_pdf(data: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                             leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=16 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("Title2", parent=styles["Heading1"], fontSize=18,
                                  textColor=INK, spaceAfter=2)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10,
                                textColor=colors.grey, spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=INK,
                         spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("Body2", parent=styles["Normal"], fontSize=9.5, leading=13)
    small_grey = ParagraphStyle("SmallGrey", parent=styles["Normal"], fontSize=8,
                                 textColor=colors.grey)

    elements = []

    p = data["patient"]
    elements.append(Paragraph("SmartPoli Patient Care Report", title_style))
    subtitle = f"{p['name']}" + (f", {p['age']}" if p["age"] else "") + (f", {p['sex']}" if p["sex"] else "")
    elements.append(Paragraph(subtitle, sub_style))
    elements.append(Paragraph(f"Generated {data['generated_at']}", small_grey))
    elements.append(HRFlowable(width="100%", thickness=1.2, color=INK, spaceAfter=8, spaceBefore=6))

    # --- Alerts ---
    elements.append(Paragraph("Alerts", h2))
    if data["alerts"]:
        for a in data["alerts"]:
            elements.append(Paragraph(f"⚠ {a}", ParagraphStyle("alert", parent=body, textColor=ALARM)))
    else:
        elements.append(Paragraph("No alerts.", body))

    # --- Prescription ---
    elements.append(Paragraph("Prescription", h2))
    med_rows = [["Medicine", "Dose", "Schedule", "Food", "Duration", "Status"]]
    for pres in data["prescriptions"]:
        for m in pres["medicines"]:
            med_rows.append([
                m["name"] or m["raw_text"], f'{m["dose_amount"] or ""}{m["dose_unit"] or ""}',
                m["schedule_code"] or "-", m["food"],
                "ongoing" if m["duration_days"] is None else str(m["duration_days"]),
                _status_chip(styles, m["status"]),
            ])
    if len(med_rows) == 1:
        med_rows.append(["No medicines on file.", "", "", "", "", ""])
    med_table = Table(med_rows, colWidths=[42 * mm, 22 * mm, 26 * mm, 20 * mm, 22 * mm, 28 * mm])
    med_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF0EA")),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (5, 0), (5, -1), "CENTER"),
    ]))
    elements.append(med_table)

    # --- Adherence ---
    elements.append(Paragraph("Adherence", h2))
    a = data["adherence"]
    pct = "—" if a["adherence_percent"] is None else f"{a['adherence_percent']}%"
    elements.append(Paragraph(
        f"Overall adherence: <b>{pct}</b> &nbsp;&nbsp; Taken: {a['taken']} &nbsp;&nbsp; "
        f"Missed: {a['missed']} &nbsp;&nbsp; Skipped: {a['skipped']} &nbsp;&nbsp; Upcoming: {a['pending']}",
        body,
    ))

    # --- Missed doses ---
    elements.append(Paragraph("Missed doses", h2))
    if data["missed_doses"]:
        rows = [["When", "Medicine"]] + [[d["scheduled_at"], d["medicine_name"]] for d in data["missed_doses"]]
        t = Table(rows, colWidths=[60 * mm, 100 * mm])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF0EA"))]))
        elements.append(t)
    else:
        elements.append(Paragraph("None.", body))

    # --- Drug interactions ---
    elements.append(Paragraph("Drug interactions", h2))
    if data["interactions"]:
        for i in data["interactions"]:
            elements.append(Paragraph(
                f"<b>{i['drug_a']} + {i['drug_b']}</b> — {i['severity']}: {i['description']}", body))
    else:
        elements.append(Paragraph("No known interactions among active medicines.", body))

    # --- Food warnings ---
    elements.append(Paragraph("Food &amp; substance warnings", h2))
    if data["food_warnings"]:
        for w in data["food_warnings"]:
            elements.append(Paragraph(f"<b>{w['drug']}</b> — avoid {w['avoid']}: {w['description']}", body))
    else:
        elements.append(Paragraph("None.", body))

    # --- Symptom & triage history ---
    elements.append(Paragraph("Symptom &amp; triage history", h2))
    if data["symptom_history"]:
        rows = [["Date", "Symptoms", "Severity", "Action"]]
        for c in data["symptom_history"]:
            rows.append([c["created_at"][:10], ", ".join(c["symptoms"]), c["severity"], c["action"]])
        t = Table(rows, colWidths=[26 * mm, 44 * mm, 26 * mm, 64 * mm])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF0EA"))]))
        elements.append(t)
    else:
        elements.append(Paragraph("None.", body))

    # --- Doctor/Caregiver notes ---
    elements.append(Paragraph("Doctor / Caregiver notes", h2))
    if data["doctor_caregiver_notes"]:
        for n in data["doctor_caregiver_notes"]:
            elements.append(Paragraph(f"<b>{n['actor'].title()}</b> ({n['at'][:16]}): {n['note']}", body))
    else:
        elements.append(Paragraph("None.", body))

    elements.append(Spacer(1, 14))
    elements.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceAfter=4))
    elements.append(Paragraph(data["disclaimer"], small_grey))

    doc.build(elements)
    return buffer.getvalue()
