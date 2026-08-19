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
    getFirestore, collection, doc, setDoc, getDocs, deleteDoc, onSnapshot, query, orderBy,
    connectFirestoreEmulator,
} from 'firebase/firestore';
import { timeline, legend, ribbon, patterns } from './charts.js';
import {
    newParticipantCode, LANGUAGES, DEFAULT_LANGUAGE,
} from '../shared/schema.js';
import { fill, progress, nextOrder, isPilot, PARTICIPANTS } from '../shared/cohort.js';
import { renderResults } from './results.js';
import { esc } from '../shared/html.js';
import { toLetters } from '../shared/actions.js';
import { comparableEpisodes, letters } from '../analysis/sequences.js';
import { score } from '../analysis/ngrams.js';
import {
    OPEN_DECISIONS, FALSE_ALARMS, SETTLED_BY, CONSISTENCY, COUPLED_DECISION,
    questionsFor, afterFor, emptyAssessment, outstanding,
} from './forms.js';
// The interview is asked from the dashboard now, so its questions come from the
// same instrument file the participant page uses. One copy, so the two cannot
// drift into asking different things.
import { INTERVIEW } from '../participant/instrument.js';

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
    // The language the NEXT participant is created in. Sticky for a run of
    // recruiting, since a cohort is usually recruited in one language at a time.
    newLang: DEFAULT_LANGUAGE,
    actions: [],
    unsubBatches: null,
    unsubAssessment: null,
    unsubQuiz: null,
    unsubDevices: null,
    unsubContact: null,
    contact: {},
    keys: {},
    answers: {},
    devices: [],
    assessment: null,
    project: 'scribe',
};

// ── the results view ─────────────────────────────────────────────────────────

/**
 * Everything the figures need, pulled once when the view is opened.
 *
 * Not a live subscription like the rest of the dashboard. Figures redrawing
 * under a researcher who is reading them is worse than figures that need a
 * click, and this is the one view nobody watches during a session.
 */
async function loadCohort() {
    const out = [];
    for (const p of state.participants) {
        const person = { ...p, answers: {}, sessions: {} };
        for (const c of CONDITIONS) {
            const batches = await getDocs(query(
                collection(db, `participants/${p.code}/sessions/${c}/batches`), orderBy('seq')));
            const actions = batches.docs.flatMap((d) => d.data().actions || [])
                .sort((a, b) => a.t - b.t);
            if (actions.length) person.sessions[c] = { actions };
        }
        const answers = await getDocs(collection(db, `participants/${p.code}/answers`));
        for (const d of answers.docs) person.answers[d.id] = d.data();
        out.push(person);
    }
    return out;
}

async function showResults() {
    state.view = 'results';
    renderRoster();
    const el = $('#detail');
    el.innerHTML = '<div class="empty">Reading every session…</div>';
    try {
        const cohort = await loadCohort();
        renderResults(el, cohort, { includePilots: false });
    } catch (err) {
        el.innerHTML = `<div class="notice">Could not read the sessions: ${
            esc(err.code || err.message)}</div>`;
    }
}

// Where the participant's own page lives. The order rides in the link because
// the participant cannot read their own record, by design.
const PARTICIPANT_PAGE = `${location.origin}/participant/`;
// Their link carries everything their page and their setup need, because the
// participant document is deliberately not readable by the participant. A link
// missing the language would run the page in English against workspaces built in
// another one, which is the confound this whole feature exists to avoid.
const linkFor = (p) => `${PARTICIPANT_PAGE}?code=${p.code}`
    + `&order=${p.order || 'codoc-first'}`
    + `&lang=${p.lang || DEFAULT_LANGUAGE}`;
const setupFor = (p) => `./setup.sh ${p.code} ${p.order || 'codoc-first'}`;

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
    watchKeys();
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

/**
 * The roster is the plan, not a list of whoever exists.
 *
 * Empty slots are drawn too. "How many more do I need, and in which order" is
 * the question mid-study, and a list of only what exists cannot answer it.
 */
