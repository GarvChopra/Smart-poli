"""
SmartPoli — the public, no-login emergency page a QR scan opens.

Plain server-rendered HTML (not the SPA) so it opens instantly on any phone
with nothing cached. Shows only what the patient chose to share on their
health card (serializers.card_profile share choices); the name is always
shown. Every patient-entered value is HTML-escaped — this page is served
from the app's own origin, where the SPA keeps its login token.
"""

from datetime import datetime
from html import escape


INACTIVE_CARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Emergency card not active</title></head>
<body style="font-family:system-ui,sans-serif;padding:24px;max-width:480px;">
<h1 style="color:#AE2B22;font-size:22px;">This emergency card is no longer active</h1>
<p>The link may have been replaced by a newer card. In an emergency, call 112.</p>
</body></html>"""


def _e(value) -> str:
    return escape(str(value)) if value not in (None, "") else ""


def _section(title: str, body: str) -> str:
    return f"<section><h2>{title}</h2>{body}</section>"


def _text_or_none(value, css: str = "") -> str:
    if not value:
        return '<p class="muted">None recorded.</p>'
    return f'<div class="{css}">{_e(value)}</div>' if css else f"<p>{_e(value)}</p>"


def _medicine_list(items) -> str:
    if not items:
        return '<p class="muted">None on file.</p>'
    lis = "".join(
        f'<li><strong>{_e(i["name"])}</strong>'
        f'{" " + _e(i["dose_amount"]) + _e(i.get("dose_unit")) if i.get("dose_amount") else ""}'
        f'{" — " + _e(i["schedule_code"]) if i.get("schedule_code") else ""}'
        f'{" (as needed)" if i.get("prn") else ""}</li>'
        for i in items
    )
    return f"<ul>{lis}</ul>"


def _names(names) -> str:
    return f"<p>{_e(', '.join(names))}</p>" if names else '<p class="muted">None linked.</p>'


def render_public_card(data: dict) -> str:
    p = data["patient"]
    profile = data["profile"]
    share = profile["share"]
    card_path = data["card_path"]

    photo = (f'<img class="photo" src="{card_path}/photo" alt="">'
             if share["photo"] and profile["has_photo"] else "")
    sub = ", ".join(x for x in (
        _e(p["age"]), _e(p["sex"]),
        f'blood group {_e(p["blood_group"])}' if share["blood_group"] and p.get("blood_group") else "",
    ) if x)
    flag = ('<div class="flag">This patient has a history of an EMERGENCY-graded symptom check in SmartPoli.</div>'
            if data["has_emergency_triage_history"] else "")

    sections = []
    if share["allergies"]:
        sections.append(_section("Allergies", _text_or_none(p["allergies"], "allergy")))
    if share["conditions"]:
        sections.append(_section("Medical conditions", _text_or_none(profile["conditions"])))
    if share["medicines"]:
        meds = data["scheduled_medicines"] + [dict(m, prn=True) for m in data["as_needed_medicines"]]
        sections.append(_section("Current medicines", _medicine_list(meds)))
    if share["emergency_contact"]:
        sections.append(_section("Emergency contact", _text_or_none(p["emergency_contact"])))
    if share["caregiver"]:
        sections.append(_section("Caregiver", _names(data["care_team"]["caregivers"])))
    if share["doctor"]:
        sections.append(_section("Doctor / care team", _names(data["care_team"]["doctors"])))
    if share["instructions"] and profile["instructions"]:
        sections.append(_section("Emergency instructions", _text_or_none(profile["instructions"], "instructions")))

    updated = datetime.fromisoformat(data["last_updated"]).strftime("%d %b %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Emergency card — {_e(p['name'])}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #fff; color: #1a1a1a; margin: 0; padding: 24px; max-width: 480px; }}
  .head {{ display: flex; gap: 14px; align-items: center; margin-bottom: 18px; }}
  .photo {{ width: 64px; height: 64px; border-radius: 50%; object-fit: cover; border: 2px solid #12876F; }}
  h1 {{ color: #AE2B22; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 4px; }}
  .name {{ font-size: 22px; font-weight: 700; }}
  .sub {{ color: #555; font-size: 14px; }}
  .flag {{ background: #AE2B22; color: #fff; padding: 10px 14px; border-radius: 6px; font-weight: 600; margin-bottom: 18px; }}
  section {{ margin-bottom: 18px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: #777; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  ul {{ padding-left: 20px; margin: 6px 0; }}
  .muted {{ color: #888; font-size: 14px; }}
  .allergy {{ background: #F5DBD7; color: #AE2B22; padding: 8px 12px; border-radius: 6px; font-weight: 600; }}
  .instructions {{ background: #DFF1EA; color: #0C6455; padding: 8px 12px; border-radius: 6px; font-weight: 600; }}
  .offline-note {{ background: #444; color: #fff; font-size: 12px; padding: 6px 10px; border-radius: 6px; margin-bottom: 14px; }}
  footer {{ font-size: 11px; color: #999; margin-top: 24px; border-top: 1px solid #eee; padding-top: 10px; }}
</style></head>
<body>
  <div class="head">{photo}<div>
    <h1>Emergency medical information</h1>
    <div class="name">{_e(p['name'])}</div>
    <div class="sub">{sub}</div>
  </div></div>
  <div class="offline-note" id="offlineNote" style="display:none;">Showing OFFLINE / CACHED information — this may be out of date.</div>
  {flag}
  {''.join(sections)}
  <footer>Last updated {updated} UTC<br>{_e(data['disclaimer'])}</footer>
  <script>
    // Offline emergency fallback: a tiny service worker scoped ONLY to
    // /emergency/ keeps this page opening with no signal once seen.
    function updateOfflineBadge() {{
      document.getElementById('offlineNote').style.display = navigator.onLine ? 'none' : 'block';
    }}
    window.addEventListener('online', updateOfflineBadge);
    window.addEventListener('offline', updateOfflineBadge);
    updateOfflineBadge();
    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.register('/static/sw-emergency.js', {{ scope: '/emergency/' }}).catch(() => {{}});
    }}
  </script>
</body></html>"""
