// Pulls the questions and their scoring tables out of the sheets.
//
// The markdown files are the instrument. They are what a researcher reads from
// during a session and what gets frozen at pre-registration, so they stay the one
// source of truth and this reads them rather than the dashboard keeping a second
// copy that could drift.
//
//   node scripts/extract-questions.mjs
//   → experimenter/questions.json
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SHEETS = join(here, '..', '..', 'docs', 'study-materials');

/** `### 3. Why the dev server works the way it does  [R1]` */
const HEADING = /^###\s+(\d+)\.\s+(.+?)\s*\[([A-Z]\d)\]\s*$/;
/** `| 2 | The answer says … |` */
const SCORE_ROW = /^\|\s*([012])\s*\|\s*(.+?)\s*\|\s*$/;

export function parseSheet(markdown) {
    const lines = markdown.split('\n');
    const questions = [];
    let current = null;
    let round = 1;

    const finish = () => {
        if (!current) return;
        current.question = current.questionLines.join(' ').replace(/\s+/g, ' ').trim();
        delete current.questionLines;
        questions.push(current);
        current = null;
    };

    for (const raw of lines) {
        const line = raw.trimEnd();
        if (/^##\s+Round two/i.test(line)) round = 2;

        const heading = HEADING.exec(line);
        if (heading) {
            finish();
            current = {
                number: Number(heading[1]),
                title: heading[2],
                code: heading[3],
                round,
                repeated: false,
                questionLines: [],
                scores: {},
            };
            continue;
        }
        if (!current) continue;

        if (/^>\s?/.test(line)) {
            current.questionLines.push(line.replace(/^>\s?/, ''));
            continue;
        }
        if (/asked again in round two/i.test(line)) { current.repeated = true; continue; }

        const score = SCORE_ROW.exec(line);
        if (score) current.scores[score[1]] = score[2];
    }
    finish();
    return questions;
}

function check(questions, name) {
    const problems = [];
    if (questions.length !== 10) problems.push(`${name}: found ${questions.length} questions, expected 10`);
    for (const q of questions) {
        if (!q.question) problems.push(`${name} ${q.code}: no question text`);
        for (const s of ['0', '1', '2']) {
            if (!q.scores[s]) problems.push(`${name} ${q.code}: no rule for score ${s}`);
        }
    }
    const codes = questions.map((q) => q.code);
    if (new Set(codes).size !== codes.length) problems.push(`${name}: duplicate codes`);
    return problems;
}

function main() {
    const out = {};
    const problems = [];
    for (const project of ['hearth', 'ember']) {
        const md = readFileSync(join(SHEETS, `questions-${project}.md`), 'utf8');
        const questions = parseSheet(md);
        problems.push(...check(questions, project));
        out[project] = questions;
    }
    if (problems.length) {
        console.error('the question sheets did not parse cleanly:');
        for (const p of problems) console.error(`  ${p}`);
        process.exit(1);
    }
    const dest = join(here, '..', 'experimenter', 'questions.json');
    writeFileSync(dest, `${JSON.stringify(out, null, 2)}\n`);
    console.log(`extracted ${out.hearth.length} + ${out.ember.length} questions`);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