function renderRoster() {
    const list = $('#roster-list');
    list.innerHTML = '';
    const { slots, extra } = fill(state.participants);
    const p = progress(state.participants);

    $('#show-results').setAttribute('aria-pressed', String(state.view === 'results'));

    const head = document.createElement('div');
    head.className = 'cohort';
    head.innerHTML = `
      <div class="cohort-line">
        <span>${p.pilots.filled} of ${p.pilots.of} pilots</span>
        <span>${p.participants.filled} of ${p.participants.of} participants</span>
      </div>
      <div class="cohort-line sub">
        <span>${p.analysable} analysable${p.excluded ? `, ${p.excluded} excluded` : ''}</span>
        ${p.imbalance > 1 ? `<span class="off">${p.imbalance} apart on order</span>` : ''}
      </div>`;
    list.append(head);

    let lastKind = null;
    for (const s of slots) {
        if (s.kind !== lastKind) {
            const h = document.createElement('div');
            h.className = 'slot-head';
            h.textContent = s.kind === 'pilot' ? 'Pilots' : 'Participants';
            list.append(h);
            lastKind = s.kind;
        }
        if (!s.participant) {
            const open = document.createElement('button');
            open.className = 'p-item open';
            open.innerHTML = `<div class="code">${esc(s.label)}</div>
                <div class="meta">not created · ${esc(s.order)}</div>`;
            open.onclick = () => createInto(s);
            list.append(open);
            continue;
        }
        const person = s.participant;
        const b = document.createElement('button');
        b.className = 'p-item';
        b.setAttribute('aria-current',
            String(state.view !== 'results' && person.code === state.selected));
        b.innerHTML = `<div class="code">${esc(s.label)}
              <span class="pcode">${esc(person.code)}</span></div>
            <div class="meta">${esc(person.order || 'order not set')}${
                person.excluded ? ' · excluded' : ''}</div>`;
        b.onclick = () => select(person.code);
        list.append(b);
    }

    // Anyone past the end of the plan. Rare, and worse than useless if hidden.
    for (const person of extra) {
        const b = document.createElement('button');
        b.className = 'p-item extra';
        b.setAttribute('aria-current', String(person.code === state.selected));
        b.innerHTML = `<div class="code">${esc(person.code)}</div>
            <div class="meta">beyond the planned ${PARTICIPANTS}</div>`;
        b.onclick = () => select(person.code);
        list.append(b);
    }
}

/** Create the person who belongs in this slot, with the order the plan says. */
/**
 * The study's keys, entered once and issued to each participant.
 *
 * Kept here rather than typed on a call, and copied to the participant when
 * they are created so their setup script can fetch its own copy. Nobody pastes
 * a key: a key copied by hand is a key that ends up in the wrong window, and
 * the copying is the step that fails while somebody is waiting.
 */
function watchKeys() {
    onSnapshot(doc(db, 'settings/keys'), (snap) => {
        state.keys = snap.exists() ? snap.data() : {};
        renderKeys();
    }, () => { state.keys = {}; });
}

const keyTail = (k) => (k ? `…${String(k).slice(-6)}` : '');

function renderKeys() {
    const el = $('#keys');
    if (!el) return;
    const k = state.keys || {};
    const have = !!(k.anthropicApiKey && k.openaiApiKey);
    el.innerHTML = `
      <h3>Session keys</h3>
      <p class="hint">Entered once. Every participant created after this gets a
      copy, and their setup script fetches it with their code — nobody pastes a
      key.</p>
      <div class="who-row">
        <label>Anthropic<input id="k-anthropic" type="password"
          placeholder="${k.anthropicApiKey ? `set ${keyTail(k.anthropicApiKey)}` : 'sk-ant-…'}"></label>
        <label>OpenAI<input id="k-openai" type="password"
          placeholder="${k.openaiApiKey ? `set ${keyTail(k.openaiApiKey)}` : 'sk-…'}"></label>
        <button id="k-save">Save</button>
      </div>
      <p class="hint">${have
        ? 'Anything holding a participant link can read that participant’s copy. That is the price of not pasting, so these must be keys issued for the study, with a hard spend cap, revoked when the sessions end.'
        : 'No keys yet. Setup will tell a participant to come back to you rather than failing halfway.'}</p>`;
    el.querySelector('#k-save').onclick = async () => {
        const a = el.querySelector('#k-anthropic').value.trim();
        const o = el.querySelector('#k-openai').value.trim();
        const update = {};
        if (a) update.anthropicApiKey = a;
        if (o) update.openaiApiKey = o;
        if (!Object.keys(update).length) return;
        try {
            await setDoc(doc(db, 'settings/keys'), { ...update, updatedAt: Date.now() }, { merge: true });
            el.querySelector('#k-anthropic').value = '';
            el.querySelector('#k-openai').value = '';
        } catch (err) {
            alert(`Could not save: ${err.code || err.message}`);
        }
    };
}

/** Give one participant their own copy, or take it away again. */
async function issueKeys(code) {
    const k = state.keys || {};
    if (!k.anthropicApiKey) return false;
    await setDoc(doc(db, `participants/${code}/secrets/session`), {
        anthropicApiKey: k.anthropicApiKey,
        openaiApiKey: k.openaiApiKey || '',
        issuedAt: Date.now(),
    });
    return true;
}

