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
    scaleFor, MANIPULATION_CHECK, SCENARIOS, SIGNOFF, REFLECTION,
    PROJECTS, TASK, HOW_TO_START, TUTORIAL, AFTER_QUIZZES,
    buildSteps, answerDoc,
} from './steps.js';
import { cmd, block, wireCopy } from './copy.js';
import { defaultsFor } from './autofill.js';
import { isPilotCode } from '../shared/schema.js';
import { setLanguage, language, t, localize, localizeAll } from './i18n/index.js';
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
    lang: 'en',
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
        state.online = true;
    } catch {
        state.online = false;
    }
    // The participant document is DENIED to a participant by the rules (only an
    // experimenter previewing a page can read it), so this read failing is the
    // normal case and says nothing about being online. It must not share the
    // try above: when it did, the guaranteed permission-denied marked every
    // participant's page offline, and every browser-side answer, prestudy,
    // quizzes, questionnaires, task timings, stayed in localStorage and never
    // reached the study.
    try {
        const snap = await getDoc(doc(db, `participants/${state.code}`));
        participant = snap.exists() ? snap.data() : null;
    } catch { /* expected for a participant; the link carries the order */ }

    // The participant document is not readable by the participant, by design, so
    // the order comes from the link. Falling back keeps the page usable if the
    // link is bare.
    state.order = params.get('order') || (participant && participant.order) || 'codoc-first';
    // The whole session runs in one language: this page, the questions, the task
    // cards, both descriptions and both workspaces. It comes off the link the way
    // the order does, because the participant document is deliberately not
    // readable by the participant.
    state.lang = setLanguage(params.get('lang') || (participant && participant.lang));
    document.documentElement.lang = state.lang;
    state.steps = buildSteps(state.order);

    const saved = recall();
    state.at = Math.min(saved.at || 0, state.steps.length - 1);
    state.answers = saved.answers || {};

    // Upload everything this browser already holds. This is both the recovery
    // path for a session that ran while saving was broken (the answers are all
    // still in localStorage, reopening the same link on the same browser sends
    // them), and the catch-up for one that genuinely lost its connection for a
    // while. Merge-writes of the same values are idempotent, so re-sending on
    // every load costs nothing.
    if (state.online) void flushAnswers();

    // Only a pilot code can reach the skip bar. The markup is hidden by default
    // and pilotBar() checks the code too; this is the third lock, because a
    // participant who saw a "Fill and skip" button would be one keystroke from
    // skipping the study.
    if (isPilotCode(state.code)) pilotBar();
    render();
}

