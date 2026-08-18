// Pulls the quizzes out of the projects' STUDY.md files.
//
// Those files are the instrument. They are what gets frozen at pre-registration,
// so they stay the one source of truth and this reads them rather than the
// dashboard keeping a second copy that could drift apart from them.
//
//   node scripts/extract-questions.mjs
//   → experimenter/questions.json
//
// The format it reads, from the "## The quiz" section onward:
//
//   ### Purpose — what this program is for
//
//   **Q1. What is scribe for?**
//   - a) Reading PDF files
//   - b) **Turning it into readable Markdown** ✓
//   - c) Converting Markdown into PDF
//   - d) Checking the text layer is complete
//
// A tick marks the right answer. Exactly one per question, or this refuses.
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const PROJECTS = join(here, '..', '..', 'docs', 'study-materials', 'projects');

// The band is the first word or phrase of the heading, up to whatever separates
// it from its gloss. Both separators are accepted: the headings were written with
// an em dash and later rewritten with a colon, and reading only the dash form
// meant every question landed in a band called "Purpose: what it is for, and
// where it stops". It refuses rather than writes when a band comes out empty, so
// that showed up as a refusal rather than as a silently unscoreable quiz.
const BAND = /^###\s+(.+?)(?:\s*[—:].*)?$/;
const QUESTION = /^\*\*Q(\d+)\.\s*(.+?)\*\*\s*$/;
// A difficulty tag at the front of the question, which is recorded and then
// REMOVED from what anybody is shown. A participant who reads "(hard)" answers
// differently from one who does not, and the tag exists to check the spread of
// the instrument, not to tell them how much to worry.
const DIFFICULTY = /^\((easy|medium|hard)\)\s*/;
const OPTION = /^-\s*([a-d])\)\s*(.+?)\s*$/;
const CORRECT = /\s*✓\s*$/;

export function parseQuiz(markdown) {
    return parseSection(markdown, '## The quiz');
}

/** The closed-book set asked straight after the task, about the change they made. */
export function parseAfter(markdown) {
    return parseSection(markdown, '## The after-task questions');
}

function parseSection(markdown, heading) {
    const start = markdown.indexOf(heading);
    if (start < 0) return [];
    // Stop at the next second-level heading, so anything after the quiz — the
    // matching notes, for instance — cannot be read as a question.
    const rest = markdown.slice(start + 1);
    const end = rest.indexOf('\n## ');
    const body = end < 0 ? rest : rest.slice(0, end);

    const questions = [];
    let band = '';
    let current = null;

    const finish = () => {
        if (current) questions.push(current);
        current = null;
    };

    for (const line of body.split('\n')) {
        const bandMatch = BAND.exec(line.trim());
        if (bandMatch) {
            finish();
            band = bandMatch[1].trim();
            continue;
        }
        const questionMatch = QUESTION.exec(line.trim());
        if (questionMatch) {
            finish();
            let text = questionMatch[2].trim();
            const tag = DIFFICULTY.exec(text);
            if (tag) text = text.slice(tag[0].length);
            current = {
                n: Number(questionMatch[1]),
                band,
                difficulty: tag ? tag[1] : '',
                question: text,
                options: [],
                answer: null,
            };
            continue;
        }
        const optionMatch = OPTION.exec(line.trim());
        if (optionMatch && current) {
            const [, letter, raw] = optionMatch;
            const isCorrect = CORRECT.test(raw);
            const text = raw.replace(CORRECT, '').replace(/^\*\*(.*)\*\*$/, '$1').trim();
            current.options.push({ letter, text });
            if (isCorrect) current.answer = letter;
        }
    }
    finish();
    // By number, not by position in STUDY.md. The instrument groups questions by
    // band (Purpose, Rationale, …), so the source order is not monotonic in `n` —
    // and the participant, who sees the numbers but not the bands, would read
    // "1, 2, 4, 3, 5" as a broken page. `n` stays each question's stable identity
    // (it keys scoring); only the reading order is normalised. Sorting here, in
    // the single parser, keeps every consumer — the generated files, the live
    // dashboard parse, the tests — in one order.
    return questions.sort((x, y) => x.n - y.n);
}