async function revokeKeys(code) {
    if (!confirm(`Revoke the keys issued to ${code}?\n\nTheir copy is cleared, so `
        + 'a setup run from now on gets nothing. Revoke them at the provider too — '
        + 'this only stops them being handed out again.')) return;
    try {
        await deleteDoc(doc(db, `participants/${code}/secrets/session`));
        renderManage();
    } catch (err) {
        alert(`Could not revoke: ${err.code || err.message}`);
    }
}

async function createInto(slot) {
    // The kind is in the code, so it survives every export, CSV and zip that
    // knows nothing about a `pilot` field.
    const code = newParticipantCode(slot.kind === 'pilot' ? 'pilot' : 'participant');
    try {
        await setDoc(doc(db, 'participants', code), {
            createdAt: Date.now(),
            order: slot.order,
            // The language the WHOLE session runs in: this page's, the questions,
            // the task cards, both descriptions and both workspaces. Chosen here
            // because it decides which workspace archives their setup unpacks,
            // and setup runs days before anybody could change their mind.
            lang: slot.lang || state.newLang || DEFAULT_LANGUAGE,
            pilot: slot.kind === 'pilot',
            released: false,
        });
        // Their own copy, so setup can fetch it. Issued at creation rather than
        // on demand, because the moment it is missing is the moment somebody is
        // running setup and cannot ask.
        const issued = await issueKeys(code);
        select(code);
        if (!issued) {
            alert('This participant has no keys, because none are set yet.\n\n'
                + 'Put them in under Session keys, then press Reissue on their page. '
                + 'Their setup will say to come back to you rather than failing halfway.');
        }
    } catch (err) {
        alert(`Could not create a participant: ${err.code || err.message}`);
    }
}

$('#show-results').addEventListener('click', () => { void showResults(); });

/**
 * Create the next of a kind.
 *
 * Two buttons rather than one, because the kind cannot be recovered later: it is
 * baked into the code, and a pilot created as a participant would quietly enter
 * the analysis. Asking at the only moment it is knowable is cheaper than any
 * amount of fixing afterwards.
 */
function createNext(kind) {
    const { slots } = fill(state.participants);
    const open = slots.find((s) => s.kind === kind && !s.participant);
    void createInto(open || { kind, order: nextOrder(state.participants, kind) });
}

$('#new-participant').addEventListener('click', () => createNext('participant'));
$('#new-pilot').addEventListener('click', () => createNext('pilot'));

// Which language the next participant is created in. A select rather than a
// per-participant edit: it decides which archives their setup unpacks, and setup
// runs days ahead, so changing it afterwards would leave a machine already built
// in the other language.
const langPicker = $('#new-lang');
if (langPicker) {
    langPicker.replaceChildren(...LANGUAGES.map((code) => {
        const o = document.createElement('option');
        o.value = code;
        o.textContent = code === 'en' ? 'English' : code === 'zh-Hans' ? '简体中文' : code;
        return o;
    }));
    langPicker.value = state.newLang;
    langPicker.onchange = () => { state.newLang = langPicker.value; };
}

// ── one participant ──────────────────────────────────────────────────────────

function select(code) {
    state.view = 'participant';
    state.selected = code;
    state.actions = [];
    state.devices = [];
    renderRoster();
    renderDetail();
    watchBatches();
    watchDevices();
}

// Which of the participant's two machines-worth of software has checked in.
//
// The browser slot is claimed when they open their link; the mirror slot when
// their editor starts with a code set. Between them they answer the only
// question worth asking before a session starts, which is whether the handoff
// worked. Nothing else reports it: a participant who never typed their code
// looks exactly like one who has not started yet.
function watchDevices() {
    if (state.unsubDevices) state.unsubDevices();
    state.unsubDevices = onSnapshot(
        collection(db, `participants/${state.selected}/devices`),
        (snap) => { state.devices = snap.docs.map((d) => d.id); renderHandoff(); },
        () => { state.devices = []; renderHandoff(); },
    );
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
        Press New. You get a code and a link to send them, and everything they do
        arrives here against that code.
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
      <div class="card handoff" id="handoff"></div>
      <div class="card manage" id="manage"></div>
      <div class="tabs" id="tabs"></div>
      <div class="card run" id="running"></div>
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
      <div class="card" id="interview"></div>
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
    renderManage();
    renderHandoff();
    renderRunning();
    renderForms();
    renderInterview();
    renderSession();
    watchAssessment();
    watchInterview();
    watchAnswers();
    watchContact();
}