/** Send every remembered answer document to the server, one merge each. */
async function flushAnswers() {
    for (const [name, values] of Object.entries(state.answers)) {
        if (!values || !Object.keys(values).length) continue;
        try {
            await setDoc(doc(db, `participants/${state.code}/answers/${name}`), {
                ...values, updatedAt: Date.now(),
            }, { merge: true });
        } catch {
            // One failure means the rest would fail the same way; the per-step
            // save() will try again as the session goes on.
            return;
        }
    }
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
    if (!state.online) { flash(t('ui.saved.local', 'saved on this computer')); return; }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        try {
            await setDoc(doc(db, `participants/${state.code}/answers/${name}`), {
                ...state.answers[name], updatedAt: Date.now(),
            }, { merge: true });
            flash(t('ui.saved', 'saved'));
        } catch {
            state.online = false;
            flash(t('ui.saved.local', 'saved on this computer'));
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
    // The closed book questions are not copyable. Blocking it is not a security
    // boundary and is not meant to be; it removes the thoughtless path, which is
    // the one people actually take.
    if (step.kind === 'reflect') section.classList.add('noselect');
    section.innerHTML = VIEWS[step.kind](step);
    stage.replaceChildren(section);
    window.scrollTo({ top: 0, behavior: 'instant' });

    wire(section, step);
    wireCopy(section);
    // The task has no clock on screen: the researcher is on the call and calls
    // time. The instant it opened is recorded anyway, because it is where the
    // interaction log stops being a record of reading and starts being a record
    // of reviewing.
    if (step.kind === 'task') {
        const a = answersFor(step);
        if (!a.startedAt) { a.startedAt = Date.now(); void save(step); }
    }

    $('#back').hidden = state.at === 0;
    $('#next').hidden = step.kind === 'done';
    $('#next').textContent = step.kind === 'task'
        ? t('ui.next.task', 'I have finished the task')
        : t('ui.next', 'Continue');
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
    if (step.kind === 'reflect') {
        // Every one answered. A blank is
        // indistinguishable from "I do not know", and which wrong option drew
        // somebody is most of what a wrong answer tells us.
        return (AFTER_QUIZZES[step.project] || []).every((q) => given(`a${q.n}`))
            && given('recall');
    }
    if (step.kind === 'signoff') {
        // The free-text one is optional; "no" is a real answer and a required
        // box buys blank characters typed to get past the button.
        return ['correct', 'confidence'].every((id) => given(id))
            && (a.grounds || []).length > 0;
    }
    return true;
}

function updateNext(step) {
    $('#next').disabled = !complete(step);
}

const VIEWS = {
    welcome: () => `
        <h1>${esc(t('ui.welcome.h', 'Thanks for taking part'))}</h1>
        <p class="lead">${esc(t('ui.welcome.lead', 'This page walks you through the session. It saves as you go, so if anything closes you can open the same link and carry on.'))}</p>
        <p>${esc(t('ui.welcome.p', 'The researcher is on the call with you. Ask them anything at any point, and tell them if something on this page does not make sense.'))}</p>`,

    consent: () => `
        <h1>${esc(t('ui.consent.h', 'Consent'))}</h1>
        <p>${esc(t('ui.consent.p', 'Please read this and fill it in. It opens in the form below, and your answers go to the research team rather than into this page.'))}</p>
        <iframe src="${CONSENT_FORM}" height="900" title="Consent form"></iframe>
        <p>${esc(t('ui.consent.after', 'When you have submitted it, continue.'))}</p>`,

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
        <h1>${esc(t('ui.prestudy.h', 'A few questions about you'))}</h1>
        <p class="lead">${esc(t('ui.prestudy.lead', 'So we can describe who took part. There are no right answers, and you can skip anything marked optional.'))}</p>
        ${localizeAll('prestudy', shown).map((q) => field(q, a[q.id])).join('')}`;
    },

    // The download is here rather than in an email. A file sent separately from
    // the link is a file that can be the wrong version, and nothing on either
    // side says so.
    setup: () => `
        <h1>${esc(t('ui.setup.h', 'Set up your machine'))}</h1>
        <p class="lead">${esc(t('ui.setup.lead', 'Four steps, about ten minutes, mostly waiting. Do this a few days before the session rather than on the day.'))}</p>

        <ol class="do">
          <li>${esc(t('ui.setup.dl', 'Download the study folder.'))}
            <a class="dl" href="${BUNDLE_URL}" download>${esc(t('ui.setup.dlbtn', 'Download (about 9 MB)'))}</a></li>
          <li>${esc(t('ui.setup.unzip', 'Unzip it. Double-click on a Mac, or'))} ${cmd('unzip codoc-study-bundle.zip')}</li>
          <li>${esc(t('ui.setup.cd', 'Open a terminal in the unzipped folder.'))}
            ${cmd('cd ~/Downloads/codoc-study-bundle')}</li>
          <li>${esc(t('ui.setup.run', 'Run this. Your code is already in it.'))}
            ${cmd(`./setup.sh ${state.code} ${state.order}`
        + (state.lang && state.lang !== 'en' ? ` ${state.lang}` : ''))}</li>
        </ol>

        <p>${esc(t('ui.setup.p', 'It prints a line for each thing it does and asks you for nothing. When it finishes it either says everything is ready or lists what is missing. Send the last few lines to the researcher either way.'))}</p>


        <h2>${esc(t('ui.setup.dayh', 'On the day'))}</h2>
        <p>${esc(t('ui.setup.dayp', 'Run this in the same folder and read the result to the researcher.'))}</p>
        <p>${cmd('./setup.sh --check')}</p>`,

    intro: (step) => {
        const how = HOW_TO_START[step.condition];
        return `
        <h1>${esc(step.n === 1
        ? t('ui.intro.first', 'The first way of working')
        : t('ui.intro.second', 'The second way of working'))}</h1>
        <p class="lead">${esc(t('ui.intro.shape',
        'Get the project open, read about it, then look at a change your coding agent '
        + 'made to it and decide what to keep.'))}</p>
        <ol class="do">
          ${how.steps.map(([text, c], i) => `<li>${esc(t(`start.${step.condition}.step.${i}`, text))}
            ${c ? cmd(c.replace('{folder}', how.folder(step.project))) : ''}
          </li>`).join('')}
        </ol>`;
    },

    // The project, in five minutes, led by pictures.
    //
    // What it replaced ran to nine source files, six rules with a second
    // sentence each explaining that another choice was possible, and a heading
    // that told the reader every rule was a tradeoff. None of that survives a
    // five minute budget, and a participant who spends it reading is one who
    // meets the change without a picture of the project.
    about: (step) => {
        const p = PROJECTS[step.project];
        return `
        <h1>${esc(t('ui.about.h', 'About {project}').replace('{project}', p.name))}</h1>
        <p class="lead">${esc(t(`project.${step.project}.oneLine`, p.oneLine))}</p>

        ${p.why.map((line, i) => `<p>${esc(t(`project.${step.project}.why.${i}`, line))}</p>`).join('')}

        <div class="ba">
          <div><span class="ba-label">${esc(t(`project.${step.project}.worked.inputLabel`, p.worked.inputLabel))}</span>
               <pre class="sample">${esc(p.worked.input)}</pre></div>
          <div><span class="ba-label">${esc(t(`project.${step.project}.worked.outputLabel`, p.worked.outputLabel))}</span>
               <pre class="sample">${esc(p.worked.output)}</pre></div>
        </div>
        <p class="fine">${esc(t(`project.${step.project}.worked.caption`, p.worked.caption))}</p>

        <h2>${esc(t('ui.about.rules', 'What it does'))}</h2>
        <dl class="words">${p.rules.map((r, i) => `
          <dt>${esc(t(`project.${step.project}.rule.${i}.name`, r.name))}</dt>
          <dd>${esc(t(`project.${step.project}.rule.${i}.what`, r.what))}</dd>`).join('')}
        </dl>
        <p>${esc(t(`project.${step.project}.limits`, p.limits))}</p>

        <h2>${esc(t('ui.about.running', 'Running it'))}</h2>
        <table class="cmds"><tbody>${p.commands.map(([c, w], i) => `
          <tr><td class="mono">${cmd(c)}</td><td>${esc(t(`project.${step.project}.command.${i}`, w))}</td></tr>`).join('')}
        </tbody></table>`;
    },

    // The way of working, in five minutes, as a tutorial rather than as four
    // sentences somebody reads once and cannot use.
    //
    // Both conditions get one of the same length. A page that is longer in one
    // arm teaches more in that arm, and then the comparison is between two
    // pages rather than between two tools.
    system: (step) => {
        const g = TUTORIAL[step.condition];
        const key = `tutorial.${step.condition}`;
        return `
        <h1>${esc(t(`${key}.title`, g.title))}</h1>
        <p class="lead">${esc(t(`${key}.lead`, g.lead))}</p>

        ${figure(g.hero, `${key}.hero`, 'hero')}

        <dl class="words parts">${g.parts.map(([name, what], i) => `
          <dt>${esc(t(`${key}.part.${i}.name`, name))}</dt>
          <dd>${esc(t(`${key}.part.${i}.what`, what))}</dd>`).join('')}
        </dl>

        ${g.marks.length ? `<p class="marks">${g.marks.map(([colour, means], i) => `
          <span class="mark ${esc(colour.toLowerCase())}">${esc(t(`${key}.mark.${i}.colour`, colour))}</span>
          ${esc(t(`${key}.mark.${i}.means`, means))}`).join('')}</p>` : ''}

        <ol class="tour">${g.steps.map((s, i) => `
          <li>
            <h2>${esc(t(`${key}.step.${i}.title`, s.title))}</h2>
            <ul>${s.points.map((point, j) => `
              <li>${esc(t(`${key}.step.${i}.point.${j}`, point))}</li>`).join('')}
            </ul>
            ${figure(s.figure, `${key}.step.${i}.figure`)}
          </li>`).join('')}
        </ol>`;
    },

    // The request, and the decision.
    //
    // It reads as one occasion: this is what the project gets wrong, this is
    // what you are going to ask for, here is the request, send it. The page used
    // to open on a memory the participant did not have, of asking for something
    // earlier and going out, and the card was a request with no author. Sending
    // it themselves is what makes the change theirs to own.
    //
    // Nothing here says the change is wrong, and nothing says what to check.
    // Either would hand over the thing being measured.
    task: (step) => {
        const p = PROJECTS[step.project];
        return `
        <h1>${esc(t('ui.task.h', 'Your task'))}</h1>

        <h2>${esc(t('ui.task.problem', 'One thing {project} gets wrong')
        .replace('{project}', step.project))}</h2>
        <p>${esc(t(`project.${step.project}.failure.lead`, p.failure.lead))}</p>
        <div class="ba stack">
          <div><span class="ba-label">${esc(t('ui.task.in', 'In'))}</span>
               <pre class="sample">${esc(p.failure.input)}</pre></div>
          <div><span class="ba-label">${esc(t('ui.task.out', 'Out'))}</span>
               <pre class="sample bad">${esc(p.failure.output)}</pre></div>
        </div>
        <p class="fine">${esc(t(`project.${step.project}.failure.caption`, p.failure.caption))}</p>

        <h2>${esc(t('ui.task.ask', 'What you are asking for'))}</h2>
        <ul class="asks">${p.ask.map((line, i) => `
          <li>${esc(t(`project.${step.project}.ask.${i}`, line))}</li>`).join('')}
        </ul>

        <h2>${esc(t('ui.task.send', 'Send it'))}</h2>
        <p>${esc(t('ui.task.lead', TASK.lead))}</p>
        <ol class="do">
          <li>${esc(t('ui.task.send.1', 'Start your coding agent in a terminal.'))}
            ${cmd('./start-session')}</li>
          <li>${esc(t('ui.task.send.2', 'Copy this and paste it in.'))}
            ${block(p.prompt, t('ui.task.copy', 'Copy'))}</li>
          <li>${esc(t('ui.task.send.3', 'Watch it work. It takes a few minutes.'))}</li>
          <li>${esc(t('ui.task.send.4', 'To talk to it again later, run this in the same terminal.'))}
            ${cmd('./claude-study')}</li>
        </ol>

        <h2>${esc(t('ui.task.then', 'What happens then'))}</h2>
        <ol class="do">
          <li>${esc(t('ui.task.stage1', TASK.stage1))}</li>
          <li>${esc(t('ui.task.stage2', TASK.stage2))}</li>
          <li>${esc(t('ui.task.stage3', TASK.stage3))}</li>
        </ol>

        <h2>${esc(t('ui.task.check', 'Seeing what it did'))}</h2>
        <p>${esc(t(`project.${step.project}.repl.lead`, p.repl.lead))}</p>
        ${cmd(p.repl.command)}
        <pre class="sample">${esc(p.repl.before)}</pre>
        <p class="fine">${esc(t(`project.${step.project}.repl.caption`, p.repl.caption))}</p>

        <p class="rule">${esc(t('ui.task.clock',
        'Twenty minutes in all. There is no clock on this page. The researcher will '
        + 'tell you when time is nearly up.'))}</p>`;
    },

    // Grouped by what each block measures, with the groups named. An unbroken
    // column of twenty-five identical rows is answered by pattern rather than by
    // reading, and the headings are the cheapest thing that stops that.
    questionnaire: (step) => {
        const a = answersFor(step);
        // TLX measures the workload of a task, not of a sitting. Asked about
        // "the session" people rate the whole hour, the six subscales stop
        // separating, and the number is no longer the one other papers report.
        // So the workload block names the task, and only the workload block:
        // usability and the rest are about the way of working, which is the
        // thing the study compares.
        const intro = {
            load: esc(t('ui.after.loadlead',
        'These six are about one thing only: reviewing the change to {project}. '
        + 'Not the reading beforehand, not the questions afterwards. Mark a position on each line.')
        .replace('{project}', step.project)),
        };
        return `
        <h1>${esc(t('ui.after.h', 'How that felt'))}</h1>
        <p class="lead">${esc(t('ui.after.lead', 'About the way of working you just used. Answer quickly; your first reaction is the useful one.'))}</p>
        ${CONSTRUCTS.map((c) => {
            const items = AFTER_CONDITION.filter((q) => q.c === c.id);
            if (!items.length) return '';
            return `<section class="block">
              <h2>${esc(t(`block.${c.id}.title`, c.title))}</h2>
              ${intro[c.id] ? `<p class="blocklead">${intro[c.id]}</p>` : ''}
              ${localizeAll('after', items).map((q) => scaleRow(q, a[q.id])).join('')}
            </section>`;
        }).join('')}
        <section class="block">
          <h2>${esc(t('ui.after.own', 'In your own words'))}</h2>
          ${localizeAll('check', MANIPULATION_CHECK).map((q) => field(q, a[q.id])).join('')}
        </section>`;
    },

    break: () => `
        <h1>${esc(t('ui.break.h', 'Halfway'))}</h1>
        <p class="lead">${esc(t('ui.break.lead', 'Take five minutes. Stretch, get a drink, leave the call running.'))}</p>
        <p>${esc(t('ui.break.p', 'The second half is the same shape as the first: a different project, the other way of working, and the same questions afterwards.'))}</p>`,

    // Asked once, with both conditions behind them. Which way of working somebody
    // would pick depends heavily on the job, and asking it as one flat question
    // gets an answer about whichever job they happened to picture.
    scenarios: (step) => {
        const a = answersFor(step);
        // Named by what they did in each, not "the first" and "the second". Two
        // hours and a break separate somebody from their first condition, and an
        // answer to a question they had to reconstruct first is worth less than
        // one they could just give.
        const first = state.steps.find((s) => s.kind === 'task' && s.n === 1) || {};
        const second = state.steps.find((s) => s.kind === 'task' && s.n === 2) || {};
        const name = (s) => esc((s.condition === 'codoc'
            ? t('ui.scenarios.was.codoc', 'was the {project} one, with the feature tree')
            : t('ui.scenarios.was.baseline', 'was the {project} one, with CLAUDE.md'))
            .replace('{project}', s.project));
        return `
        <h1>${esc(t('ui.scenarios.h', 'Which one, for which kind of work'))}</h1>
        <p class="lead">${esc(t('ui.scenarios.lead', 'Imagine doing each of these at your own job, in your own codebase. Which of the two ways of working would you rather have?'))}</p>
        <div class="note">
          <p><b>${esc(t('ui.scenarios.first', 'The first one'))}</b> ${name(first)}.<br>
             <b>${esc(t('ui.scenarios.second', 'The second one'))}</b> ${name(second)}.</p>
        </div>
        ${localizeAll('scenario', SCENARIOS).map((s) => scenarioRow(s, a[s.id])).join('')}`;
    },

    // Closed book, about the change they just made.
    //
    // Nothing enforces the closed book, the files are still on their machine and
    // the researcher is watching the screen. Saying so plainly is what makes it
    // work, and it is said as a reason rather than as a rule, because somebody
    // who understands why will not go and look.
    reflect: (step) => {
        const a = answersFor(step);
        const questions = AFTER_QUIZZES[step.project] || [];
        return `
        <h1>${esc(t('ui.reflect.h', 'About what you just did'))}</h1>
        <p class="lead">${esc(t('ui.reflect.lead',
        'Five questions about the change you just made to {project}.')
        .replace('{project}', step.project))}</p>
        <p class="rule"><b>${esc(t('ui.reflect.closed', 'Answer from memory.'))}</b>
          ${esc(t('ui.reflect.closedp', 'Do not go back to the code, the description, or the agent. A guess is fine.'))}</p>
        ${questions.map((q) => `
          <div class="q" data-q="a${q.n}">
            <span class="label">${q.n}. ${esc(t(`after.${step.project}.${q.n}.question`, q.question))}</span>
            <div class="opts">${q.options.map((o) => `
              <button type="button" data-value="${esc(o.letter)}"
                aria-pressed="${String(a[`a${q.n}`] === o.letter)}">
                <span class="opt-letter">${esc(o.letter)}</span>
                <span class="opt-text">${esc(t(`after.${step.project}.${q.n}.option.${o.letter}`, o.text))}</span>
              </button>`).join('')}</div>
          </div>`).join('')}
        ${localizeAll('reflect', REFLECTION).map((q) => field(q, a[q.id])).join('')}`;
    },

    // Their own sign-off, in their own words. It used to be typed into the
    // dashboard while they spoke, which made it a record of how well somebody
    // explained themselves and how fast the researcher could type.
    signoff: (step) => `
        <h1>${esc(t('ui.signoff.h', 'Before you move on'))}</h1>
        <p class="lead">${esc(t('ui.signoff.lead', 'About the change you just made. There is no right answer and nobody is marking it.'))}</p>
        ${localizeAll('signoff', SIGNOFF).map((q) => field(q, answersFor(step)[q.id])).join('')}`,

    // Spoken, not typed. Asking these on the page got short written answers to
    // questions whose value is in the follow-up, and it asked somebody to write
    // for fifteen minutes at the end of two hours. The researcher has the same
    // questions in the dashboard and asks them on the call.
    interview: () => `
        <h1>${esc(t('ui.interview.h', 'Last part'))}</h1>
        <p class="lead">${esc(t('ui.interview.lead', 'Nothing to fill in here. The researcher will talk this part through with you.'))}</p>
        <p>${esc(t('ui.interview.p', 'They will ask how the two compared, where you trusted the agent and where you did not, and whether you would use either of them for your own work. Answer however you like, and say so if you would rather not answer something.'))}</p>`,

    done: () => `
        <h1>${esc(t('ui.done.h', 'That is everything'))}</h1>
        <p class="lead">${esc(t('ui.done.lead', 'Thank you. One last thing: run this and send the file it makes to the researcher.'))}</p>
        <p>${cmd(`./collect.sh ${state.code}`)}</p>
        <p>${esc(t('ui.done.p', 'Once they confirm it arrived, you can delete the {folder} folder and the extensions.').replace('{folder}', '~/codoc-study'))}</p>`,
};

/**
 * A figure in the tutorial.
 *
 * A figure with a `src` is a picture. A figure with a `todo` is a space with a
 * line saying what belongs in it, so a screenshot that has not been taken yet is
 * a visible gap while the page is being built rather than a broken image in
 * front of somebody. Both draw at the same width, so adding the picture later
 * does not move the page around.
 */
function figure(fig, key, extra = '') {
    if (!fig) return '';
    const caption = fig.caption
        ? `<figcaption>${esc(t(`${key}.caption`, fig.caption))}</figcaption>` : '';
    if (fig.src) {
        return `<figure class="shot ${extra}">
            <img src="${esc(fig.src)}" alt="${esc(t(`${key}.alt`, fig.alt || ''))}">
            ${caption}</figure>`;
    }
    return `<figure class="shot todo ${extra}">
        <div class="holder">${esc(t(`${key}.todo`, fig.todo || ''))}</div>
        ${caption}</figure>`;
}

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
    // disagree-to-agree, and one of its items runs failure-to-perfect. One
    // shared scale would have relabelled six items and mislabelled one.
    const s = scaleFor(q);
    const step = s.step || 1;
    const marks = [];
    for (let i = s.min; i <= s.max; i += step) {
        marks.push(`<button type="button" data-value="${i}"
            aria-label="${i}" aria-pressed="${String(value === i)}">${i}</button>`);
    }
    // Twenty-one numbered circles do not fit on a line and would not be read if
    // they did. TLX's own form is a row of tick marks you mark a position on,
    // so that is what the wide scale draws: same points, same click target,
    // numbers left to the ends where the words are.
    const wide = marks.length > 9;
    // The subscale's name and its full definition, for the block that has them.
    // Lee et al. found the six TLX subscales correlate far more strongly in HCI
    // studies than in the original validation, and that showing only the short
    // question is one of the reasons, four of the six read alike without their
    // definitions.
    const head = q.title
        ? `<span class="label"><b>${esc(q.title)}</b> — ${esc(q.text)}</span>
           <span class="define">${esc(q.description || '')}</span>`
        : `<span class="label">${esc(q.text)}</span>`;
    return `<div class="q" data-q="${q.id}">
        ${head}
        <div class="scale${wide ? ' wide' : ''}">
            <span class="end">${esc(s.lowLabel)}</span>
            <div class="dots">${marks.join('')}</div>
            <span class="end high">${esc(s.highLabel)}</span>
        </div>
    </div>`;
}

function scenarioRow(s, value) {
    // The stored value stays English whatever is on screen, so one analysis reads
    // every participant. Only the label moves.
    const options = [
        ['The first one', t('scenario.option.first', 'The first one')],
        ['The second one', t('scenario.option.second', 'The second one')],
        ['No preference', t('scenario.option.none', 'No preference')],
    ];
    return `<div class="q" data-q="${s.id}">
        <span class="label">${esc(s.text)}</span>
        <div class="choices">${options.map(([stored, shown]) => `
            <button type="button" data-value="${esc(stored)}"
                aria-pressed="${String(value === stored)}">${esc(shown)}</button>`).join('')}</div>
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
// twenty-five scales and two question rounds first, so in practice the last steps were
// piloted least, which is the wrong way round, since they are the ones nobody
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
    // the way it would have if the session had reached it, a questionnaire that
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
    return `${step.kind}${which}`;
}

// ── moving ───────────────────────────────────────────────────────────────────

/** Move to the next step, stamping anything the step owes on the way out. */
function advance() {
    const step = state.steps[state.at];
    // The task's clock. It is the only record of when the review began and
    // ended, and the interaction log is cut on those two instants.
    if (step.kind === 'task') {
        const a = answersFor(step);
        a.finishedAt = Date.now();
        if (a.startedAt) a.elapsedMs = a.finishedAt - a.startedAt;
    }
    void save(step);
    if (state.at < state.steps.length - 1) { state.at += 1; remember(); render(); }
}

$('#next').addEventListener('click', () => {
    if (!complete(state.steps[state.at])) return;
    advance();
});

$('#back').addEventListener('click', () => {
    if (state.at > 0) { state.at -= 1; remember(); render(); }
});

void start();

export { state, complete, VIEWS, stepName };
