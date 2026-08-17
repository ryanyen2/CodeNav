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
    CONSENT_FORM, PRESTUDY, REQUIRED, AFTER_CONDITION, CONSTRUCTS,
    scaleFor, MANIPULATION_CHECK, SCENARIOS, SIGNOFF, INTERVIEW, TASK_CARDS,
    PROJECTS, RESPONSIBILITY, HOW_TO_START, QUIZZES, buildSteps, answerDoc,
} from './steps.js';
import { drawCard } from './card.js';
import { defaultsFor } from './autofill.js';
import { isPilotCode } from '../shared/schema.js';
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

// One name, one path, for everybody. It is not per-condition and not
// per-participant: the same folder holds all four workspaces, and the setup
// script picks which two to use from the code. A name that varied would tell
// somebody which condition they were in before they started.
const BUNDLE_URL = '/bundles/codoc-study-bundle.zip';

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

    pilotBar();
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

    const jump = $('#pilot-jump');
    if (jump && jump.options.length) jump.value = String(state.at);
}

/** Whether this step has been answered enough to move on. */
function complete(step) {
    const a = answersFor(step);
    const given = (id) => a[id] !== undefined && String(a[id]).trim() !== '';
    if (step.kind === 'prestudy') return REQUIRED.every((id) => given(id));
    if (step.kind === 'questionnaire') {
        // The free-text answer is optional. Blocking on it would buy blank
        // characters typed to get past the button, which is worse than no answer.
        return AFTER_CONDITION.every((q) => a[q.id] !== undefined)
            && MANIPULATION_CHECK.filter((q) => q.type !== 'longtext').every((q) => given(q.id));
    }
    if (step.kind === 'scenarios') return SCENARIOS.every((s) => given(s.id));
    if (step.kind === 'signoff') {
        // The free-text one is optional; "no" is a real answer and a required
        // box buys blank characters typed to get past the button.
        return ['correct', 'confidence'].every((id) => given(id))
            && (a.grounds || []).length > 0;
    }
    if (step.kind === 'quiz') {
        // Every one answered. A blank is indistinguishable from "I do not know",
        // and the guess is the data: which wrong option attracted somebody is
        // most of what a wrong answer tells us.
        return (QUIZZES[step.project] || []).every((q) => given(`q${q.n}`));
    }
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

    prestudy: (step) => {
        const a = answersFor(step);
        const shown = PRESTUDY.filter((q) => {
            if (!q.showWhen) return true;
            // A follow-up appears only once the answer that calls for it is
            // given, so nobody is asked to self-describe something they did not
            // choose to.
            const [key, value] = Object.entries(q.showWhen)[0];
            return a[key] === value;
        });
        return `
        <h1>A few questions about you</h1>
        <p class="lead">So we can describe who took part. There are no right
        answers, and you can skip anything marked optional.</p>
        ${shown.map((q) => field(q, a[q.id])).join('')}`;
    },

    // The download is here rather than in an email. A file sent separately from
    // the link is a file that can be the wrong version, and nothing on either
    // side says so.
    setup: () => `
        <h1>Set up your machine</h1>
        <p class="lead">Four steps, about ten minutes, mostly waiting. Do this a
        few days before the session rather than on the day.</p>

        <ol class="do">
          <li>Download the study folder.
            <a class="dl" href="${BUNDLE_URL}" download>Download (about 5 MB)</a></li>
          <li>Unzip it. Double-click on a Mac, or <code class="pick">unzip codoc-study-bundle.zip</code></li>
          <li>Open a terminal in the unzipped folder.
            <code class="pick">cd ~/Downloads/codoc-study-bundle</code></li>
          <li>Run this. Your code is already in it.
            <code class="pick">./setup.sh ${esc(state.code)} ${esc(state.order)}</code></li>
        </ol>

        <p>It prints a line for each thing it does and asks you for nothing. When
        it finishes it either says everything is ready or lists what is missing.
        Send the last few lines to the researcher either way.</p>

        <div class="note">We pay for the AI, so nothing in this study costs you
        anything and you do not need your own plan. The keys come down with your
        code, so you never paste one. Everything it sets up lives inside the four
        project folders, including a separate assistant profile, so your own
        setup is untouched and deleting those folders removes all of it.</div>

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
            ${cmd ? `<code class="pick">${esc(cmd.replace('{folder}', how.folder(step.project)))}</code>` : ''}
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
        <p class="lead">${esc(p.oneLine)}</p>

        <h2>The problem</h2>
        ${p.problem.map((t) => `<p>${esc(t)}</p>`).join('')}
        <div class="ba">
          <div><span class="ba-label">Out of the PDF</span><pre class="sample">${esc(p.before)}</pre></div>
          <div><span class="ba-label">After</span><pre class="sample">${esc(p.after)}</pre></div>
        </div>
        <p class="fine">${esc(p.afterNote)}</p>

        <h2>What it does</h2>
        <p>You do not need to remember these. They are here so nothing in the
        code is a surprise.</p>
        <dl class="words">${p.does.map(([w, d]) => `
          <dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}
        </dl>
        <p>${esc(p.notScope)}</p>

        <h2>The one idea worth holding</h2>
        <p class="lead">Every one of those is a judgement call, and it could have
        gone the other way.</p>
        <dl class="words">${p.judgement.map(([w, d]) => `
          <dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}
        </dl>
        <div class="note">${esc(p.name)} made a choice about each one. The code
        shows you what it chose. It does not tell you why, or what it gave up.</div>

        <h2>Running it</h2>
        <table class="cmds"><tbody>${p.commands.map(([c, w]) => `
          <tr><td class="mono">${esc(c)}</td><td>${esc(w)}</td></tr>`).join('')}
        </tbody></table>
        <p class="fine">${esc(p.commandNote)}</p>

        <h2>The files</h2>
        <p>Nine, and small. You will probably touch two or three.</p>
        <table class="cmds"><tbody>${p.layout.map(([c, w]) => `
          <tr><td class="mono">${esc(c)}</td><td>${esc(w)}</td></tr>`).join('')}
        </tbody></table>
        <p>${esc(p.inputs)}</p>

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

    // The same twelve, before and after. No feedback either time: telling
    // somebody they were wrong before the task would teach them the answer, and
    // the second sitting would measure the telling rather than the session.
    quiz: (step) => {
        const a = answersFor(step);
        const questions = QUIZZES[step.project] || [];
        return `
        <h1>${step.sitting === 'before' ? 'Before you start' : 'A few questions'}</h1>
        <p class="lead">${step.sitting === 'before'
            ? `Twelve questions about ${esc(step.project)}. Answer from what you have just read — a guess is fine, and we expect several.`
            : `The same twelve. Answer from what you know now.`}</p>
        <div class="note">There is no feedback either time, on purpose. We are
        looking at what changed between the two, not at either score.</div>
        ${questions.map((q) => `
          <div class="q" data-q="q${q.n}">
            <span class="label">${q.n}. ${esc(q.question)}</span>
            <div class="opts">${q.options.map((o) => `
              <button type="button" data-value="${esc(o.letter)}"
                aria-pressed="${String(a[`q${q.n}`] === o.letter)}">
                <span class="opt-letter">${esc(o.letter)}</span>
                <span class="opt-text">${esc(o.text)}</span>
              </button>`).join('')}</div>
          </div>`).join('')}`;
    },

    break: () => `
        <h1>Halfway</h1>
        <p class="lead">Take five minutes. Stretch, get a drink, leave the call
        running.</p>
        <p>The second half is the same shape as the first: a different project,
        the other way of working, and the same questions afterwards.</p>
        <div class="note">Leave the folder you have just been working in exactly
        as it is. Nothing needs saving or closing, and we collect it at the
        end.</div>`,

    // Asked once, with both conditions behind them. Which way of working somebody
    // would pick depends heavily on the job, and asking it as one flat question
    // gets an answer about whichever job they happened to picture.
    scenarios: (step) => {
        const a = answersFor(step);
        return `
        <h1>Which one, for which job</h1>
        <p class="lead">For each of these, which of the two ways of working you
        just used would you rather have? "The first one" means the one you did
        first.</p>
        ${SCENARIOS.map((s) => scenarioRow(s, a[s.id])).join('')}`;
    },

    // Their own sign-off, in their own words. It used to be typed into the
    // dashboard while they spoke, which made it a record of how well somebody
    // explained themselves and how fast the researcher could type.
    signoff: (step) => `
        <h1>Before you move on</h1>
        <p class="lead">About the change you just made. There is no right answer
        and nobody is marking it.</p>
        ${SIGNOFF.map((q) => field(q, answersFor(step)[q.id])).join('')}`,

    interview: (step) => {
        const a = answersFor(step);
        return `
        <h1>Last part</h1>
        <p class="lead">Now that both are done. Write as much or as little as you
        like — the researcher will pick some of these up with you.</p>
        ${INTERVIEW.map((part) => `
          <section class="block">
            <h2>${esc(part.title)}</h2>
            ${part.questions.map((q) => field(
        { id: q.id, type: 'longtext', label: q.label }, a[q.id])).join('')}
          </section>`).join('')}`;
    },

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
    if (q.type === 'multi') {
        // More than one can be true at once, and which combination somebody
        // picks is the answer. Forcing a single choice would throw that away.
        const chosen = Array.isArray(value) ? value : [];
        return `<div class="q" data-q="${q.id}" data-multi="1">
            <span class="label">${esc(q.label)}</span>
            <div class="choices">${q.options.map((o) => `
                <button type="button" data-value="${esc(o)}"
                    aria-pressed="${String(chosen.includes(o))}">${esc(o)}</button>`).join('')}</div>
        </div>`;
    }
    if (q.type === 'scale5') {
        const dots = [];
        for (let i = 1; i <= 5; i += 1) {
            dots.push(`<button type="button" data-value="${i}"
                aria-label="${i}" aria-pressed="${String(value === i)}">${i}</button>`);
        }
        return `<div class="q" data-q="${q.id}">
            <span class="label">${esc(q.label)}</span>
            <div class="scale">
                <span class="end">${esc(q.low)}</span>
                <div class="dots">${dots.join('')}</div>
                <span class="end high">${esc(q.high)}</span>
            </div>
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
                if (q.dataset.multi) {
                    const held = Array.isArray(answersFor(step)[id]) ? answersFor(step)[id] : [];
                    const next = held.includes(raw)
                        ? held.filter((x) => x !== raw) : [...held, raw];
                    answersFor(step)[id] = next;
                    b.setAttribute('aria-pressed', String(next.includes(raw)));
                    void save(step);
                    updateNext(step);
                    return;
                }
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

// ── the pilot bar ────────────────────────────────────────────────────────────
//
// Two controls, shown only to a `pilot-` code: fill this step in and move on,
// and jump straight to a step by name. Piloting the interview meant answering
// twenty-five scales and two quizzes first, so in practice the last steps were
// piloted least — which is the wrong way round, since they are the ones nobody
// has walked through before.

/** Fill a step in and record that a machine did it. */
function autofill(step) {
    const name = answerDoc(step);
    const values = defaultsFor(step);
    if (!name || !values) return false;
    state.answers[name] = { ...state.answers[name], ...values };
    void save(step);
    return true;
}

function pilotBar() {
    const bar = $('#pilot-bar');
    if (!isPilotCode(state.code)) return;
    bar.hidden = false;

    const jump = $('#pilot-jump');
    jump.replaceChildren(...state.steps.map((s, i) => {
        const o = document.createElement('option');
        o.value = String(i);
        o.textContent = `${i + 1}. ${stepName(s)}`;
        return o;
    }));

    $('#pilot-skip').addEventListener('click', () => {
        autofill(state.steps[state.at]);
        if (state.at < state.steps.length - 1) state.at += 1;
        remember();
        render();
    });

    // Jumping fills in everything skipped over, so the step landed on behaves
    // the way it would have if the session had reached it — a questionnaire that
    // reads an earlier answer gets one, and the dashboard sees a session at that
    // point rather than one that answered nothing and then answered the end.
    jump.addEventListener('change', () => {
        const to = Number(jump.value);
        for (let i = state.at; i < to; i += 1) autofill(state.steps[i]);
        state.at = to;
        remember();
        render();
    });
}

/** What a step is called in the jump menu. */
function stepName(step) {
    const which = step.n ? ` ${step.n} (${step.condition})` : '';
    if (step.kind === 'quiz') return `quiz ${step.sitting}, ${step.project}`;
    return `${step.kind}${which}`;
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

export { state, complete, VIEWS, stepName };