// ── managing one participant ─────────────────────────────────────────────────

/**
 * Who this is, and what can be done to them.
 *
 * The name and the note are held in a separate collection that the export never
 * touches, so a collaborator who is given a session's data cannot see who it
 * belonged to. That separation is the whole reason it is not simply a field on
 * the participant.
 */
function renderManage() {
    const el = $('#manage');
    if (!el) return;
    const p = state.participants.find((x) => x.code === state.selected);
    if (!p) return;
    const c = state.contact || {};

    el.innerHTML = `
      <h3>Who this is</h3>
      <p class="hint">Kept apart from their session data, and never exported with
      it. For your own scheduling; nobody analysing the results sees it.</p>
      <div class="who-row">
        <label>Name<input id="c-name" value="${esc(c.name || '')}" placeholder="For your list"></label>
        <label>Email<input id="c-email" value="${esc(c.email || '')}" placeholder="For scheduling"></label>
      </div>
      <label class="who-note">Note
        <textarea id="c-note" rows="2" placeholder="When they are booked, anything to remember">${esc(c.note || '')}</textarea>
      </label>

      <h3 class="manage-h">Managing this ${isPilot(p) ? 'pilot' : 'participant'}</h3>
      <div class="manage-row">
        <label class="toggle">
          <input type="checkbox" id="m-excluded" ${p.excluded ? 'checked' : ''}>
          Leave out of the analysis
        </label>
        <span class="hint">${p.excluded
            ? 'Their session still counts as having happened, and is not analysed.'
            : 'Use this if something went wrong, or they should not have been run.'}</span>
      </div>
      <div class="manage-row">
        <button id="m-reissue">Reissue keys</button>
        <button id="m-revoke" class="danger">Revoke</button>
        <span class="hint">Their setup fetches these with their code. Revoke if a
        key leaks, then revoke it at the provider too.</span>
      </div>
      <div class="manage-row">
        <button id="m-reset">Reset their data</button>
        <span class="hint">Clears every answer and session this code holds, and frees
        their devices. The code and the order stay, so they can start again.</span>
      </div>
      <div class="manage-row">
        <button id="m-delete" class="danger">Delete</button>
        <span class="hint">Removes the code and everything under it. There is no undo.</span>
      </div>`;

    for (const [id, key] of [['c-name', 'name'], ['c-email', 'email'], ['c-note', 'note']]) {
        const input = el.querySelector(`#${id}`);
        input.oninput = () => saveContact(key, input.value);
    }
    el.querySelector('#m-excluded').onchange = (e) => void setExcluded(p.code, e.target.checked);
    el.querySelector('#m-reissue').onclick = async () => {
        const ok = await issueKeys(p.code).catch(() => false);
        alert(ok ? 'Issued. Their setup can fetch them now.'
                 : 'No keys are set yet — put them in under Session keys first.');
    };
    el.querySelector('#m-revoke').onclick = () => void revokeKeys(p.code);
    el.querySelector('#m-reset').onclick = () => void resetParticipant(p.code);
    el.querySelector('#m-delete').onclick = () => void deleteParticipant(p.code);
}

let contactTimer;
function saveContact(key, value) {
    state.contact = { ...(state.contact || {}), [key]: value };
    clearTimeout(contactTimer);
    contactTimer = setTimeout(() => {
        void setDoc(doc(db, `contacts/${state.selected}`),
            { ...state.contact, updatedAt: Date.now() }, { merge: true })
            .catch((err) => console.warn('contact not saved', err.code));
    }, 500);
}

function watchContact() {
    if (state.unsubContact) state.unsubContact();
    state.contact = {};
    state.unsubContact = onSnapshot(doc(db, `contacts/${state.selected}`), (snap) => {
        // Not while somebody is typing into it.
        if (document.activeElement && document.activeElement.closest('#manage')) return;
        state.contact = snap.exists() ? snap.data() : {};
        renderManage();
    }, () => { state.contact = {}; });
}

async function setExcluded(code, excluded) {
    try {
        await setDoc(doc(db, 'participants', code), { excluded }, { merge: true });
    } catch (err) {
        alert(`Could not change that: ${err.code || err.message}`);
    }
}

/** Everything under a participant, so a code can be handed out again. */
async function clearUnder(code) {
    for (const c of CONDITIONS) {
        const batches = await getDocs(
            collection(db, `participants/${code}/sessions/${c}/batches`));
        for (const d of batches.docs) await deleteDoc(d.ref);
    }
    for (const sub of ['answers', 'assessments', 'devices']) {
        const snap = await getDocs(collection(db, `participants/${code}/${sub}`));
        for (const d of snap.docs) await deleteDoc(d.ref);
    }
}