function check(project, questions, opts = {}) {
    const problems = [];
    if (!questions.length) problems.push('no questions found');
    for (const q of questions) {
        if (q.options.length !== 4) {
            problems.push(`Q${q.n} has ${q.options.length} options, not 4`);
        }
        if (!q.answer) {
            problems.push(`Q${q.n} has no answer marked; put a tick on the right one`);
        }
        if (!q.band) problems.push(`Q${q.n} is in no band`);
    }
    // The bands are what RQ1 is answered in. A quiz missing one of them cannot
    // speak to the part of the question that band covers.
    const bands = new Set(questions.map((q) => q.band));
    for (const needed of ['Purpose', 'Rationale', 'Change', 'Extension']) {
        if (!bands.has(needed)) problems.push(`no questions in the ${needed} band`);
    }
    const numbers = questions.map((q) => q.n);
    if (new Set(numbers).size !== numbers.length) problems.push('two questions share a number');

    // Every question carries a difficulty, and the spread is checked here.
    //
    // A quiz where everything is hard measures who already knew the domain. One
    // where everything is easy separates nobody. The tag never reaches the
    // participant — it is stripped from the text and left out of the browser
    // copy — and it exists so this can refuse a set that has drifted to one end.
    for (const q of questions) {
        if (!q.difficulty) problems.push(`Q${q.n} has no (easy|medium|hard) tag`);
    }
    // One of each level, not three. The set is five questions now: three of each
    // would need nine. What this still refuses is a set that has drifted to ONE
    // end — all-hard measures who already knew the domain, which is what the
    // earliest draft did — and that refusal is what the rule is for.
    const least = opts.leastPerLevel ?? 1;
    for (const level of ['easy', 'medium', 'hard']) {
        const n = questions.filter((q) => q.difficulty === level).length;
        if (n < least) problems.push(`only ${n} ${level} questions; a spread needs at least ${least}`);
    }
    return problems;
}

/**
 * The two projects have to be the same instrument in a different domain.
 *
 * Each participant does one project each way, so a difference in difficulty
 * between the projects lands entirely on whichever condition happened to get the
 * harder one. Band for band and level for level, or the counterbalancing does
 * not cancel it.
 */
function compare(all) {
    const shape = (questions) => questions
        .map((q) => `${q.band}/${q.difficulty}`).sort().join(',');
    const [a, b] = Object.keys(all);
    if (shape(all[a]) !== shape(all[b])) {
        return [`${a} and ${b} do not match band for band and level for level:`,
            `  ${a}: ${shape(all[a])}`, `  ${b}: ${shape(all[b])}`];
    }
    return [];
}

function main() {
    const out = {};
    const afterOut = {};
    let failed = false;

    for (const project of ['scribe', 'tally']) {
        const path = join(PROJECTS, project, 'STUDY.md');
        let markdown;
        try {
            markdown = readFileSync(path, 'utf8');
        } catch {
            console.error(`no STUDY.md for ${project}`);
            failed = true;
            continue;
        }
        const questions = parseQuiz(markdown);
        const after = parseAfter(markdown);
        // Both sets are five, so neither can carry three of each level.
        const problems = [
            ...check(project, questions),
            ...check(project, after, { leastPerLevel: 1 }).map((p) => `after-task: ${p}`),
        ];
        if (problems.length) {
            failed = true;
            console.error(`${project}:`);
            for (const problem of problems) console.error(`  ${problem}`);
        } else {
            console.log(`${project}: ${questions.length} questions, `
                + `${new Set(questions.map((q) => q.band)).size} bands`
                + `, + ${after.length} after the task`);
        }
        out[project] = questions;
        afterOut[project] = after;
    }

    const mismatch = failed ? [] : [...compare(out), ...compare(afterOut)];
    if (mismatch.length) {
        failed = true;
        for (const line of mismatch) console.error(line);
    }

    if (failed) {
        console.error('\nnothing written. A quiz that cannot be read here cannot be '
            + 'scored during a session either.');
        process.exit(1);
    }
    writeFileSync(join(here, '..', 'experimenter', 'questions.json'),
        `${JSON.stringify(out, null, 2)}\n`);
    console.log('wrote experimenter/questions.json');
    writeFileSync(join(here, '..', 'experimenter', 'after-questions.json'),
        `${JSON.stringify(afterOut, null, 2)}\n`);
    console.log('wrote experimenter/after-questions.json');

    // The participant's copy, with the answers stripped. It ships to a browser,
    // and a participant who opened the console would otherwise find them — which
    // would make the second sitting a measure of their curiosity.
    const strip = (set) => Object.fromEntries(Object.entries(set).map(([project, qs]) =>
        [project, qs.map(({ n, band, question, options }) => ({ n, band, question, options }))]));
    const forBrowser = strip(out);
    const afterForBrowser = strip(afterOut);
    writeFileSync(join(here, '..', 'participant', 'quiz.js'),
`// The quiz, as the participant sees it.
//
// Generated from the projects' STUDY.md, so there is one source of truth for the
// wording. THE RIGHT ANSWER IS NOT HERE: this file ships to a browser. Marking
// happens in the dashboard, against its own copy.
//
// Do not edit by hand. Run: npm run questions
export const QUIZZES = Object.freeze(${JSON.stringify(forBrowser, null, 4)});

// Asked straight after the task, closed book, about the change they just made.
export const AFTER_QUIZZES = Object.freeze(${JSON.stringify(afterForBrowser, null, 4)});
`);
    console.log('wrote participant/quiz.js');
}

if (import.meta.url === `file://${process.argv[1]}`) main();
