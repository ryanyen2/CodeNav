// The page a participant works through.
//
// One step on screen at a time, in a fixed order, saving as they go. It does not
// pace them: the researcher is on the call and can see their screen, so the only
// job here is to ask the right things in the right order and lose nothing.
//
// If Firestore cannot be reached, the page says so and the session carries on
// with the scripts, because a study that stops when a website does is worse than
// one that never had a website.
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, connectAuthEmulator } from 'firebase/auth';
import {
    getFirestore, doc, getDoc, setDoc, connectFirestoreEmulator, serverTimestamp,
} from 'firebase/firestore';
import {
    CONSENT_FORM, PRESTUDY_FORM, SCREENING, AFTER_CONDITION, CONSTRUCTS,
    scaleFor, MANIPULATION_CHECK, SCENARIOS, DEBRIEF, TASK_CARDS,
    PROJECTS, RESPONSIBILITY, HOW_TO_START, buildSteps, answerDoc,
} from './steps.js';
import { drawCard } from './card.js';
import { esc } from '../shared/html.js';

const firebaseConfig = {
    apiKey: 'AIzaSyCeIFBc8HhCmtw9-pXjUm1qT3CUyo5GbkY',
    authDomain: 'codoc-11b10.firebaseapp.com',
    projectId: 'codoc-11b10',
    storageBucket: 'codoc-11b10.firebasestorage.app',
    messagingSenderId: '23316400560',
    appId: '1:23316400560:web:1ba4963bb0de7ef1622e75',
};

const params = new URLSearchParams(location.search);
const CODE = params.get('code') || '';

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
if (params.has('emulator')) {
    connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
    connectFirestoreEmulator(db, '127.0.0.1', 8080);
}

const $ = (s) => document.querySelector(s);
const stage = $('#stage');

const state = {
    code: CODE,
    order: 'codoc-first',
    steps: [],
    at: 0,
    answers: {},        // docName -> { id: value }
    online: false,
};

// Where they had got to, so a reload does not start them again.
const localKey = () => `codoc-study:${state.code}`;
const remember = () => {
    try { localStorage.setItem(localKey(), JSON.stringify({ at: state.at, answers: state.answers })); }
    catch { /* private browsing; the server copy is the real one */ }
};
const recall = () => {
    try { return JSON.parse(localStorage.getItem(localKey()) || '{}'); } catch { return {}; }
};

// ── starting up ──────────────────────────────────────────────────────────────

async function start() {
    if (!state.code) return fatal('This link is missing its code.',
        'Open the study page from VS Code, or ask the researcher for your link.');

    let participant = null;
    try {
        await signInAnonymously(auth);
        // Claiming the browser slot is what lets this page write anything. The
        // mirror inside the editor claims a different one, so the two never
        // collide.
        await setDoc(doc(db, `participants/${state.code}/devices/browser`), {
            uid: auth.currentUser.uid, kind: 'browser', registeredAt: Date.now(),
        }, { merge: false }).catch(() => {});
        const snap = await getDoc(doc(db, `participants/${state.code}`));
        participant = snap.exists() ? snap.data() : null;
        state.online = true;
    } catch {
        state.online = false;
    }

    // The participant document is not readable by the participant, by design, so
    // the order comes from the link. Falling back keeps the page usable if the
    // link is bare.
    state.order = params.get('order') || (participant && participant.order) || 'codoc-first';
    state.steps = buildSteps(state.order);

    const saved = recall();
    state.at = Math.min(saved.at || 0, state.steps.length - 1);
    state.answers = saved.answers || {};

    render();
}

function fatal(title, detail) {
    stage.innerHTML = `<section class="step"><h1>${title}</h1><p>${detail}</p></section>`;
    $('#next').hidden = true;
    $('#back').hidden = true;
}

// ── saving ───────────────────────────────────────────────────────────────────

let saveTimer;
async function save(step) {
    const name = answerDoc(step);
    if (!name) return;
    remember();
    if (!state.online) { flash('saved on this computer'); return; }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        try {
            await setDoc(doc(db, `participants/${state.code}/answers/${name}`), {
                ...state.answers[name], updatedAt: Date.now(),
            }, { merge: true });
            flash('saved');
        } catch {
            state.online = false;
            flash('saved on this computer');
        }
    }, 400);
}