async function resetParticipant(code) {
    if (!confirm(`Reset ${code}?\n\nEvery answer and session this code holds is `
        + 'deleted, and their devices are freed. The code and the order stay, so '
        + 'they can start again with the same link.\n\nThere is no undo.')) return;
    try {
        await clearUnder(code);
        alert(`${code} is empty again. Their existing link still works.`);
    } catch (err) {
        alert(`Could not reset: ${err.code || err.message}`);
    }
}

async function deleteParticipant(code) {
    if (!confirm(`Delete ${code}?\n\nThe code and everything under it goes. `
        + 'If you only want them out of the results, tick "leave out of the '
        + 'analysis" instead — that keeps the record that a session happened.'
        + '\n\nThere is no undo.')) return;
    try {
        await clearUnder(code);
        await deleteDoc(doc(db, 'contacts', code)).catch(() => {});
        await deleteDoc(doc(db, 'participants', code));
        state.selected = null;
    } catch (err) {
        alert(`Could not delete: ${err.code || err.message}`);
    }
}

// ── handing the session over ─────────────────────────────────────────────────

/**
 * Free the device slots so a participant can register again.
 *
 * The slot is claimed once, on purpose: it is what stops a stray copy of the code
 * writing into somebody's session. But a participant who changes machine or
 * reinstalls is then locked out, and the message their editor shows tells them to
 * ask the experimenter, who, until now, had no way to do it. The pilot hit this
 * and it took a hand-written REST call to clear, which is not something to be
 * doing on a call.
 */
async function releaseCode(code) {
    const held = state.devices.join(' and ');
    if (!confirm(`Release ${code}?\n\nThis frees the ${held} slot so their `
        + 'software can register again. Nothing they have already sent is touched.')) return;
    try {
        for (const slot of state.devices) {
            await deleteDoc(doc(db, `participants/${code}/devices/${slot}`));
        }
    } catch (err) {
        alert(`Could not release: ${err.code || err.message}`);
    }
}

/**
 * The two things to send, and whether they landed.
 *
 * This is the first card on the page until both have checked in, and one quiet
 * line afterwards. A code that never reaches the participant's editor costs the
 * whole session and is invisible on their shared screen, so it is worth the room
 * it takes early and not worth any of it later.
 */
function renderHandoff() {
    const el = $('#handoff');
    if (!el) return;
    const p = state.participants.find((x) => x.code === state.selected);
    if (!p) return;

    const browser = state.devices.includes('browser');
    const mirror = state.devices.includes('mirror');
    el.classList.toggle('settled', browser && mirror);

    if (browser && mirror) {
        el.innerHTML = `<div class="hs">
            <span class="tick">●</span> They have the page open and their editor is
            reporting. <button class="link-btn" id="handoff-more">show the link again</button>
        </div>`;
        $('#handoff-more').onclick = () => { el.classList.remove('settled'); renderOpenHandoff(el, p, browser, mirror); };
        return;
    }
    renderOpenHandoff(el, p, browser, mirror);
}

function renderOpenHandoff(el, p, browser, mirror) {
    const dot = (on) => `<span class="tick ${on ? 'on' : 'off'}">${on ? '●' : '○'}</span>`;
    el.innerHTML = `
      <h3>Send this</h3>
      <p class="hint">One link, and it is the whole handoff. The download, the
      command and the keys are all on it, and it carries the code everything they
      do is filed against.</p>

      <div class="give">
        <label>Their link</label>
        <div class="give-row">
          <code>${esc(linkFor(p))}</code>
          <button data-copy="${esc(linkFor(p))}">Copy</button>
        </div>
        <p class="give-note">${dot(browser)} ${browser
            ? 'They have opened it.'
            : 'Not opened yet. Send it now, before anything else.'}</p>
      </div>

      <div class="give">
        <label>What their page tells them to run</label>
        <div class="give-row">
          <code>${esc(setupFor(p))}</code>
          <button data-copy="${esc(setupFor(p))}">Copy</button>
        </div>
        <p class="give-note">Here so you can read it back to them if they get
        stuck. You do not need to send it.</p>
        <p class="give-note">${dot(mirror)} ${mirror
            ? 'Their editor is reporting.'
            : 'Their editor has not reported. Until it does, nothing they do in it arrives here.'}</p>
        ${state.devices.length ? `<p class="give-note">
          <button class="link-btn" id="release">release this code</button>
          — if they have changed machine, or reinstalled. Their editor says to ask
          you for this, and the slot is claimed once.</p>` : ''}
      </div>

      <div class="give">
        <label>The keys are not sent, and not shown here</label>
        <p class="give-note">Setup fetches them with the code on their page, so
        nobody pastes one. Put them in once under <strong>Session keys</strong>;
        every participant made afterwards gets their own copy, and this card is a
        website, so no key is ever drawn on it.</p>
        <p class="give-note">Setup checks both against the model before it
        finishes, so ask them to read you the last few lines rather than watching
        for it here.</p>
      </div>`;

    const release = el.querySelector('#release');
    if (release) release.onclick = () => void releaseCode(p.code);

    wireCopy(el);
}

