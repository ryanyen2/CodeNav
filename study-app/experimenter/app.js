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
    renderSession();
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
