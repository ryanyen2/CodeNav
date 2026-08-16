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

const BAND = /^###\s+(.+?)(?:\s+—.*)?$/;
const QUESTION = /^\*\*Q(\d+)\.\s*(.+?)\*\*\s*$/;
const OPTION = /^-\s*([a-d])\)\s*(.+?)\s*$/;
const CORRECT = /\s*✓\s*$/;

export function parseQuiz(markdown) {
    const start = markdown.indexOf('## The quiz');
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
            current = {
                n: Number(questionMatch[1]),
                band,
                question: questionMatch[2].trim(),
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
    return questions;
}

function check(project, questions) {
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
    return problems;
}

function main() {
    const out = {};
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
        const problems = check(project, questions);
        if (problems.length) {
            failed = true;
            console.error(`${project}:`);
            for (const problem of problems) console.error(`  ${problem}`);
        } else {
            console.log(`${project}: ${questions.length} questions, `
                + `${new Set(questions.map((q) => q.band)).size} bands`);
        }
        out[project] = questions;
    }

    if (failed) {
        console.error('\nnothing written. A quiz that cannot be read here cannot be '
            + 'scored during a session either.');
        process.exit(1);
    }
    writeFileSync(join(here, '..', 'experimenter', 'questions.json'),
        `${JSON.stringify(out, null, 2)}\n`);
    console.log('wrote experimenter/questions.json');

    // The participant's copy, with the answers stripped. It ships to a browser,
    // and a participant who opened the console would otherwise find them — which
    // would make the second sitting a measure of their curiosity.
    const forBrowser = {};
    for (const [project, questions] of Object.entries(out)) {
        forBrowser[project] = questions.map(({ n, band, question, options }) =>
            ({ n, band, question, options }));
    }
    writeFileSync(join(here, '..', 'participant', 'quiz.js'),
`// The quiz, as the participant sees it.
//
// Generated from the projects' STUDY.md, so there is one source of truth for the
// wording. THE RIGHT ANSWER IS NOT HERE: this file ships to a browser. Marking
// happens in the dashboard, against its own copy.
//
// Do not edit by hand. Run: npm run questions
export const QUIZZES = Object.freeze(${JSON.stringify(forBrowser, null, 4)});
`);
    console.log('wrote participant/quiz.js');
}

if (import.meta.url === `file://${process.argv[1]}`) main();