/** Make every copy button inside `el` work. */
function wireCopy(el) {
    for (const b of el.querySelectorAll('[data-copy]')) {
        b.onclick = async () => {
            try {
                await navigator.clipboard.writeText(b.dataset.copy);
                b.textContent = 'Copied';
            } catch {
                // Clipboard access can be refused. Select it instead, so the
                // keyboard still works and the button never lies about copying.
                b.textContent = 'Select it';
                const r = document.createRange();
                r.selectNodeContents(b.previousElementSibling);
                getSelection().removeAllRanges();
                getSelection().addRange(r);
            }
            setTimeout(() => { b.textContent = 'Copy'; }, 1600);
        };
    }
}

// ── starting a condition ─────────────────────────────────────────────────────

/**
 * The steps for starting the condition on screen.
 *
 * There is far less to do than there used to be. The participant no longer
 * starts a daemon, stops it, runs a player and starts it again: the launcher
 * does all four, because every one of them was a command with the word replay in
 * it typed by the person who was not supposed to know there was one. What is
 * left is opening the folder, checking the logger is recording, and watching.
 */
function renderRunning() {
    const el = $('#running');
    if (!el) return;
    const p = state.participants.find((x) => x.code === state.selected);
    if (!p) return;

    const codoc = state.condition === 'codoc';
    const project = projectFor(p, state.condition);
    const folder = `~/codoc-study/${project}`;

    const line = (text, command) => `<li>${text}${command ? `
        <div class="give-row">
          <code>${esc(command)}</code>
          <button data-copy="${esc(command)}">Copy</button>
        </div>` : ''}</li>`;

    const steps = [
        line(`They open <code>${esc(folder)}</code> in VS Code and answer the trust
              prompt with "Yes, I trust the authors". Until they do, VS Code turns
              every extension off and the session records nothing.`),
        codoc
            ? line('They open the description with Cmd+Shift+P and "codoc: Open", and '
                + 'open a terminal inside VS Code.')
            : line('They open <code>CLAUDE.md</code> from the file tree, and open a '
                + 'terminal inside VS Code.'),
        line('They run "Study logger: show what is being recorded" from Cmd+Shift+P, '
            + 'and read you the snapshot count. Anything above zero is fine. A zero '
            + 'here is the one fault that cannot be repaired afterwards.'),
        line('They read the project page, then the page about the way of working. '
            + 'Five minutes each. Answer questions, but do not add to either page.'),
        line('On the task page they start the agent with <code>./claude-study</code> '
            + 'and paste in the request the page gives them. It works for about three '
            + 'minutes. Let them watch it.'),
        line('Twenty minutes from there. Say "about ten minutes gone" once, at the '
            + 'halfway point, and call time at twenty.'),
    ];

    el.innerHTML = `
      <h3>Starting the condition ${codoc ? 'with codoc' : 'without codoc'}</h3>
      <p class="hint">Their project for this half is <strong>${esc(project)}</strong>.
      They type everything themselves, on the screen you are watching.</p>
      <ol class="do">${steps.join('')}</ol>

      <div class="say">
        <h4>What to say when the agent starts working</h4>
        <p class="quote">That is it running. Watch what it does, and when it stops,
        decide what you want to keep.</p>
        <p class="hint">Do not say the session was recorded, and do not say whether
        anything in the change is right or wrong. If they ask, tell them to work from
        the request and what they find in the project.</p>
      </div>

      <p class="hint">If the agent starts and no work arrives, the recording for that
      folder is missing: run <code>./setup.sh --check</code> in their bundle. If it
      stops partway, they can run <code>./claude-study</code> again, which restarts
      the same session from the beginning.</p>`;

    wireCopy(el);
}

// ── the forms ────────────────────────────────────────────────────────────────

/**
 * Everything the participant has answered, as they answer it.
 *
 * It used to sort the answers a second time into a per-project shape, keyed by
 * question and sitting, for the panel that scored the open-book round before the
 * task. That round is gone and so is the panel, and what was left was a regular
 * expression matching document names nothing writes.
 */
