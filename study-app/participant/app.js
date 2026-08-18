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
    scaleFor, MANIPULATION_CHECK, SCENARIOS, SIGNOFF, REFLECTION, TASK_CARDS,
    PROJECTS, RESPONSIBILITY, HOW_TO_START, QUIZZES, AFTER_QUIZZES,
    buildSteps, answerDoc,
} from './steps.js';
import { drawCard } from './card.js';
import { cmd, wireCopy } from './copy.js';
import {
    QUIZ_MINUTES, QUIZ_WARN_MS, QUIZ_ADVANCE_DELAY_MS, timedOutAllowsAdvance,
} from './quiz-timing.js';
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

    // Only a pilot code can reach the skip bar. The markup is hidden by default
    // and pilotBar() checks the code too; this is the third lock, because a
    // participant who saw a "Fill and skip" button would be one keystroke from
    // skipping the study.
    if (isPilotCode(state.code)) pilotBar();
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
    // Stop the question clock before the step it belongs to leaves the screen.
    // Only the quiz step arms it, and going Back off one used to leave it
    // ticking against an element that no longer existed.
    clearInterval(quizTick);
    $('#rail').style.width = `${(state.at / (state.steps.length - 1)) * 100}%`;

    const section = document.createElement('section');
    section.className = 'step';
    // The questions are not copyable. They are open book — read the description,
    // read the code, ask the agent about the PROJECT — but pasting the question
    // itself into the agent measures the agent instead of the pair. Blocking the
    // copy is not a security boundary and is not meant to be; it removes the
    // thoughtless path, which is the one people actually take.
    if (step.kind === 'quiz' || step.kind === 'reflect') section.classList.add('noselect');
    section.innerHTML = VIEWS[step.kind](step);
    stage.replaceChildren(section);
    window.scrollTo({ top: 0, behavior: 'instant' });

    if (step.kind === 'task') {
        // Light, like the rest of the page, rather than following the machine.
        // The card is a picture that goes into the screen recording, and a card
        // that is dark for one participant and light for the next is one more
        // thing that differs between sessions for no reason.
        // The card is a picture so its words cannot be selected and pasted at the
        // agent. It is drawn in the session's language like everything else.
        const card = TASK_CARDS[step.project];
        drawCard(section.querySelector('.card-wrap'), {
            title: t(`card.${step.project}.title`, card.title),
            lines: (t(`card.${step.project}.lines`, card.lines.join('\n'))).split('\n'),
            // The sample stays in the language the OUTPUT is in — it is what the
            // program prints, not prose written to be read in translation.
            example: card.example && {
                label: t(`card.${step.project}.example.label`, card.example.label),
                lines: card.example.lines,
            },
        }, { dark: false });
    }
    wire(section, step);
    wireCopy(section);
    if (step.kind === 'quiz') startQuizTimer(step);
    // The task has no clock on screen — it is not timed against them — but the
    // instant it opened is recorded, because that is where the interaction log
    // stops being a record of working the codebase out and starts being a record
    // of changing it.
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
        // Every one answered, the same rule the pre-task quiz uses: a blank is
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
    if (step.kind === 'quiz') {
        // Every one answered. A blank is indistinguishable from "I do not know",
        // and the guess is the data: which wrong option attracted somebody is
        // most of what a wrong answer tells us.
        //
        // Unless the clock ran out, which is the one case where a blank means
        // something on its own — and holding somebody on a step they are out of
        // time for would make the button, not the timer, the thing in charge.
        return timedOutAllowsAdvance({
            answered: (QUIZZES[step.project] || []).every((q) => given(`q${q.n}`)),
            timedOut: a.timedOut,
        });
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
        <p>${esc(t('ui.welcome.p', 'The researcher is on the call with you. Ask them anything at any point, and tell them if something on this page does not make sense.'))}</p>
        <div class="note">${esc(t('ui.welcome.note', 'Nothing you type here is stored with your name. Your answers are filed against a code, and only the consent form knows who you are.'))}</div>`,

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
            <a class="dl" href="${BUNDLE_URL}" download>${esc(t('ui.setup.dlbtn', 'Download (about 5 MB)'))}</a></li>
          <li>${esc(t('ui.setup.unzip', 'Unzip it. Double-click on a Mac, or'))} ${cmd('unzip codoc-study-bundle.zip')}</li>
          <li>${esc(t('ui.setup.cd', 'Open a terminal in the unzipped folder.'))}
            ${cmd('cd ~/Downloads/codoc-study-bundle')}</li>
          <li>${esc(t('ui.setup.run', 'Run this. Your code is already in it.'))}
            ${cmd(`./setup.sh ${state.code} ${state.order}`
        + (state.lang && state.lang !== 'en' ? ` ${state.lang}` : ''))}</li>
        </ol>

        <p>${esc(t('ui.setup.p', 'It prints a line for each thing it does and asks you for nothing. When it finishes it either says everything is ready or lists what is missing. Send the last few lines to the researcher either way.'))}</p>

        <div class="note">${esc(t('ui.setup.pays', 'We pay for the AI, so nothing in this study costs you anything and you do not need your own plan. The keys come down with your code, so you never paste one. Everything it sets up lives inside the project folders, including a separate assistant profile, so your own setup is untouched and deleting those folders removes all of it.'))}</div>

        <h2>${esc(t('ui.setup.dayh', 'On the day'))}</h2>
        <p>${esc(t('ui.setup.dayp', 'Run this in the same folder and read the result to the researcher.'))}</p>
        <p>${cmd('./setup.sh --check')}</p>
        <div class="note">${esc(t('ui.setup.daynote', 'If anything says fail or todo, say so now. It takes a minute to fix here and cannot be fixed afterwards.'))}</div>`,

    intro: (step) => {
        const how = HOW_TO_START[step.condition];
        return `
        <h1>${esc(step.n === 1
        ? t('ui.intro.first', 'The first way of working')
        : t('ui.intro.second', 'The second way of working'))}</h1>
        <p class="lead">${esc(t(`start.${step.condition}.title`, how.title))}.</p>
        <ol class="do">
          ${how.steps.map(([text, c], i) => `<li>${esc(t(`start.${step.condition}.step.${i}`, text))}
            ${c ? cmd(c.replace('{folder}', how.folder(step.project))) : ''}
          </li>`).join('')}
        </ol>
        <h2>${esc(t('ui.intro.whatdoc', 'What the written description is here'))}</h2>
        ${how.about.map((line, i) => `<p>${esc(t(`start.${step.condition}.about.${i}`, line))}</p>`).join('')}`;
    },

    // Everything on this page used to be a file the researcher shared on the
    // call. Reading it here means nobody leaves the page while forming their
    // first picture of the codebase, and every participant gets the same words.
    about: (step) => {
        const p = PROJECTS[step.project];
        return `
        <h1>${esc(t('ui.about.h', 'About {project}').replace('{project}', p.name))}</h1>
        <p class="lead">${esc(t(`project.${step.project}.oneLine`, p.oneLine))}</p>

        <h2>${esc(t('ui.about.problem', 'The problem'))}</h2>
        ${p.problem.map((line, i) => `<p>${esc(t(`project.${step.project}.problem.${i}`, line))}</p>`).join('')}
        <div class="ba">
          <div><span class="ba-label">${esc(t(`project.${step.project}.beforeLabel`, 'Out of the PDF'))}</span><pre class="sample">${esc(p.before)}</pre></div>
          <div><span class="ba-label">${esc(t('ui.about.after', 'After'))}</span><pre class="sample">${esc(p.after)}</pre></div>
        </div>
        <p class="fine">${esc(t(`project.${step.project}.afterNote`, p.afterNote))}</p>

        <h2>${esc(t('ui.about.does', 'What it does'))}</h2>
        <p>${esc(t('ui.about.doesp', 'You do not need to remember these. They are here so nothing in the code is a surprise.'))}</p>
        <dl class="words">${p.does.map(([w, d], i) => `
          <dt>${esc(t(`project.${step.project}.does.${i}.term`, w))}</dt>
          <dd>${esc(t(`project.${step.project}.does.${i}.body`, d))}</dd>`).join('')}
        </dl>
        <p>${esc(t(`project.${step.project}.notScope`, p.notScope))}</p>

        <h2>${esc(t('ui.about.tradeoff', 'Each rule is a tradeoff'))}</h2>
        <p class="lead">${esc(t('ui.about.tradeoffp', 'Each of the rules above chose one reasonable option over another reasonable option.'))}</p>
        <dl class="words">${p.judgement.map(([w, d], i) => `
          <dt>${esc(t(`project.${step.project}.judgement.${i}.term`, w))}</dt>
          <dd>${esc(t(`project.${step.project}.judgement.${i}.body`, d))}</dd>`).join('')}
        </dl>
        <div class="note">${esc(t('ui.about.chose',
        'The code shows you what {project} chose in each case. It does not always say why, or what the alternative would have cost.')
        .replace('{project}', p.name))}</div>

        <h2>${esc(t('ui.about.running', 'Running it'))}</h2>
        <table class="cmds"><tbody>${p.commands.map(([c, w], i) => `
          <tr><td class="mono">${cmd(c)}</td><td>${esc(t(`project.${step.project}.command.${i}`, w))}</td></tr>`).join('')}
        </tbody></table>
        <p class="fine">${esc(t(`project.${step.project}.commandNote`, p.commandNote))}</p>

        <h2>${esc(t('ui.about.files', 'The files'))}</h2>
        <p>${esc(t('ui.about.filesp', 'Nine, and small. You will probably touch two or three.'))}</p>
        <table class="cmds"><tbody>${p.layout.map(([c, w], i) => `
          <tr><td class="mono">${esc(c)}</td><td>${esc(t(`project.${step.project}.layout.${i}`, w))}</td></tr>`).join('')}
        </tbody></table>
        <p>${esc(t(`project.${step.project}.inputs`, p.inputs))}</p>

        <div class="note">${RESPONSIBILITY.map((line, i) => `<p>${esc(t(`responsibility.${i}`, line))}</p>`).join('')}</div>`;
    },

    task: () => `
        <h1>${esc(t('ui.task.h', 'Your task'))}</h1>
        <div class="card-wrap"></div>
        <p>${esc(t('ui.task.p', 'Anything the card does not say is yours to decide, and we will ask you about those decisions, so make them on purpose.'))}</p>
        <p class="note">${esc(t('ui.task.time', 'About 17 minutes. Work as you normally would.'))}</p>`,

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
        const task = (TASK_CARDS[step.project] || {}).title || '';
        const intro = {
            load: esc(t('ui.after.loadlead',
        'These six are about one thing only: the change you just made to {project}{task}. '
        + 'Not the reading beforehand, not the questions afterwards. Mark a position on each line.')
        .replace('{project}', step.project)
        .replace('{task}', task ? `, ${task}` : '')),
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

    // Open book, and timed.
    //
    // Answering "from what you have just read" measured how much of a two-minute
    // briefing somebody retained, which is not what either way of working is for.
    // Both arms let you go and find out: the baseline has CLAUDE.md, the code and
    // an agent you can ask; the codoc arm has the feature tree, the code and the
    // same agent. How quickly and how well you can find an answer IS the
    // comparison, so the questions are asked with everything open and a clock
    // running.
    //
    // The one thing barred is pasting a question into the agent, which would
    // measure the agent instead of the pair. Nothing enforces it; the researcher
    // is watching the screen, and the transcript shows it afterwards.
    //
    // No feedback either sitting: telling somebody they were wrong before the
    // task would teach them the answer.
    quiz: (step) => {
        const a = answersFor(step);
        const questions = QUIZZES[step.project] || [];
        const where = step.condition === 'codoc'
            ? t('ui.quiz.where.codoc', 'the feature tree and the code')
            : t('ui.quiz.where.baseline', 'CLAUDE.md and the code');
        return `
        <h1>${esc(step.sitting === 'before'
        ? t('ui.quiz.h.before', 'Before you start')
        : t('ui.quiz.h.after', 'A few questions'))}</h1>
        <p class="lead">${esc(t('ui.quiz.lead',
        'Five questions about {project}. You have {minutes} minutes and may look anything up. '
        + 'When the time is up the page moves on by itself.')
        .replace('{project}', step.project).replace('{minutes}', String(QUIZ_MINUTES)))}</p>
        <div class="timer" id="quiz-timer" role="timer" aria-live="off"></div>
        <div class="note">
          <p><b>${esc(t('ui.quiz.go', 'Go and find out.'))}</b>
          ${esc(t('ui.quiz.gop',
        'Read {where}, run the project, and ask the agent whatever you like. Working out where the answer lives is part of what we are looking at.')
        .replace('{where}', where))}</p>
          <p><b>${esc(t('ui.quiz.rule', 'One rule:'))}</b>
          ${esc(t('ui.quiz.rulep', 'do not paste a question or its options into the agent. Ask in your own words instead.'))}</p>
          <p>${esc(t('ui.quiz.answerall', 'Answer every one. A guess is fine and we expect several. There is no feedback, so nothing here tells you whether you were right.'))}</p>
        </div>
        ${questions.map((q) => `
          <div class="q" data-q="q${q.n}">
            <span class="label">${q.n}. ${esc(t(`quiz.${step.project}.${q.n}.question`, q.question))}</span>
            <div class="opts">${q.options.map((o) => `
              <button type="button" data-value="${esc(o.letter)}"
                aria-pressed="${String(a[`q${q.n}`] === o.letter)}">
                <span class="opt-letter">${esc(o.letter)}</span>
                <span class="opt-text">${esc(t(`quiz.${step.project}.${q.n}.option.${o.letter}`, o.text))}</span>
              </button>`).join('')}</div>
          </div>`).join('')}`;
    },

    break: () => `
        <h1>${esc(t('ui.break.h', 'Halfway'))}</h1>
        <p class="lead">${esc(t('ui.break.lead', 'Take five minutes. Stretch, get a drink, leave the call running.'))}</p>
        <p>${esc(t('ui.break.p', 'The second half is the same shape as the first: a different project, the other way of working, and the same questions afterwards.'))}</p>
        <div class="note">${esc(t('ui.break.note', 'Leave the folder you have just been working in exactly as it is. Nothing needs saving or closing, and we collect it at the end.'))}</div>`,

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
          <p>${esc(t('ui.scenarios.noright', 'There is no right answer, and "no preference" is a real one.'))}</p>
        </div>
        ${localizeAll('scenario', SCENARIOS).map((s) => scenarioRow(s, a[s.id])).join('')}`;
    },

    // Closed book, about the change they just made.
    //
    // Nothing enforces the closed book — the files are still on their machine and
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
        <div class="note">
          <p><b>${esc(t('ui.reflect.closed', 'Please answer from memory.'))}</b>
          ${esc(t('ui.reflect.closedp', 'Do not go back to the code, the description, or the agent for this part.'))}</p>
          <p>${esc(t('ui.reflect.why', 'What we are looking at is what you carried out of the task, so an answer you went and looked up would not tell us anything. Answer every one; a guess is fine.'))}</p>
        </div>
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
        <p>${esc(t('ui.interview.p', 'They will ask how the two compared, where you trusted the agent and where you did not, and whether you would use either of them for your own work. Answer however you like, and say so if you would rather not answer something.'))}</p>
        <div class="note">${esc(t('ui.interview.note', 'When you have finished talking, continue to the last step, which tells you how to send your work back.'))}</div>`,

    done: () => `
        <h1>${esc(t('ui.done.h', 'That is everything'))}</h1>
        <p class="lead">${esc(t('ui.done.lead', 'Thank you. One last thing: run this and send the file it makes to the researcher.'))}</p>
        <p>${cmd(`./collect.sh ${state.code}`)}</p>
        <p>${esc(t('ui.done.p', 'Once they confirm it arrived, you can delete the {folder} folder and the extensions.').replace('{folder}', '~/codoc-study'))}</p>`,
};

// ── the question clock ───────────────────────────────────────────────────────
//
// Started when the step is first shown and stored with the answers, so how long
// somebody took to find twelve answers is itself a result: the same questions,
// the same clock, one way of working each. It survives a reload, because the
// start time is stored rather than the remaining time.

let quizTick;

function startQuizTimer(step) {
    const el = $('#quiz-timer');
    if (!el) return;
    const a = answersFor(step);
    if (!a.startedAt) { a.startedAt = Date.now(); void save(step); }

    const draw = () => {
        // A step's markup is replaced wholesale, so this element can outlive the
        // screen it was drawn on. Stopping when it is no longer in the document
        // bounds the clock to its own step however the step was left.
        if (!el.isConnected) { clearInterval(quizTick); return; }
        const left = a.startedAt + QUIZ_MINUTES * 60_000 - Date.now();
        if (left <= 0) {
            clearInterval(quizTick);
            el.textContent = t('ui.quiz.timeup', 'Time is up.');
            el.classList.add('out');
            // The clock RUNS OUT — it does not merely say so. Letting somebody
            // carry on past it meant the sitting was not timed at all, and how
            // long they took is half of what this measures: a score reached in
            // fourteen minutes is not the same result as the same score in ten.
            // Whatever is answered at this instant is the answer.
            timeUp(step, a);
            return;
        }
        const total = Math.round(left / 1000);
        const clock = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
        el.textContent = t('ui.quiz.timeleft', '{time} left').replace('{time}', clock);
        // Warn before it happens rather than after. Being moved on mid-click with
        // no notice reads as the page malfunctioning; thirty seconds is enough to
        // put an answer down on the question in hand.
        el.classList.toggle('soon', left <= QUIZ_WARN_MS);
    };

    clearInterval(quizTick);
    draw();
    quizTick = setInterval(draw, 1000);
    // A repainting clock is never a reason to keep a process alive. In a browser
    // setInterval returns a number and this does nothing; under the test runner
    // it returns a handle, and without this the page's own clock kept the run
    // from ever exiting.
    quizTick?.unref?.();
}

/**
 * The clock reached zero: stamp it and move on.
 *
 * `timedOut` is recorded so the analysis can tell an unanswered question that RAN
 * OUT from one somebody chose to leave — they are different findings, and without
 * the flag both arrive as the same blank.
 */
function timeUp(step, answers) {
    if (answers.timedOut) return;          // already handled (a re-render re-armed the clock)
    answers.timedOut = true;
    answers.finishedAt = Date.now();
    if (answers.startedAt) answers.elapsedMs = answers.finishedAt - answers.startedAt;
    void save(step);
    // A beat before the page changes under them, so the last thing they see is
    // the clock reaching zero rather than a different screen arriving unexplained.
    setTimeout(() => {
        if (state.steps[state.at] !== step) return;   // they already moved on themselves
        advance();
    }, QUIZ_ADVANCE_DELAY_MS)?.unref?.();
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
    // question is one of the reasons — four of the six read alike without their
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

/** Move to the next step, stamping anything the step owes on the way out. */
function advance() {
    const step = state.steps[state.at];
    // How long twelve answers took, with everything open, is one of the numbers
    // the two ways of working are compared on. Stamped on the way out rather
    // than computed later, because nothing else records when they left.
    if (step.kind === 'quiz') {
        const a = answersFor(step);
        a.finishedAt = Date.now();
        if (a.startedAt) a.elapsedMs = a.finishedAt - a.startedAt;
        clearInterval(quizTick);
    }
    // The task's clock, for the same reason as the quiz's: it is the only record
    // of when the change stage began and ended, and the interaction log is cut on
    // those two instants to tell comprehending from implementing.
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
