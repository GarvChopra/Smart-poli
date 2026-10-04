// SmartPoli Voice — command understanding. No AI service, no API key, no
// network: the patient's words are matched against fixed phrase tables
// (English, Hinglish, Devanagari) in the browser, so a command is understood
// the instant they stop talking.
//
//   understand(text)                → an intent, e.g. { type: 'navigate', screen: 'dashboard' }
//   answer(intent, dash, now, lang) → { reply, actions } from the dashboard data the page already has
//
// One command, one short answer — it never starts a question-and-answer
// conversation. Symptoms open the symptom check screen; anything that sounds
// like immediate danger shows the call-112 card straight away.
//
// Pure functions, so they're tested in Node (backend/tests/voice_commands.test.js).

(function (root) {
  const SCREENS = ['dashboard', 'prescriptions', 'safety', 'triage', 'report', 'timeline', 'emergency', 'settings'];

  // Happening-now danger, from voice_safety.py's tables plus the worrying
  // symptoms that must never wait behind a question.
  const DANGER = [
    /\bunconscious\b|\bpassed out\b|\bnot (waking up|responding)\b|\bbehosh\b|\bhosh nahi\b|बेहोश/,
    /\bseizures?\b|\bconvuls|\bdaura\b|\bdaure\b|\bmirgi\b|दौरा|मिर्गी/,
    /\bkill myself\b|\bend my life\b|\bsuicid|\bhurt myself\b|\bkhudkushi\b|आत्महत्या|खुदकुशी/,
    /too many (pills|tablets|medicines)|\boverdose\b|ज़्यादा गोलियाँ/,
    /face.{0,15}droop|\bslurr|\bmunh\b.{0,15}\b(tedha|terha)\b|मुँह टेढ़ा/,
    /\bgasping\b|\bchoking\b|can'?t breathe|cannot breathe|unable to breathe|saa?ns\b.{0,20}\b(nahi|nahin|nhi) (aa|le)|साँस नहीं|सांस नहीं/,
    /chest pain|pain in (my )?chest|seene (mein|me) dard|chhati (mein|me) dard|सीने में दर्द|छाती में दर्द/,
    /\b(heavy|lot of|a lot of) bleeding\b|bleeding (a lot|heavily|won'?t stop)|bahut khoon|खून बहुत/,
  ];
  const HELP_NUMBER = /\b(call )?(112|108|ambulance)\b|^emergency$|emergency (call|help)|madad chahiye|bachao|बचाओ/;

  const STOP = /^(stop|pause|bye|goodbye|band karo|bas|ruko|chup|chup raho|thank you|thanks|shukriya|dhanyavaad)$|बंद करो|रुको/;
  const HELP = /\bwhat can you do\b|\bhelp me\b|^help$|kya kar sakte|क्या कर सकते/;
  const HINDI = /\b(hindi|हिंदी|हिन्दी)\b/;
  const ENGLISH = /\b(english|angrezi|अंग्रेज़ी)\b/;
  const SWITCH = /\b(speak|talk|bolo|boliye|baat karo|language|bhasha|mein)\b|बोलो|में/;

  const MED = /\b(medicines?|medications?|meds|dawai|dawaai|dawaiyan|dawa|dava|goli|goliyan|tablets?|pills?|doses?)\b|दवा|दवाई|गोली/;
  const TOOK = /\b(took|taken|have had|had|le li|le liya|kha li|kha liya|li hai|le chuka|le chuki|kha chuka|kha chuki)\b|ले ली|खा ली|ले लिया|खा लिया/;
  const MARK = /\b(mark|tick)\b.{0,20}\b(taken|done|as taken)\b/;
  const QUESTION = /\b(did i|have i|kya maine|kya mene)\b|\?|क्या मैंने/;
  const NEGATION = /\b(not|didn'?t|haven'?t|nahi|nahin|nhi|na)\b|नहीं/;
  const NEXT = /\bnext\b|\bagli\b|\bkab (hai|leni)\b|\bwhen\b.{0,25}\b(medicine|dose|tablet|pill)|अगली|अगला|अगले|कब/;
  const LEFT = /\b(left|remaining|baaki|baki|bachi|bacha|kitni|how many)\b|बाकी|बची|कितनी/;
  // \"which medicines today?\" / \"aaj konsi konsi dawai leni hai\" - the whole day, taken and still to take.
  const SCHEDULE = /\b(konsi|which|list|schedule|saari|sari|sabhi|kya kya|kaun kaun)\b|\btoday'?s (medicines|doses|schedule)\b|\baaj ki dawai\b|कौन|कौनसी|कौन सी|सारी|सभी|आज की/;
  const ADHERENCE = /\b(adherence|missed|miss|chhooti|chhoot|chhut|progress|score)\b|छूटी/;
  const SYMPTOM = /\b(pain|ache|aching|hurts?|headache|dizzy|dizziness|fever|vomit|vomiting|nausea|cough|cold|rash|itch|breathless|weak|tired|unwell|sick|not feeling well|dard|chakkar|bukhar|ulti|ji machla|khansi|jukam|khujli|kamzori|thakan|tabiyat|theek nahi)\b|symptom|lakshan|दर्द|चक्कर|बुखार|उल्टी|खांसी/;
  const UPDATE = /\b(update|edit|change|badlo|badalna|sudhar|theek karo)\b|बदलो/;
  const DETAILS = /\b(details?|profile|info|information|blood group|allerg|contact|address|photo|jaankari|jankari)\b|जानकारी/;
  const SLOTS = [
    ['morning', /\b(morning|breakfast|subah|savere)\b|सुबह/, 4, 11],
    ['afternoon', /\b(afternoon|lunch|dopahar|dopher)\b|दोपहर/, 11, 16],
    ['evening', /\b(evening|shaam|sham)\b|शाम/, 16, 20],
    ['night', /\b(night|bedtime|dinner|raat)\b|रात/, 20, 28],
  ];
  // [screen, words] — checked in this order.
  const SCREEN_WORDS = [
    ['settings', /\b(settings?|caregivers?|care ?team|family|parivar|link (a )?(doctor|caregiver)|share (my )?(record|data))\b|सेटिंग/],
    ['emergency', /\b(emergency|health) card\b|\bcard\b|\bqr\b|कार्ड/],
    ['prescriptions', /\b(prescriptions?|parcha|purchi|parchi|scan|upload|add (a )?(new )?(medicine|prescription))\b|पर्चा|पर्ची/],
    ['safety', /\b(safety|interactions?|side effects?|avoid|food warnings?|kya nahi khana)\b|सुरक्षा/],
    ['triage', /\bsymptom check\b|\blakshan jaanch\b|\bcheck (my )?symptoms?\b|लक्षण/],
    ['report', /\b(care )?report\b|\briport\b|\bpdf\b|रिपोर्ट/],
    ['timeline', /\b(timeline|history|itihaas|itihas)\b|इतिहास/],
    ['dashboard', /\b(dashboard|home|main screen|ghar)\b|डैशबोर्ड|होम/],
  ];
  const OPEN = /\b(open|show|go to|goto|take me|display|manage|kholo|kholiye|khol do|dikhao|dikhaiye|chalo|le chalo)\b|खोलो|दिखाओ|दिखाइए|चलो/;

  // Speech-to-text spells Hindi words many ways; fold the common variants into the one spelling the tables use.
  const SPELLINGS = [
    [/(davai|davaai|dawayi|dawaee|davaee|dawaie|daawai|dawaiyan|davaiyan|dawayian)/g, 'dawai'],
    [/(agle|agla|aglee|agali|agley)/g, 'agli'],
    [/(batiye|bataiye|batayiye|bataye|bataiyen|btao|btaiye|batiyega|bata do|bta do|batado|btado)/g, 'batao'],
    [/(konsi|konsee|kaunsi|kaun si|kon si|konsa|kaunsa|kaun sa|kon sa)/g, 'konsi'],
    [/(lagi|lage|lagee|leni|lena|khani|khana hai)/g, 'leni'],
  ];

  function clean(text) {
    let t = String(text || '').toLowerCase().replace(/[.,!।?]/g, ' ').replace(/\s+/g, ' ').trim();
    for (const [re, to] of SPELLINGS) t = t.replace(re, to);
    return t;
  }

  function slotOf(t) {
    const s = SLOTS.find(([, re]) => re.test(t));
    return s ? s[0] : null;
  }

  function screenOf(t) {
    const s = SCREEN_WORDS.find(([, re]) => re.test(t));
    return s ? s[0] : null;
  }

  /** The patient's words → one intent. */
  function understand(text) {
    const t = clean(text);
    if (!t) return { type: 'unknown' };
    if (DANGER.some((re) => re.test(t)) || HELP_NUMBER.test(t)) return { type: 'emergency' };
    if (STOP.test(t)) return { type: 'stop' };
    if (SWITCH.test(t) && HINDI.test(t)) return { type: 'language', lang: 'hi' };
    if (SWITCH.test(t) && ENGLISH.test(t)) return { type: 'language', lang: 'en' };
    if (HELP.test(t)) return { type: 'help' };

    const opens = OPEN.test(t);
    if (UPDATE.test(t) && DETAILS.test(t)) return { type: 'navigate', screen: 'emergency', edit: true };

    const aboutMeds = MED.test(t) || slotOf(t);
    if (QUESTION.test(t) && (TOOK.test(t) || /\btake\b/.test(t))) return { type: 'did_i_take', slot: slotOf(t) };
    if ((TOOK.test(t) || MARK.test(t)) && aboutMeds && !NEGATION.test(t)) {
      return { type: 'took', slot: slotOf(t), words: t };
    }

    const screen = screenOf(t);
    if (opens && screen) return { type: 'navigate', screen };
    if (NEXT.test(t) && !SCHEDULE.test(t.replace(/\bnext\b/, ''))) return { type: 'next' };
    if (ADHERENCE.test(t)) return { type: 'adherence' };
    if (LEFT.test(t) && (MED.test(t) || /\btoday\b|\baaj\b|आज/.test(t))) return { type: 'today' };
    if ((SCHEDULE.test(t) && (MED.test(t) || /\btoday\b|\baaj\b|आज/.test(t))) || (MED.test(t) && /\b(aaj|today)\b|आज/.test(t))) return { type: 'schedule' };
    if (SYMPTOM.test(t)) return { type: 'navigate', screen: 'triage', symptom: true };
    if (screen) return { type: 'navigate', screen };
    if (MED.test(t)) return { type: 'schedule' };
    return { type: 'unknown' };
  }

  // ---------------------------------------------------------------- answers

  const SAY = {
    en: {
      opening: (s) => `Opening ${s}.`,
      editing: 'Opening your details to update.',
      symptom: 'Opening the symptom check.',
      next: (m, when) => `Your next medicine is ${m}, ${when}.`,
      noNext: 'No more medicines are scheduled.',
      today: (n, list) => n ? `${n} left today: ${list}.` : 'Nothing left for today — all done.',
      schedule: (list, left) => `Today: ${list}.${left ? '' : ' All taken.'}`,
      schedNone: 'No medicines are scheduled today.',
      taken: 'taken',
      adherence: (p, taken, missed) => p == null ? 'No doses recorded yet.'
        : `Your adherence is ${p} percent. ${taken} taken, ${missed} missed.`,
      marked: (list) => `Marked ${list} as taken.`,
      choose: 'Which one did you take? Tap it below.',
      noneDue: 'No medicine is due right now.',
      didTake: (list) => `Yes — you took ${list} today.`,
      didNot: (list) => `Not yet. Still to take: ${list}.`,
      nothingToday: 'You have no medicines scheduled today.',
      emergency: 'If this is serious, call 112 now. Tap the red button to call.',
      stop: 'Okay. Tap the circle when you need me.',
      language: 'Okay, speaking English.',
      help: 'Say things like: open dashboard, next medicine, I took my medicine, update my details, or show my report.',
      unknown: 'Sorry, I didn\'t get that. Try: open dashboard, or next medicine.',
      today_word: 'today', tomorrow_word: 'tomorrow', at: 'at',
      screens: { dashboard: 'your dashboard', prescriptions: 'prescriptions', safety: 'the safety center', triage: 'the symptom check',
        report: 'your care report', timeline: 'your timeline', emergency: 'your emergency card', settings: 'settings' },
    },
    hi: {
      opening: (s) => `${s} khol raha hoon.`,
      editing: 'Aapki details badalne ke liye khol raha hoon.',
      symptom: 'Symptom check khol raha hoon.',
      next: (m, when) => `Aapki agli dawai ${m} hai, ${when}.`,
      noNext: 'Aage koi dawai scheduled nahi hai.',
      today: (n, list) => n ? `Aaj ${n} baaki hain: ${list}.` : 'Aaj ki sab dawaiyan ho gayi hain.',
      schedule: (list, left) => `Aaj ki dawaiyan: ${list}.${left ? '' : ' Sab le li hain.'}`,
      schedNone: 'Aaj koi dawai scheduled nahi hai.',
      taken: 'li',
      adherence: (p, taken, missed) => p == null ? 'Abhi koi dose record nahi hui.'
        : `Aapki adherence ${p} percent hai. ${taken} li, ${missed} chhooti.`,
      marked: (list) => `${list} le li — mark kar diya.`,
      choose: 'Kaun si li? Neeche dabaiye.',
      noneDue: 'Abhi koi dawai ka samay nahi hai.',
      didTake: (list) => `Haan — aaj aapne ${list} li hai.`,
      didNot: (list) => `Abhi nahi. Baaki hai: ${list}.`,
      nothingToday: 'Aaj koi dawai scheduled nahi hai.',
      emergency: 'Agar yeh gambhir hai to abhi 112 par call kijiye. Laal button dabaiye.',
      stop: 'Theek hai. Zarurat ho to gola dabaiye.',
      language: 'Theek hai, ab Hindi mein baat karunga.',
      help: 'Aise boliye: dashboard kholo, agli dawai kab hai, maine dawai le li, meri details badlo, ya report dikhao.',
      unknown: 'Maaf kijiye, samajh nahi aaya. Boliye: dashboard kholo, ya agli dawai kab hai.',
      today_word: 'aaj', tomorrow_word: 'kal', at: '',
      screens: { dashboard: 'Dashboard', prescriptions: 'Prescription', safety: 'Safety center', triage: 'Symptom check',
        report: 'Care report', timeline: 'Timeline', emergency: 'Emergency card', settings: 'Settings' },
    },
  };

  // Dose times are the patient's wall-clock time without a zone, so they are
  // read as local time here — the same clock the patient is living on.
  const at = (d) => new Date(d.scheduled_at);
  const hhmm = (date) => `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const names = (doses) => [...new Set(doses.map((d) => d.medicine_name))].join(', ');
  const HOUR = 3600000;

  function inSlot(date, slot) {
    const s = SLOTS.find(([name]) => name === slot);
    const h = date.getHours() < 4 ? date.getHours() + 24 : date.getHours();
    return !s || (h >= s[2] && h < s[3]);
  }

  /** Which doses "I took my medicine" means: a medicine they named, else the
   * slot they named (morning/night…), else everything scheduled at the time
   * closest to now. Several candidates spread over the day → let them tap. */
  function dosesToMark(intent, doses, now) {
    const open = doses.filter((d) => ['pending', 'snoozed', 'missed'].includes(d.state)
      && at(d) >= new Date(now - 12 * HOUR) && at(d) <= new Date(+now + 2 * HOUR));
    const named = open.filter((d) => (d.medicine_name || '').toLowerCase().split(/\s+/)
      .some((w) => w.length > 3 && (intent.words || '').includes(w)));
    if (named.length) return { mark: [named.sort((a, b) => Math.abs(at(a) - now) - Math.abs(at(b) - now))[0]] };
    const pool = intent.slot ? open.filter((d) => sameDay(at(d), now) && inSlot(at(d), intent.slot)) : open;
    if (!pool.length) return { mark: [] };
    const closest = pool.reduce((best, d) => (Math.abs(at(d) - now) < Math.abs(at(best) - now) ? d : best));
    if (intent.slot || Math.abs(at(closest) - now) <= 3 * HOUR) {
      const time = at(closest).getTime();
      return { mark: pool.filter((d) => intent.slot ? true : at(d).getTime() === time) };
    }
    return { choose: pool };
  }

  /** An intent + the dashboard the page already loaded → what to say and do. */
  function answer(intent, dash, now, lang) {
    const s = SAY[lang] || SAY.en;
    const doses = (dash && dash.recent_doses) || [];
    const reply = (text, actions = []) => ({ reply: text, actions, lang });
    const when = (d) => {
      const date = at(d);
      const day = sameDay(date, now) ? s.today_word
        : sameDay(date, new Date(+now + 24 * HOUR)) ? s.tomorrow_word
          : date.toLocaleDateString([], { weekday: 'long' });
      return [day, s.at, hhmm(date)].filter(Boolean).join(' ');
    };

    switch (intent.type) {
      case 'emergency':
        return reply(s.emergency, [{ type: 'emergency' }]);
      case 'stop':
        return reply(s.stop, [{ type: 'stop' }]);
      case 'language':
        return { ...reply((SAY[intent.lang] || SAY.en).language, [{ type: 'language', lang: intent.lang }]), lang: intent.lang };
      case 'help':
        return reply(s.help);
      case 'navigate': {
        const text = intent.edit ? s.editing : intent.symptom ? s.symptom : s.opening(s.screens[intent.screen]);
        return reply(text, [{ type: 'navigate', screen: intent.screen, edit: !!intent.edit }]);
      }
      case 'next': {
        const upcoming = doses.filter((d) => ['pending', 'snoozed'].includes(d.state) && at(d) >= now);
        const next = upcoming[0] || ((dash && dash.upcoming_doses) || []).find((d) => at(d) >= now);
        return reply(next ? s.next(next.medicine_name, when(next)) : s.noNext);
      }
      case 'today': {
        const left = doses.filter((d) => sameDay(at(d), now) && ['pending', 'snoozed'].includes(d.state) && at(d) >= now);
        return reply(s.today(left.length, left.map((d) => `${d.medicine_name} ${hhmm(at(d))}`).join(', ')));
      }
      case 'schedule': {
        const all = doses.filter((d) => sameDay(at(d), now) && d.state !== 'skipped')
          .sort((a, b) => at(a) - at(b));
        if (!all.length) return reply(s.schedNone);
        const list = all.map((d) => `${d.medicine_name} ${hhmm(at(d))}${d.state === 'taken' ? ` (${s.taken})` : ''}`).join(', ');
        return reply(s.schedule(list, all.some((d) => d.state !== 'taken')));
      }
      case 'adherence': {
        const a = (dash && dash.adherence) || {};
        const pct = a.adherence_percent == null ? null : Math.round(a.adherence_percent);
        return reply(s.adherence(pct, a.taken || 0, a.missed || 0));
      }
      case 'did_i_take': {
        const today = doses.filter((d) => sameDay(at(d), now) && (!intent.slot || inSlot(at(d), intent.slot)));
        if (!today.length) return reply(s.nothingToday);
        const due = today.filter((d) => d.state !== 'taken' && at(d) <= new Date(+now + 2 * HOUR));
        if (due.length) return reply(s.didNot(names(due)));
        const taken = today.filter((d) => d.state === 'taken');
        return reply(taken.length ? s.didTake(names(taken)) : s.noneDue);
      }
      case 'took': {
        const pick = dosesToMark(intent, doses, now);
        if (pick.choose) {
          return reply(s.choose, [{ type: 'choose_dose', options: pick.choose.map((d) => (
            { dose_id: d.id, medicine: d.medicine_name, time: hhmm(at(d)) })) }]);
        }
        if (!pick.mark.length) return reply(s.noneDue);
        return reply(s.marked(names(pick.mark)), [{ type: 'take', doses: pick.mark.map((d) => (
          { dose_id: d.id, medicine: d.medicine_name, time: hhmm(at(d)) })) }]);
      }
      default:
        return reply(s.unknown);
    }
  }

  const api = { understand, answer, SCREENS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, { VoiceCommands: api });
})(typeof window !== 'undefined' ? window : globalThis);