function flash(text) {
    const el = $('#saved');
    el.textContent = text;
    el.style.opacity = '1';
    setTimeout(() => { el.style.opacity = '0'; }, 1800);
}

const answersFor = (step) => {
    const name = answerDoc(step);
    if (!name) return {};
    state.answers[name] = state.answers[name] || {};
    return state.answers[name];
};

// ── rendering ────────────────────────────────────────────────────────────────

function render() {
    const step = state.steps[state.at];
    $('#rail').style.width = `${(state.at / (state.steps.length - 1)) * 100}%`;

    const section = document.createElement('section');
    section.className = 'step';
    section.innerHTML = VIEWS[step.kind](step);
    stage.replaceChildren(section);
    window.scrollTo({ top: 0, behavior: 'instant' });

    if (step.kind === 'task') {
        // Light, like the rest of the page, rather than following the machine.
        // The card is a picture that goes into the screen recording, and a card
        // that is dark for one participant and light for the next is one more
        // thing that differs between sessions for no reason.
        drawCard(section.querySelector('.card-wrap'), TASK_CARDS[step.project], { dark: false });
    }
    wire(section, step);

    $('#back').hidden = state.at === 0;
    $('#next').hidden = step.kind === 'done';
    $('#next').textContent = step.kind === 'task' ? 'I have finished the task' : 'Continue';
    updateNext(step);
}

/** Whether this step has been answered enough to move on. */
function complete(step) {
    const a = answersFor(step);
    const given = (id) => a[id] !== undefined && String(a[id]).trim() !== '';
    if (step.kind === 'screening') return SCREENING.every((q) => given(q.id));
    if (step.kind === 'questionnaire') {
        // The free-text answer is optional. Blocking on it would buy blank
        // characters typed to get past the button, which is worse than no answer.
        return AFTER_CONDITION.every((q) => a[q.id] !== undefined)
            && MANIPULATION_CHECK.filter((q) => q.type !== 'longtext').every((q) => given(q.id));
    }
    if (step.kind === 'scenarios') return SCENARIOS.every((s) => given(s.id));
    return true;
}

function updateNext(step) {
    $('#next').disabled = !complete(step);
}

