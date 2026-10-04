"""
SmartPoli — the public, no-login emergency page a QR scan opens.

Plain server-rendered HTML (not the SPA) so it opens instantly on any phone
with nothing cached. Shows only what the patient chose to share on their
health card (serializers.card_profile share choices); the name is always
shown. Every patient-entered value is HTML-escaped — this page is served
from the app's own origin, where the SPA keeps its login token.
"""

import re
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


def _when_words(code, food) -> str:
    """'1-0-1 PC' -> 'morning & night, after food'. A stranger (or a tired paramedic) should not have to decode
    OD / BD / 1-0-1. Falls back to the code as written if it cannot be read."""
    out = ""
    if code:
        try:
            from shorthand import decode_schedule
            d = decode_schedule(code)
            if d.get("slots"):
                out = " & ".join(slot.capitalize() if i == 0 else slot for i, slot in enumerate(d["slots"]))
            elif d.get("times"):
                out = "At " + ", ".join(d["times"])
            else:
                out = d.get("label") or code
        except Exception:  # noqa: BLE001 - never let a schedule we cannot read break an emergency page
            out = code
    if food in ("before", "after"):
        out = (out + ", " if out else "") + f"{food} food"
    return out


def _medicine_rows(items) -> str:
    if not items:
        return '<p class="muted">None on file.</p>'
    seen, rows = set(), []
    for i in items:
        dose = f'{i.get("dose_amount") or ""}{i.get("dose_unit") or ""}'
        when = "As needed" if i.get("prn") else _when_words(i.get("schedule_code"), i.get("food"))
        key = (str(i["name"]).strip().lower(), dose, when)
        if key in seen:
            continue                        # the same medicine entered twice shows once
        seen.add(key)
        rows.append(f'<li><div class="m-name">{_e(i["name"])} <span class="m-dose">{_e(dose)}</span></div>'
                    f'{f"<div class=m-when>{_e(when)}</div>" if when else ""}</li>')
    return f'<ul class="meds">{"".join(rows)}</ul>'


def _names(names) -> str:
    return f"<p>{_e(', '.join(names))}</p>" if names else '<p class="muted">None linked.</p>'


_PHONE = re.compile(r"(\+?\d[\d\s\-().]{7,}\d)")


def _split_contact(text):
    """('Suresh Kumar (son)', '+919876543210') from 'Suresh Kumar (son), +91 98765 43210'; phone is None if there is none."""
    if not text:
        return None, None
    m = _PHONE.search(str(text))
    if not m:
        return str(text).strip(), None
    digits = re.sub(r"[^\d+]", "", m.group(1))
    if digits.count("+") > 1 or ("+" in digits and not digits.startswith("+")):
        digits = digits.replace("+", "")
    label = (str(text)[:m.start()] + str(text)[m.end():]).strip(" ,;:-")
    return label or None, digits if 8 <= len(re.sub(r"\D", "", digits)) <= 15 else None


