// SmartPoli — the week strip + day timeline, shared by Your medicines (index.html) and a family member's page (caregiver.html).
// DayTimeline.mount(el, patientId, { onLoaded, actions }) draws a Sun–Sat strip and that day's doses from
// GET /patients/{id}/doses?date=. Tapping a day loads it. Everything the server sends goes through esc() first.

const DayTimeline = (() => {
  const text = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const FOOD = { before: 'before food', after: 'after food', with: 'with food' };
  const PILL = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="2.5" y="8.5" width="19" height="7" rx="3.5" transform="rotate(-45 12 12)"></rect><path d="M9.5 9.5l5 5"></path></svg>';
  const CHECK = '<svg class="tl-check" role="img" aria-label="Taken" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M5 12l5 5 9-10"></path></svg>';

  const parse = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
  const iso = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;

  /** The seven days (Sun–Sat) of the week that holds `today`; `selected` is lit. */
  function stripHtml(today, selected) {
    const t = parse(today);
    const sun = new Date(t.getFullYear(), t.getMonth(), t.getDate() - t.getDay());
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(sun.getFullYear(), sun.getMonth(), sun.getDate() + i), key = iso(d);
      return `<button class="day${key === selected ? ' on' : ''}" type="button" data-day="${key}"${key === selected ? ' aria-current="date"' : ''}><span>${DAYS[d.getDay()]}</span><b>${d.getDate()}</b></button>`;
    }).join('');
  }

  const timeOf = (isoTime) => {
    const [hh, mm] = isoTime.slice(11, 16).split(':').map(Number);
    return `${hh % 12 || 12}:${String(mm).padStart(2, '0')}<small>${hh < 12 ? 'AM' : 'PM'}</small>`;
  };

  /** One row of the day. Status is words/shape as well as colour: Missed in red, a tick for Taken, a hollow dot for still to come. */
  function rowHtml(d, i, n) {
    const missed = d.state === 'missed', taken = d.state === 'taken', skipped = d.state === 'skipped';
    const dot = missed ? 'background:#D3402A' : taken ? '' : 'background:#fff;border:2px solid #9DC9BA;box-sizing:border-box';
    const sub = missed ? '<span class="md tl-missed">Missed</span>' : skipped ? '<span class="md">Skipped</span>'
      : `<span class="md">${text(FOOD[d.food] || '')}</span>`;
    return `<span class="t">${timeOf(d.scheduled_at)}</span><span class="ln${i === 0 ? ' first' : ''}${i === n - 1 ? ' last' : ''}"><span class="dt" style="${dot}"></span></span>
      <div class="mc"><span class="ic${missed ? ' bad' : ''}">${PILL}</span>
        <span style="flex:1;min-width:0"><span class="mn">${text(d.medicine_name)}</span>${sub}</span>${taken ? CHECK : ''}</div>`;
  }

  /** The rows for one day, or the plain "nothing" line. */
  function listHtml(doses, day, today) {
    return doses.length ? `<div class="tl">${doses.map((d, i) => rowHtml(d, i, doses.length)).join('')}</div>`
      : `<div class="empty">${day === today ? 'Nothing to take today.' : 'Nothing was scheduled this day.'}</div>`;
  }

  /** Draw into `el`. `fetchDay(dateISO)` defaults to the shared endpoint; `onLoaded(data)` lets the page react (e.g. a missed card). */
  function mount(el, patientId, opts = {}) {
    const get = opts.fetchDay || ((day) => apiFetch('GET', `/patients/${patientId}/doses${day ? `?date=${day}` : ''}`));
    let today = null;
    el.classList.add('dtl');
    async function show(day) {
      el.innerHTML = '<div class="empty">Loading…</div>';
      let data;
      try { data = await get(day); } catch (e) { el.innerHTML = `<div class="empty">${text(e.message || 'Could not load this day.')}</div>`; return; }
      today = data.today;
      const doses = data.doses || [];
      el.innerHTML = `<div class="day-strip">${stripHtml(today, data.date)}</div>
        <div class="tl-shadow" aria-hidden="true"></div>
        ${listHtml(doses, data.date, today)}`;
      el.querySelectorAll('[data-day]').forEach((b) => b.addEventListener('click', () => show(b.dataset.day)));
      if (opts.onLoaded) opts.onLoaded(data);
    }
    return show(null);
  }

  return { mount, stripHtml, rowHtml, listHtml };
})();