const VIEWS = {
    welcome: () => `
        <h1>Thanks for taking part</h1>
        <p class="lead">This page walks you through the session. It saves as you go,
        so if anything closes you can open the same link and carry on.</p>
        <p>The researcher is on the call with you. Ask them anything at any point,
        and tell them if something on this page does not make sense.</p>
        <div class="note">Nothing you type here is stored with your name. Your
        answers are filed against a code, and only the consent form knows who you
        are.</div>`,

    consent: () => `
        <h1>Consent</h1>
        <p>Please read this and fill it in. It opens in the form below, and your
        answers go to the research team rather than into this page.</p>
        <iframe src="${CONSENT_FORM}" height="900" title="Consent form"></iframe>
        <p>When you have submitted it, continue.</p>`,

    prestudy: () => `
        <h1>A few questions about you</h1>
        <p>So we can describe who took part. There are no right answers, and the
        first field asks for your code, which is at the top of this page.</p>
        <iframe src="${PRESTUDY_FORM}" height="2536" title="Pre-study questionnaire"></iframe>
        <p>When you have submitted it, continue.</p>`,

    screening: (step) => `
        <h1>One more</h1>
        <p>This one is here rather than in the form above because the researcher
        needs to see it before the session.</p>
        ${SCREENING.map((q) => field(q, answersFor(step)[q.id])).join('')}`,

    setup: () => `
        <h1>Set up your machine</h1>
        <p>Unzip the bundle we sent you, open a terminal in that folder, and run
        this. It has your code in it already.</p>
        <p><code class="pick">./setup.sh ${esc(state.code)} ${esc(state.order)}</code></p>
        <p>It will ask you for two keys. The researcher sends those separately,
        not through this page. Paste each one and press enter. They are not shown
        as you type, so they will not appear on screen later when you share it.</p>
        <p>It takes about ten minutes. Do this a few days before the session, not
        on the day.</p>
        <div class="note">The keys are ours and we pay for them, so nothing in
        this study costs you anything and you do not need a Claude plan. They are
        written only into the four project folders, so deleting those folders
        removes them, and your own projects keep using your own account.</div>

        <h2>On the day</h2>
        <p>Run this in the same folder and read the result to the researcher.</p>
        <p><code class="pick">./setup.sh --check</code></p>
        <div class="note">If anything says <strong>fail</strong> or <strong>todo</strong>,
        say so now. It takes a minute to fix here and cannot be fixed afterwards.</div>`,

    intro: (step) => {
        const how = HOW_TO_START[step.condition];
        return `
        <h1>${step.n === 1 ? 'The first way of working' : 'The second way of working'}</h1>
        <p class="lead">${esc(how.title)}.</p>
        <ol class="do">
          ${how.steps.map(([text, cmd]) => `<li>${esc(text)}
            ${cmd ? `<code class="pick">${esc(cmd.replace('~/codoc-study/hearth', how.folder(step.project)))}</code>` : ''}
          </li>`).join('')}
        </ol>
        <p class="lead">The folder is <code class="pick">${esc(how.folder(step.project))}</code></p>
        <h2>What the written description is here</h2>
        ${how.about.map((t) => `<p>${esc(t)}</p>`).join('')}`;
    },

    // Everything on this page used to be a file the researcher shared on the
    // call. Reading it here means nobody leaves the page while forming their
    // first picture of the codebase, and every participant gets the same words.
    about: (step) => {
        const p = PROJECTS[step.project];
        return `
        <h1>About ${esc(p.name)}</h1>
        ${p.what.map((t) => `<p class="lead">${esc(t)}</p>`).join('')}

        <h2>${esc(p.flow.caption)}</h2>
        <table class="flow"><tbody>${p.flow.lines.map(([from, to, note]) => `
          <tr><td class="mono">${esc(from)}</td>
              <td class="arrow">${from && to ? '→' : ''}</td>
              <td class="mono">${esc(to)}</td>
              <td class="note-cell">${esc(note)}</td></tr>`).join('')}
        </tbody></table>

        <p>${esc(p.sample.caption)}</p>
        <pre class="sample">${esc(p.sample.code)}</pre>

        <h2>The commands you will use</h2>
        <table class="cmds"><tbody>${p.commands.map(([c, w]) => `
          <tr><td class="mono">${esc(c)}</td><td>${esc(w)}</td></tr>`).join('')}
        </tbody></table>
        <p class="fine">${esc(p.commandNote)}</p>

        <h2>Where things live</h2>
        <table class="cmds"><tbody>${p.layout.map(([c, w]) => `
          <tr><td class="mono">${esc(c)}</td><td>${esc(w)}</td></tr>`).join('')}
        </tbody></table>

        <h2>Two words in the task</h2>
        <dl class="words">${p.words.map(([w, d]) => `
          <dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}
        </dl>

        <div class="note">${RESPONSIBILITY.map((t) => `<p>${esc(t)}</p>`).join('')}</div>`;
    },

    task: () => `
        <h1>Your task</h1>
        <div class="card-wrap"></div>
        <p>Anything the card does not say is yours to decide, and we will ask you
        about those decisions, so make them on purpose.</p>`,

    // Grouped by what each block measures, with the groups named. An unbroken
    // column of twenty-five identical rows is answered by pattern rather than by
    // reading, and the headings are the cheapest thing that stops that.
    questionnaire: (step) => {
        const a = answersFor(step);
        return `
        <h1>How that felt</h1>
        <p class="lead">About the way of working you just used. Answer quickly;
        your first reaction is the useful one.</p>
        ${CONSTRUCTS.map((c) => {
            const items = AFTER_CONDITION.filter((q) => q.c === c.id);
            if (!items.length) return '';
            return `<section class="block">
              <h2>${esc(c.title)}</h2>
              ${items.map((q) => scaleRow(q, a[q.id])).join('')}
            </section>`;
        }).join('')}
        <section class="block">
          <h2>In your own words</h2>
          ${MANIPULATION_CHECK.map((q) => field(q, a[q.id])).join('')}
        </section>`;
    },

    debrief: (step) => `
        <h1>Last two</h1>
        <p>Now that both are done. Say as much or as little as you like.</p>
        ${DEBRIEF.map((q) => field(q, answersFor(step)[q.id])).join('')}`,

    break: () => `
        <h1>Take a couple of minutes</h1>
        <p>Stretch, get a drink. When you come back you will do the same kind of
        task the other way, on a different project.</p>`,

    scenarios: (step) => `
        <h1>Which would you pick</h1>
        <p>Now that you have tried both. For each one, pick the way of working you
        would choose.</p>
        ${SCENARIOS.map((s) => scenarioRow(s, answersFor(step)[s.id])).join('')}`,

    done: () => `
        <h1>That is everything</h1>
        <p class="lead">Thank you. One last thing: run this and send the file it
        makes to the researcher.</p>
        <p><code>./collect.sh YOUR-CODE</code></p>
        <p>Once they confirm it arrived, you can delete the
        <code>~/codoc-study</code> folder and the extensions.</p>`,
};

const folderFor = (step) =>
    (step.condition === 'codoc' ? step.project : `${step.project}-baseline`);

function field(q, value) {
    if (q.type === 'choice') {
        return `<div class="q" data-q="${q.id}">
            <span class="label">${esc(q.label)}</span>
            <div class="choices">${q.options.map((o) => `
                <button type="button" data-value="${esc(o)}"
                    aria-pressed="${String(value === o)}">${esc(o)}</button>`).join('')}</div>
        </div>`;
    }
    if (q.type === 'longtext') {
        return `<div class="q" data-q="${q.id}">
            <label class="label" for="f-${q.id}">${esc(q.label)}</label>
            <textarea id="f-${q.id}" rows="3"
                placeholder="${esc(q.placeholder || '')}">${esc(value ?? '')}</textarea>
        </div>`;
    }
    return `<div class="q" data-q="${q.id}">
        <label class="label" for="f-${q.id}">${esc(q.label)}</label>
        <input id="f-${q.id}" type="${q.type}" ${q.min !== undefined ? `min="${q.min}"` : ''}
            ${q.max !== undefined ? `max="${q.max}"` : ''}
            placeholder="${esc(q.placeholder || '')}" value="${esc(value ?? '')}">
    </div>`;
}

function scaleRow(q, value) {
    // Per item, because the workload block runs low-to-high rather than
    // disagree-to-agree. One shared scale would have relabelled six items.
    const s = scaleFor(q);
    const dots = [];
    for (let i = s.min; i <= s.max; i += 1) {
        dots.push(`<button type="button" data-value="${i}"
            aria-label="${i}" aria-pressed="${String(value === i)}">${i}</button>`);
    }
    return `<div class="q" data-q="${q.id}">
        <span class="label">${esc(q.text)}</span>
        <div class="scale">
            <span class="end">${esc(s.lowLabel)}</span>
            <div class="dots">${dots.join('')}</div>
            <span class="end high">${esc(s.highLabel)}</span>
        </div>
    </div>`;
}

function scenarioRow(s, value) {
    const options = ['The first one', 'The second one', 'No preference'];
    return `<div class="q" data-q="${s.id}">
        <span class="label">${esc(s.text)}</span>
        <div class="choices">${options.map((o) => `
            <button type="button" data-value="${esc(o)}"
                aria-pressed="${String(value === o)}">${esc(o)}</button>`).join('')}</div>
    </div>`;
}

function wire(section, step) {
    for (const q of section.querySelectorAll('.q')) {
        const id = q.dataset.q;
        for (const b of q.querySelectorAll('button[data-value]')) {
            b.addEventListener('click', () => {
                const raw = b.dataset.value;
                // Numbers stay numbers; reverse keyed items are stored exactly as
                // answered and flipped once, during analysis, so the stored data
                // always matches what the person actually saw.
                const value = /^\d+$/.test(raw) ? Number(raw) : raw;
                answersFor(step)[id] = value;
                for (const sib of q.querySelectorAll('button[data-value]')) {
                    sib.setAttribute('aria-pressed', String(sib === b));
                }
                void save(step);
                updateNext(step);
            });
        }
        const input = q.querySelector('input, textarea');
        if (input) {
            input.addEventListener('input', () => {
                answersFor(step)[id] = input.value;
                void save(step);
                updateNext(step);
            });
        }
    }
}

// ── moving ───────────────────────────────────────────────────────────────────

$('#next').addEventListener('click', () => {
    const step = state.steps[state.at];
    if (!complete(step)) return;
    void save(step);
    if (state.at < state.steps.length - 1) { state.at += 1; remember(); render(); }
});

$('#back').addEventListener('click', () => {
    if (state.at > 0) { state.at -= 1; remember(); render(); }
});

void start();

export { state, complete, VIEWS };
