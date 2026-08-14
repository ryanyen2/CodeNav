// The experimenter's dashboard.
//
// It reads. It does not drive the session: the participant is sharing their
// screen, so pacing them from here would be a second thing to get wrong. What it
// is for is seeing that data is arriving, and later holding the forms.
//
// Everything on screen is a live subscription rather than a poll, so a session
// that stops sending is visible within seconds rather than at the end of the call.
import { initializeApp } from 'firebase/app';
import {
    getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged,
    connectAuthEmulator,
} from 'firebase/auth';
import {
    getFirestore, collection, doc, setDoc, onSnapshot, query, orderBy,
    connectFirestoreEmulator,
} from 'firebase/firestore';
import { timeline, legend, ribbon, patterns } from './charts.js';
import { newParticipantCode } from '../shared/schema.js';
import { toLetters } from '../shared/actions.js';
import { comparableEpisodes, letters } from '../analysis/sequences.js';
import { score } from '../analysis/ngrams.js';
import {
    OPEN_DECISIONS, SETTLED_BY, GROUNDS, rounds, emptyAssessment, outstanding,
} from './forms.js';

const firebaseConfig = {
    apiKey: 'AIzaSyCeIFBc8HhCmtw9-pXjUm1qT3CUyo5GbkY',
    authDomain: 'codoc-11b10.firebaseapp.com',
    projectId: 'codoc-11b10',
    storageBucket: 'codoc-11b10.firebasestorage.app',
    messagingSenderId: '23316400560',
    appId: '1:23316400560:web:1ba4963bb0de7ef1622e75',
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

// Local development runs against the emulator, so a mistake there cannot touch
// real study data.
if (new URLSearchParams(location.search).has('emulator')) {
    connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
    connectFirestoreEmulator(db, '127.0.0.1', 8080);
}

const $ = (sel) => document.querySelector(sel);
const CONDITIONS = ['codoc', 'baseline'];

const state = {
    participants: [],
    selected: null,
    condition: 'codoc',
    actions: [],
    unsubBatches: null,
    unsubAssessment: null,
    assessment: null,
    project: 'hearth',
};

// ── signing in ───────────────────────────────────────────────────────────────

$('#sign-in')?.addEventListener('click', async () => {
    try {
        await signInWithPopup(auth, new GoogleAuthProvider());
    } catch (err) {
        $('#detail').innerHTML = `<div class="notice">Could not sign in: ${err.code || err.message}</div>`;
    }
});

onAuthStateChanged(auth, (user) => {
    if (!user) {
        $('#who').textContent = '';
        $('#roster-list').innerHTML = '';
        return;
    }
    $('#who').innerHTML = `${user.email} · <a href="#" id="sign-out">sign out</a>`;
    $('#sign-out').onclick = (e) => { e.preventDefault(); void signOut(auth); };
    watchParticipants();
});

// ── the roster ───────────────────────────────────────────────────────────────

function watchParticipants() {
    onSnapshot(
        query(collection(db, 'participants'), orderBy('createdAt', 'desc')),
        (snap) => {
            state.participants = snap.docs.map((d) => ({ code: d.id, ...d.data() }));
            renderRoster();
            if (!state.selected && state.participants.length) select(state.participants[0].code);
            if (!state.participants.length) renderEmpty();
        },
        (err) => {
            // The most likely cause by far is an address that is not on the
            // allowlist, so say that rather than showing an empty page.
            $('#detail').innerHTML = `<div class="notice">
                Cannot read the study data (${err.code}). If this address is not on
                the allowlist in <code>firestore.rules</code>, that is why.
            </div>`;
        },
    );
}

function renderRoster() {
    const list = $('#roster-list');
    list.innerHTML = '';
    for (const p of state.participants) {
        const b = document.createElement('button');
        b.className = 'p-item';
        b.setAttribute('aria-current', String(p.code === state.selected));
        const live = p.lastSeen && Date.now() - p.lastSeen < 90_000;
        b.innerHTML = `<div class="code">${live ? '<i class="live-dot"></i>' : ''}${p.code}</div>
            <div class="meta">${p.order || 'order not set'}</div>`;
        b.onclick = () => select(p.code);
        list.append(b);
    }
}

$('#new-participant').addEventListener('click', async () => {
    const code = newParticipantCode();
    // The order that is least represented so far, so the four combinations fill
    // evenly without anyone having to keep a tally.
    const counts = { 'codoc-first': 0, 'baseline-first': 0 };
    for (const p of state.participants) if (p.order in counts) counts[p.order] += 1;
    const order = counts['codoc-first'] <= counts['baseline-first'] ? 'codoc-first' : 'baseline-first';
    try {
        await setDoc(doc(db, 'participants', code), { createdAt: Date.now(), order, released: false });
        select(code);
    } catch (err) {
        alert(`Could not create a participant: ${err.code || err.message}`);
    }
});

// ── one participant ──────────────────────────────────────────────────────────

function select(code) {
    state.selected = code;
    state.actions = [];
    renderRoster();
    renderDetail();
    watchBatches();
}

function watchBatches() {
    if (state.unsubBatches) state.unsubBatches();
    const path = `participants/${state.selected}/sessions/${state.condition}/batches`;
    state.unsubBatches = onSnapshot(
        query(collection(db, path), orderBy('seq')),
        (snap) => {
            state.actions = snap.docs.flatMap((d) => d.data().actions || []);
            state.actions.sort((a, b) => a.t - b.t);
            renderSession();
        },
        () => { state.actions = []; renderSession(); },
    );
}

function renderEmpty() {
    $('#detail').innerHTML = `<div class="empty">
        <strong>No participants yet</strong>
        Create one to get a code. Give that code to the participant during setup,
        and everything they do arrives here against it.
    </div>`;
}

function renderDetail() {
    const p = state.participants.find((x) => x.code === state.selected);
    if (!p) return renderEmpty();
    $('#detail').innerHTML = `
      <div class="detail-head">
        <h2>${p.code}</h2>
        <span class="sub">${p.order || 'order not set'}</span>
      </div>
      <div class="tabs" id="tabs"></div>
      <div class="stats" id="stats"></div>
      <div class="card">
        <h3>The session</h3>
        <p class="hint">Each bar is something that was on screen or happened. Gaps are drawn, not closed up.</p>
        <div id="chart"></div>
        <div class="legend" id="legend"></div>
      </div>
      <div class="card">
        <h3>As a sequence</h3>
        <p class="hint">The same session in the vocabulary the patterns are counted in.</p>
        <div class="ribbon" id="ribbon"></div>
      </div>
      <div class="card" id="forms"></div>
      <div class="card">
        <h3>What recurs</h3>
        <p class="hint">Ranked by how much more often each happens than its parts alone would predict, so the longest bar is not simply the commonest action twice.</p>
        <div id="patterns"></div>
        <p class="pat-note" id="patterns-note"></p>
      </div>`;

    const tabs = $('#tabs');
    for (const c of CONDITIONS) {
        const b = document.createElement('button');
        b.textContent = c === 'codoc' ? 'With codoc' : 'Without codoc';
        b.setAttribute('aria-selected', String(c === state.condition));
        b.onclick = () => { state.condition = c; renderDetail(); watchBatches(); };
        tabs.append(b);
    }
    legend($('#legend'));
    renderForms();
    renderSession();
    watchAssessment();
}

// ── the forms ────────────────────────────────────────────────────────────────

function watchAssessment() {
    if (state.unsubAssessment) state.unsubAssessment();
    const path = `participants/${state.selected}/assessments/${state.condition}`;
    state.unsubAssessment = onSnapshot(doc(db, path), (snap) => {
        const incoming = snap.exists() ? snap.data() : null;
        // Do not stamp over something being typed right now. Two people on one
        // participant is rare; losing a sign-off to a snapshot is not recoverable.
        if (!state.assessment || !document.activeElement
            || !document.activeElement.closest('#forms')) {
            state.assessment = incoming || emptyAssessment(state.project);
            renderForms();
        }
    }, () => { state.assessment = state.assessment || emptyAssessment(state.project); });
}

let assessTimer;
function saveAssessment() {
    state.assessment.updatedAt = Date.now();
    renderGaps();
    clearTimeout(assessTimer);
    assessTimer = setTimeout(() => {
        void setDoc(
            doc(db, `participants/${state.selected}/assessments/${state.condition}`),
            state.assessment, { merge: true },
        ).catch((err) => console.warn('assessment not saved', err.code));
    }, 500);
}

function renderForms() {
    const el = $('#forms');
    if (!el) return;
    const p = state.participants.find((x) => x.code === state.selected);
    state.project = projectFor(p, state.condition);
    state.assessment = state.assessment || emptyAssessment(state.project);
    const a = state.assessment;

    el.innerHTML = `
      <h3>During and after the task</h3>
      <p class="hint">Typed once, here. Everything saves as you go.</p>

      <div class="form-block">
        <h4>The sign-off</h4>
        <p class="quote">Is this change correct and complete? How confident are you,
        1 to 5? And what is that resting on?</p>
        <div class="row">
          <span class="row-label">Confidence</span>
          <div class="choices" id="signoff-n">${[1, 2, 3, 4, 5].map((n) => `
            <button data-n="${n}" aria-pressed="${String(a.signoffConfidence === n)}">${n}</button>`).join('')}</div>
        </div>
        <div class="row">
          <span class="row-label">Resting on</span>
          <div class="choices" id="signoff-g">${GROUNDS.map((g) => `
            <button data-g="${esc(g)}" aria-pressed="${String((a.signoffGrounds || []).includes(g))}">${esc(g)}</button>`).join('')}</div>
        </div>
        <textarea id="signoff-text" rows="3"
          placeholder="Their answer, word for word">${esc(a.signoffVerbatim || '')}</textarea>
      </div>

      <div class="form-block">
        <h4>Who settled what</h4>
        <p class="hint">The four things the card leaves open.</p>
        ${(OPEN_DECISIONS[state.project] || []).map((d) => `
          <div class="row">
            <span class="row-label wide">${esc(d)}</span>
            <div class="choices" data-decision="${esc(d)}">${SETTLED_BY.map((s, i) => `
              <button data-s="${esc(s)}" title="${esc(s)}"
                aria-pressed="${String((a.decisions || {})[d] === s)}">${i + 1}</button>`).join('')}</div>
          </div>`).join('')}
        <p class="hint">1 they decided first · 2 the agent proposed and they accepted ·
        3 the agent did it and they never noticed</p>
      </div>

      <div class="form-block">
        <h4>The questions</h4>
        <p class="hint">Closed book first, then again with the description open.
        The change between the two is the result.</p>
        <div class="rounds" id="rounds"></div>
      </div>

      <p class="gaps" id="gaps"></p>`;

    renderRounds();
    wireForms();
    renderGaps();
}

function projectFor(p, condition) {
    // hearth goes with whichever condition comes first.
    if (!p || !p.order) return condition === 'codoc' ? 'hearth' : 'ember';
    const codocFirst = p.order === 'codoc-first';
    if (condition === 'codoc') return codocFirst ? 'hearth' : 'ember';
    return codocFirst ? 'ember' : 'hearth';
}

function renderRounds() {
    const wrap = $('#rounds');
    const a = state.assessment;
    const byRound = rounds(state.project);
    wrap.innerHTML = [1, 2].map((r) => `
      <div class="round">
        <h5>${r === 1 ? 'Before the task' : 'After the task'}</h5>
        ${byRound[r].map((q) => {
        const base = `${q.code}-r${r}`;
        return `<details class="q-item">
            <summary>
              <span class="q-title">${q.number}. ${esc(q.title)}</span>
              <span class="q-scores">
                <b class="${a.scores[`${base}-closed`] != null ? 'set' : ''}">${a.scores[`${base}-closed`] ?? '·'}</b>
                <b class="${a.scores[`${base}-open`] != null ? 'set' : ''}">${a.scores[`${base}-open`] ?? '·'}</b>
              </span>
            </summary>
            <p class="quote">${esc(q.question)}</p>
            <table class="key">${['2', '1', '0'].map((s) => `
              <tr><th>${s}</th><td>${esc(q.scores[s])}</td></tr>`).join('')}</table>
            <div class="row">
              <span class="row-label">Closed book</span>
              <div class="choices" data-score="${base}-closed">${[0, 1, 2].map((s) => `
                <button data-v="${s}" aria-pressed="${String(a.scores[`${base}-closed`] === s)}">${s}</button>`).join('')}</div>
              <span class="row-label">Confidence</span>
              <div class="choices" data-score="${base}-confidence">${[1, 2, 3, 4, 5].map((s) => `
                <button data-v="${s}" aria-pressed="${String(a.scores[`${base}-confidence`] === s)}">${s}</button>`).join('')}</div>
            </div>
            <div class="row">
              <span class="row-label">Open book</span>
              <div class="choices" data-score="${base}-open">${[0, 1, 2].map((s) => `
                <button data-v="${s}" aria-pressed="${String(a.scores[`${base}-open`] === s)}">${s}</button>`).join('')}</div>
            </div>
            <textarea rows="2" data-notes="${base}-notes"
              placeholder="What they said">${esc(a.scores[`${base}-notes`] || '')}</textarea>
          </details>`;
    }).join('')}
      </div>`).join('');
}

function renderGaps() {
    const el = $('#gaps');
    if (!el) return;
    const gaps = outstanding(state.assessment, state.project);
    el.textContent = gaps.length
        ? `Still missing: ${gaps.join(', ')}.`
        : 'Nothing outstanding for this condition.';
    el.className = gaps.length ? 'gaps open' : 'gaps done';
}

function wireForms() {
    const a = state.assessment;

    for (const b of document.querySelectorAll('#signoff-n button')) {
        b.onclick = () => {
            a.signoffConfidence = Number(b.dataset.n);
            for (const s of document.querySelectorAll('#signoff-n button')) {
                s.setAttribute('aria-pressed', String(s === b));
            }
            saveAssessment();
        };
    }
    for (const b of document.querySelectorAll('#signoff-g button')) {
        b.onclick = () => {
            // More than one ground is normal: people run the tests and read the
            // diff, and forcing one answer would lose that.
            const g = b.dataset.g;
            const list = new Set(a.signoffGrounds || []);
            if (list.has(g)) list.delete(g); else list.add(g);
            a.signoffGrounds = [...list];
            b.setAttribute('aria-pressed', String(list.has(g)));
            saveAssessment();
        };
    }
    const text = $('#signoff-text');
    if (text) text.oninput = () => { a.signoffVerbatim = text.value; saveAssessment(); };

    for (const group of document.querySelectorAll('[data-decision]')) {
        for (const b of group.querySelectorAll('button')) {
            b.onclick = () => {
                a.decisions = a.decisions || {};
                a.decisions[group.dataset.decision] = b.dataset.s;
                for (const s of group.querySelectorAll('button')) {
                    s.setAttribute('aria-pressed', String(s === b));
                }
                saveAssessment();
            };
        }
    }

    for (const group of document.querySelectorAll('[data-score]')) {
        for (const b of group.querySelectorAll('button')) {
            b.onclick = () => {
                a.scores[group.dataset.score] = Number(b.dataset.v);
                for (const s of group.querySelectorAll('button')) {
                    s.setAttribute('aria-pressed', String(s === b));
                }
                const item = group.closest('.q-item');
                if (item) refreshSummary(item);
                saveAssessment();
            };
        }
    }
    for (const t of document.querySelectorAll('[data-notes]')) {
        t.oninput = () => { a.scores[t.dataset.notes] = t.value; saveAssessment(); };
    }
}

/** Keep the two numbers on the collapsed row in step with what was clicked. */
function refreshSummary(item) {
    const a = state.assessment;
    const groups = [...item.querySelectorAll('[data-score]')].map((g) => g.dataset.score);
    const closed = groups.find((g) => g.endsWith('-closed'));
    const open = groups.find((g) => g.endsWith('-open'));
    const cells = item.querySelectorAll('.q-scores b');
    if (cells[0]) {
        cells[0].textContent = a.scores[closed] ?? '·';
        cells[0].className = a.scores[closed] != null ? 'set' : '';
    }
    if (cells[1]) {
        cells[1].textContent = a.scores[open] ?? '·';
        cells[1].className = a.scores[open] != null ? 'set' : '';
    }
}

function renderSession() {
    const chart = $('#chart');
    if (!chart) return;

    const counts = {};
    for (const a of state.actions) counts[a.a] = (counts[a.a] || 0) + 1;
    const span = state.actions.length
        ? (state.actions[state.actions.length - 1].t - state.actions[0].t) / 60000 : 0;

    $('#stats').innerHTML = [
        ['actions', state.actions.length],
        ['minutes', Math.round(span)],
        ['prompts', counts.PROMPT || 0],
        ['test runs', counts.RUN_TEST || 0],
        ['agent edits', (counts.AGENT_EDIT || 0) + (counts.AGENT_DOC || 0)],
    ].map(([k, n]) => `<div class="stat"><div class="n">${n}</div><div class="k">${k}</div></div>`).join('');

    if (!state.actions.length) {
        chart.innerHTML = `<div class="empty" style="padding:34px">
            Nothing has arrived for this condition yet. It appears here as it happens.
        </div>`;
        $('#ribbon').innerHTML = '';
        return;
    }
    if (chart.querySelector('.empty')) chart.innerHTML = '';
    timeline(chart, state.actions);
    ribbon($('#ribbon'), state.actions);
    renderPatterns();
}

function renderPatterns() {
    const el = $('#patterns');
    const note = $('#patterns-note');
    if (!el) return;

    // One session is never enough to call something a pattern, so say that
    // rather than plotting six bars from twenty actions.
    const { episodes, droppedActions } = comparableEpisodes(state.actions);
    const seqs = episodes.map(letters);
    const enough = seqs.reduce((n, s) => n + Math.max(s.length - 1, 0), 0) >= 20;
    if (!enough) {
        el.innerHTML = '';
        note.textContent = 'Too little of this session so far to say what recurs.';
        return;
    }

    const s = score(seqs, { n: 2, minCount: 2 });
    patterns(el, s.rows);
    note.textContent = `${s.total} pairs across ${seqs.length} stretches of work. `
        + `${s.trimmed} seen once were left out, which is `
        + `${(s.trimmedShare * 100).toFixed(1)}% of them. `
        + (droppedActions ? `${droppedActions} actions sat in stretches too short to show an order. ` : '')
        + 'One session is a description, not a finding.';
}

let resizeTimer;
addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.actions.length) renderSession(); }, 120);
});

export { state, toLetters };