function watchAnswers() {
    if (state.unsubQuiz) state.unsubQuiz();
    state.unsubQuiz = onSnapshot(
        collection(db, `participants/${state.selected}/answers`),
        (snap) => {
            state.answers = {};
            for (const d of snap.docs) state.answers[d.id] = d.data();
            renderForms();
        },
        () => { state.answers = {}; renderForms(); },
    );
}

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
        <h4>Their sign-off</h4>
        <p class="hint">They answer this on their own page, straight after the
        task. It appears here as they write it.</p>
        <div id="their-signoff" class="said"></div>
      </div>

      <div class="form-block">
        <h4>What they knew afterwards, from memory</h4>
        <p class="hint">Five questions about the change they just made, answered on
        their own page with the code, the description and the agent closed. They
        have right answers, so nothing here needs marking by hand.</p>
        <div id="their-reflection" class="said"></div>
      </div>

      <div class="form-block">
        <h4>What they found, and who settled it</h4>
        <p class="hint">The problems planted in the change the agent made. Rate
        detection first, then who settled it. The one marked with a diamond is the
        coupled one, where a change that looks local is not.</p>
        ${(OPEN_DECISIONS[state.project] || []).map((d, n) => `
          <div class="row">
            <span class="row-label wide">${n === COUPLED_DECISION[state.project] ? '\u25c6 ' : ''}${esc(d)}</span>
            <div class="choices" data-detected="${esc(d)}">${CONSISTENCY.map((s, i) => `
              <button data-v="${i}" title="${esc(s)}"
                aria-pressed="${String((a.consistency || {})[d] === i)}">${i}</button>`).join('')}</div>
            <div class="choices" data-decision="${esc(d)}">${SETTLED_BY.map((s, i) => `
              <button data-s="${esc(s)}" title="${esc(s)}"
                aria-pressed="${String((a.decisions || {})[d] === s)}">${i + 1}</button>`).join('')}</div>
          </div>`).join('')}
        <p class="hint">Detection: 0 not found, 1 found, 2 found and tied to what
        it contradicts. Settled by: 1 they directed it, 2 they accepted a proposal
        deliberately, 3 it stands and they never noticed.</p>
      </div>

      <div class="form-block">
        <h4>False alarms</h4>
        <p class="hint">Correct parts of the change they called wrong, including
        the decoy: ${esc((FALSE_ALARMS[state.project] || []).join('; '))}. Write 0
        if there were none, because none and not-asked are different answers.</p>
        <div class="row">
          <span class="row-label wide">How many</span>
          <input id="false-alarms" type="number" min="0" step="1"
            value="${a.falseAlarms == null ? '' : String(a.falseAlarms)}">
        </div>
        <textarea id="false-alarm-notes" rows="2"
          placeholder="What they called wrong, in their words">${esc(a.falseAlarmNotes || '')}</textarea>
      </div>

      <p class="gaps" id="gaps"></p>`;

    wireForms();
    renderGaps();
}

// ── the closing interview ────────────────────────────────────────────────────
//
// Asked out loud, at the end, with both conditions done. It used to be typed by
// the participant on their own page, which got short written answers to
// questions whose whole value is in the follow-up, and asked somebody to write
// for a quarter of an hour after two hours of work. The questions are here so
// they are asked the same way every time, and the box is for what was said.
//
// Stored under its own name rather than per condition, because there is one
// interview and it is about the pair.

let interviewTimer;
function saveInterview() {
    state.interview.updatedAt = Date.now();
    clearTimeout(interviewTimer);
    interviewTimer = setTimeout(() => {
        void setDoc(
            doc(db, `participants/${state.selected}/assessments/interview`),
            state.interview, { merge: true },
        ).catch((err) => console.warn('interview not saved', err.code));
    }, 500);
}

function watchInterview() {
    if (state.unsubInterview) state.unsubInterview();
    const path = `participants/${state.selected}/assessments/interview`;
    state.unsubInterview = onSnapshot(doc(db, path), (snap) => {
        const incoming = snap.exists() ? snap.data() : null;
        // Same guard as the assessment: never stamp over a box being typed in.
        if (!state.interview || !document.activeElement
            || !document.activeElement.closest('#interview')) {
            state.interview = incoming || {};
            renderInterview();
        }
    }, () => { state.interview = state.interview || {}; });
}

function renderInterview() {
    const el = $('#interview');
    if (!el) return;
    state.interview = state.interview || {};
    const a = state.interview;

    el.innerHTML = `
      <h3>The closing interview</h3>
      <p class="hint">Asked out loud, once, with both conditions done. The
      participant's page tells them to expect it and shows them nothing. Type
      what they say; the recording is the full record.</p>
      ${INTERVIEW.map((part) => `
        <div class="form-block">
          <h4>${esc(part.title)}</h4>
          ${part.questions.map((q) => `
            <label class="iv">
              <span class="iv-q">${esc(q.label)}</span>
              <textarea rows="2" data-iv="${esc(q.id)}"
                placeholder="what they said">${esc(a[q.id] || '')}</textarea>
            </label>`).join('')}
        </div>`).join('')}`;

    for (const t of el.querySelectorAll('[data-iv]')) {
        t.oninput = () => { a[t.dataset.iv] = t.value; saveInterview(); };
    }
}

function projectFor(p, condition) {
    // scribe goes with whichever condition comes first, so the pairing of
    // project to condition alternates with the order and neither project is
    // always the one seen fresh.
    if (!p || !p.order) return condition === 'codoc' ? 'scribe' : 'tally';
    const codocFirst = p.order === 'codoc-first';
    if (condition === 'codoc') return codocFirst ? 'scribe' : 'tally';
    return codocFirst ? 'tally' : 'scribe';
}

// The panel that showed the open-book round before the task is gone with the
// round itself. It asked about the CODEBASE, and the task is now a review of a
// change to that codebase, so its first half was the same activity with the
// clock running twice. The only set left has right answers and is rendered by
// `wireForms`, under the sign-off, because it is answered after the task.

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

    // Their sign-off, shown rather than typed. It arrives with everything else
    // they answer, so this only has to render it.
    // Their four closed-book answers, shown rather than typed. Nothing here is
    // scored during the session: rating what somebody wrote about their own
    // change, while listening to them talk, is how a rating ends up measuring
    // how well they explained rather than what they knew.
    // The closed-book set, scored here because it has right answers. Read only:
    // the participant answers it on their own page, and a researcher marking it
    // during a session would be scoring while listening.
    const rf = $('#their-reflection');
    if (rf) {
        const answers = (state.answers || {})[`reflect-${state.condition}`];
        const set = afterFor(state.project);
        if (!answers || !set.length) {
            rf.innerHTML = '<span class="hint">Not answered yet.</span>';
        } else {
            const right = set.filter((q) => answers[`a${q.n}`] === q.answer).length;
            rf.innerHTML = `
              <div class="quiz-score">
                <span><b>${right}</b>/${set.length} correct</span>
                <span>sure of it: <b>${esc(String(answers.recall ?? '—'))}</b>/5</span>
              </div>
              ${set.map((q) => {
        const given = answers[`a${q.n}`];
        const mark = given == null ? '<i class="unanswered">·</i>'
            : given === q.answer ? '<i class="right">✓</i>' : `<i class="wrong">${esc(given)}</i>`;
        return `<div class="q-line">
                  <span class="q-marks">${mark}</span>
                  <span class="q-title">${q.n}. ${esc(q.question)}</span>
                </div>`;
    }).join('')}
              <p class="hint">A letter is the wrong option they chose. The scale is
              their own read on whether they knew it or worked it out just now.</p>`;
        }
    }

    const said = $('#their-signoff');
    if (said) {
        const sign = (state.answers || {})[`signoff-${state.condition}`];
        said.innerHTML = !sign ? '<span class="hint">Not answered yet.</span>' : `
          <p><b>${esc(sign.correct || '—')}</b>, confidence ${esc(String(sign.confidence ?? '—'))}/5</p>
          <p class="hint">Resting on: ${esc((sign.grounds || []).join(', ') || 'nothing selected')}</p>
          ${sign.unsure ? `<p class="quote">${esc(sign.unsure)}</p>` : ''}`;
    }

    const alarms = $('#false-alarms');
    if (alarms) {
        alarms.onchange = () => {
            a.falseAlarms = alarms.value === '' ? null : Number(alarms.value);
            saveAssessment();
        };
    }
    const alarmNotes = $('#false-alarm-notes');
    if (alarmNotes) {
        alarmNotes.onchange = () => { a.falseAlarmNotes = alarmNotes.value; saveAssessment(); };
    }

    for (const group of document.querySelectorAll('[data-detected]')) {
        for (const b of group.querySelectorAll('button')) {
            b.onclick = () => {
                a.consistency = a.consistency || {};
                a.consistency[group.dataset.detected] = Number(b.dataset.v);
                for (const s of group.querySelectorAll('button')) {
                    s.setAttribute('aria-pressed', String(s === b));
                }
                saveAssessment();
                renderGaps();
            };
        }
    }

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
