// What a participant reads about the project, and how each condition starts.
//
// Five minutes each. The version this replaced ran to nine source file names, a
// six item rule list where every item carried a second sentence explaining that
// another choice had been possible, and a heading announcing that each rule was
// a tradeoff. A participant reading that spends the budget on the page instead
// of on the project, and arrives at the change with no picture of what the
// program is for.
//
// So each project reads top down: one sentence saying what it is, why anybody
// wants it, one worked example, four rules, what it does not do, and how to run
// it. The case where the current behaviour is unhelpful lives with the task,
// because it is the reason the request exists.
//
// Every input and output below came from running the project.

import { COPY } from './prose.js';

const COMMANDS = Object.freeze({
    scribe: Object.freeze([
        ['.venv/bin/scribe convert fixtures/report.txt', 'Convert one document'],
        ['.venv/bin/scribe check fixtures/', 'Convert everything, write nothing'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ]),
    tally: Object.freeze([
        ['.venv/bin/tally summarise fixtures/current.csv', 'Summarise one export'],
        ['.venv/bin/tally check fixtures/', 'Summarise everything, write nothing'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ]),
});

const build = (name) => Object.freeze({ name, ...COPY[name], commands: COMMANDS[name] });

export const PROJECTS = Object.freeze({
    scribe: build('scribe'),
    tally: build('tally'),
});

/** The words around the request, which are the same whichever project it is. */
export const TASK = COPY.task;

/**
 * How each condition is started.
 *
 * The two used to differ in shape as well as in content: one arm ran a daemon in
 * a terminal of its own and opened two more, and the other opened one. That is a
 * difference in how much setting up a person does, on top of the difference the
 * study is actually about. Both are four steps now, and the daemon starts itself
 * behind the session.
 */
const TRUST = 'Choose "Yes, I trust the authors" when VS Code asks. Until you do, '
    + 'it turns off extensions, and this study needs them running.';

export const HOW_TO_START = Object.freeze({
    codoc: {
        title: 'The way of working with codoc',
        folder: (p) => `~/codoc-study/${p}`,
        steps: [
            ['Open this folder in VS Code.', '{folder}'],
            [TRUST, null],
            ['Open the description: press Cmd+Shift+P and run "codoc: Open". It opens as a tab beside your code.', null],
            ['Open a terminal inside VS Code.', null],
        ],
    },
    baseline: {
        title: 'The way of working without codoc',
        folder: (p) => `~/codoc-study/${p}`,
        steps: [
            ['Open this folder in VS Code.', '{folder}'],
            [TRUST, null],
            ['Open the description: CLAUDE.md, in the file tree. It opens as a tab beside your code.', null],
            ['Open a terminal inside VS Code.', null],
        ],
    },
});