def render_public_card(data: dict) -> str:
    p = data["patient"]
    profile = data["profile"]
    share = profile["share"]
    card_path = data["card_path"]

    photo = (f'<img class="photo" src="{card_path}/photo" alt="">'
             if share["photo"] and profile["has_photo"] else "")
    sub = " &bull; ".join(x for x in (
        f'{_e(p["age"])} years' if p.get("age") else "", _e(p["sex"]),
    ) if x)
    flag = ('<div class="flag">&#9888; This patient has a history of an EMERGENCY-graded symptom check in SmartPoli.</div>'
            if data["has_emergency_triage_history"] else "")

    # ---- the two things a responder needs first: blood group and allergies, big
    tiles = []
    if share["blood_group"]:
        bg = p.get("blood_group")
        tiles.append(f'<div class="tile"><div class="tile-label">Blood group <span lang="hi">/ ब्लड ग्रुप</span></div>'
                     f'<div class="tile-big">{_e(bg) if bg else "&mdash;"}</div>'
                     f'{"" if bg else "<div class=muted>Not recorded</div>"}</div>')
    if share["allergies"]:
        if p.get("allergies"):
            chips = "".join(f'<span class="chip">{_e(a.strip())}</span>'
                            for a in re.split(r"[,;\n]+", str(p["allergies"])) if a.strip())
            tiles.append(f'<div class="tile tile-danger"><div class="tile-label">Allergies <span lang="hi">/ एलर्जी</span></div>'
                         f'<div class="chips">{chips}</div></div>')
        else:
            tiles.append('<div class="tile"><div class="tile-label">Allergies <span lang="hi">/ एलर्जी</span></div>'
                         '<div class="muted">None recorded</div></div>')
    tiles_html = f'<div class="tiles">{"".join(tiles)}</div>' if tiles else ""

    # ---- actions: tap to call. Nothing is ever called automatically.
    actions = []
    if share["emergency_contact"] and p.get("emergency_contact"):
        label, phone = _split_contact(p["emergency_contact"])
        if phone:
            actions.append(f'<a class="btn call" href="tel:{phone}"><span class="btn-main">Call {_e(label) if label else "emergency contact"}</span>'
                           f'<span class="btn-sub" lang="hi">परिवार को कॉल करें</span></a>')
    actions.append('<a class="btn sos" href="tel:112"><span class="btn-main">Call emergency services (112)</span>'
                   '<span class="btn-sub" lang="hi">आपातकालीन सेवा को कॉल करें</span></a>')

    cards = []
    if share["emergency_contact"]:
        cards.append(_section("Emergency contact", _text_or_none(p["emergency_contact"])))
    if share["instructions"] and profile["instructions"]:
        cards.append(_section("Important instructions", _text_or_none(profile["instructions"], "instructions")))
    if share["conditions"]:
        cards.append(_section("Medical conditions", _text_or_none(profile["conditions"])))
    if share["medicines"]:
        meds = data["scheduled_medicines"] + [dict(m, prn=True) for m in data["as_needed_medicines"]]
        cards.append(_section("Takes these medicines", _medicine_rows(meds)))
    if share["caregiver"]:
        cards.append(_section("Caregiver", _names(data["care_team"]["caregivers"])))
    if share["doctor"]:
        cards.append(_section("Doctor / care team", _names(data["care_team"]["doctors"])))

    updated = datetime.fromisoformat(data["last_updated"]).strftime("%d %b %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#AE2B22">
<title>Emergency card — {_e(p['name'])}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; background: #F4F4F2; color: #1a1a1a; margin: 0; }}
  .wrap {{ max-width: 520px; margin: 0 auto; padding: 0 0 28px; }}
  .cards {{ display: block; }}
  .band {{ background: #AE2B22; color: #fff; font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 10px 16px; }}
  .id {{ background: #fff; padding: 16px; display: flex; gap: 14px; align-items: center; border-bottom: 1px solid #E5E5E0; }}
  .photo {{ width: 72px; height: 72px; border-radius: 50%; object-fit: cover; border: 3px solid #12876F; flex: none; }}
  .name {{ font-size: 28px; font-weight: 800; line-height: 1.15; word-break: break-word; }}
  .sub {{ color: #555; font-size: 16px; margin-top: 2px; }}
  .flag {{ background: #AE2B22; color: #fff; padding: 12px 16px; font-weight: 600; margin: 12px 16px 0; border-radius: 10px; }}
  .tiles {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 14px 16px 0; }}
  .tiles:has(.tile:only-child) {{ grid-template-columns: 1fr; }}
  .tile {{ background: #fff; border: 1px solid #E0E0DA; border-radius: 14px; padding: 12px 14px; min-width: 0; }}
  .tile-danger {{ background: #FBE4E0; border-color: #E8A79E; }}
  .tile-label {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #666; }}
  .tile-danger .tile-label {{ color: #8E2018; }}
  .tile-big {{ font-size: 44px; font-weight: 800; line-height: 1.1; color: #AE2B22; margin-top: 4px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
  .chip {{ background: #AE2B22; color: #fff; font-weight: 700; font-size: 16px; padding: 6px 12px; border-radius: 999px; }}
  .actions {{ display: grid; gap: 10px; padding: 14px 16px 0; }}
  .btn {{ display: flex; flex-direction: column; align-items: center; justify-content: center; text-decoration: none; min-height: 58px;
          border-radius: 14px; padding: 10px 14px; text-align: center; }}
  .btn-main {{ font-size: 17px; font-weight: 800; }}
  .btn-sub {{ font-size: 13px; opacity: 0.9; }}
  .call {{ background: #12876F; color: #fff; }}
  .sos {{ background: #fff; color: #AE2B22; border: 2px solid #AE2B22; }}
  section {{ background: #fff; border: 1px solid #E0E0DA; border-radius: 14px; margin: 12px 16px 0; padding: 12px 14px; }}
  h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: #666; margin: 0 0 8px; }}
  section p {{ margin: 0; font-size: 16px; }}
  .muted {{ color: #888; font-size: 15px; }}
  .instructions {{ background: #DFF1EA; color: #0C6455; padding: 10px 12px; border-radius: 10px; font-weight: 700; font-size: 16px; }}
  .meds {{ list-style: none; padding: 0; margin: 0; }}
  .meds li {{ padding: 9px 0; border-top: 1px solid #EEE; }}
  .meds li:first-child {{ border-top: 0; padding-top: 0; }}
  .m-name {{ font-size: 17px; font-weight: 700; }}
  .m-dose {{ font-weight: 600; color: #444; }}
  .m-when {{ color: #555; font-size: 14px; margin-top: 1px; }}
  .offline-note {{ background: #444; color: #fff; font-size: 13px; padding: 8px 16px; }}
  footer {{ font-size: 12px; color: #777; margin: 18px 16px 0; line-height: 1.5; }}
  @media (min-width: 760px) {{
    body {{ padding: 24px; }}
    .wrap {{ max-width: 920px; background: #F4F4F2; border-radius: 18px; overflow: hidden; box-shadow: 0 2px 18px rgba(0,0,0,.08); }}
    .actions {{ grid-template-columns: 1fr 1fr; }}
    .cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 0; padding-right: 16px; }}
    .cards section {{ margin-right: 0; }}
    .tile-big {{ font-size: 56px; }}
  }}
</style></head>
<body><div class="wrap">
  <div class="band">Emergency health ID &nbsp;&middot;&nbsp; <span lang="hi">आपातकालीन स्वास्थ्य कार्ड</span></div>
  <div class="id">{photo}<div>
    <div class="name">{_e(p['name'])}</div>
    <div class="sub">{sub}</div>
  </div></div>
  <div class="offline-note" id="offlineNote" style="display:none;">Showing OFFLINE / CACHED information — this may be out of date.</div>
  {flag}
  {tiles_html}
  <div class="actions">{''.join(actions)}</div>
  <div class="cards">{''.join(cards)}</div>
  <footer>Last updated {updated} UTC<br>Entered by the patient and shown without a login. It has not been verified by a doctor.<br>{_e(data['disclaimer'])}</footer>
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
</div></body></html>"""
