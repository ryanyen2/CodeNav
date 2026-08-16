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
import { newParticipantCode } from '../shared/schema.js';
import { fill, progress, nextOrder, PARTICIPANTS } from '../shared/cohort.js';
import { renderResults } from './results.js';
import { esc } from '../shared/html.js';
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
    unsubDevices: null,
    devices: [],
    assessment: null,
    project: 'hearth',
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
const linkFor = (p) => `${PARTICIPANT_PAGE}?code=${p.code}&order=${p.order || 'codoc-first'}`;
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
async function createInto(slot) {
    const code = newParticipantCode();
    try {
        await setDoc(doc(db, 'participants', code), {
            createdAt: Date.now(),
            order: slot.order,
            pilot: slot.kind === 'pilot',
            released: false,
        });
        select(code);
    } catch (err) {
        alert(`Could not create a participant: ${err.code || err.message}`);
    }
}

$('#show-results').addEventListener('click', () => { void showResults(); });

$('#new-participant').addEventListener('click', () => {
    const { slots } = fill(state.participants);
    const open = slots.find((s) => s.kind === 'participant' && !s.participant)
        || slots.find((s) => !s.participant);
    void createInto(open || {
        kind: 'participant', order: nextOrder(state.participants),
    });
});

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
    renderHandoff();
    renderForms();
    renderSession();
    watchAssessment();
}

// ── handing the session over ─────────────────────────────────────────────────

/**
 * Free the device slots so a participant can register again.
 *
 * The slot is claimed once, on purpose: it is what stops a stray copy of the code
 * writing into somebody's session. But a participant who changes machine or
 * reinstalls is then locked out, and the message their editor shows tells them to
 * ask the experimenter — who, until now, had no way to do it. The pilot hit this
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
      <h3>Send these</h3>
      <p class="hint">The first two carry the code. Everything they do is filed against it.</p>

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
        <label>What they run in the folder they unzipped</label>
        <div class="give-row">
          <code>${esc(setupFor(p))}</code>
          <button data-copy="${esc(setupFor(p))}">Copy</button>
        </div>
        <p class="give-note">${dot(mirror)} ${mirror
            ? 'Their editor is reporting.'
            : 'Their editor has not reported. Until it does, nothing they do in it arrives here.'}</p>
        ${state.devices.length ? `<p class="give-note">
          <button class="link-btn" id="release">release this code</button>
          — if they have changed machine, or reinstalled. Their editor says to ask
          you for this, and the slot is claimed once.</p>` : ''}
      </div>

      <div class="give">
        <label>The two keys, by hand</label>
        <p class="give-note">Setup asks for an Anthropic key and an OpenAI key.
        Read them down the call, or send a <code>keys.env</code> separately. They
        are deliberately not held here, and not in the bundle: this page is a
        website, and the bundle is built once and goes to everybody.</p>
        <p class="give-note">Setup checks both against the model before it
        finishes, so ask them to read you the last few lines rather than watching
        for it here. Nothing about a key reaches this page.</p>
      </div>`;

    const release = el.querySelector('#release');
    if (release) release.onclick = () => void releaseCode(p.code);

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
