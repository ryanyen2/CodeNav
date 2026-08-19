// How the thing you are about to work with works.
//
// A participant used to meet the description for the first time in the same
// minute they met the codebase, with four lines of text to explain it and a
// researcher improvising the rest on the call. They spent the first part of the
// task working out what they were looking at, which is not what the study is
// comparing.
//
// So each condition gets five minutes and a page of its own, built the way a
// tutorial is built: one picture, then a small number of steps, each with a
// figure and two or three sentences. Both conditions get the same shape and the
// same number of steps, because a page that is longer in one arm teaches more in
// that arm and the comparison stops being about the tool.
//
// Figures that exist are files under `img/`. Figures that do not exist yet
// render as a labelled space saying exactly what belongs there, so a missing
// screenshot is a visible gap on a draft rather than a broken image in front of
// a participant.

/**
 * One tutorial step.
 *
 * `figure.src`   a file in img/, drawn at full width
 * `figure.todo`  no picture yet; draw the space and say what goes in it
 * `points`       what to look at, as short lines rather than a paragraph
 */

const CODOC = {
    title: 'The description of this project',
    lead: 'It is a tree of features. Each one names something the project does, '
        + 'and points at the code that does it.',
    hero: {
        src: 'img/codoc-ui.png',
        alt: 'The description open in the editor, with its parts labelled',
        caption: 'The description opens as a tab beside your code.',
    },
    // Keyed to the labels on the picture above, in the order the eye meets them.
    parts: [
        ['Navigation tree', 'Every feature in the project, on the left. Click one to jump to it.'],
        ['The description', 'What each feature does, and why it was built that way.'],
        ['Linked code', 'The names under a heading are the code files belonging to that feature. Click one to open it.'],
        ['Pending suggestions', 'Changes waiting for you to accept or reject, counted in the toolbar.'],
    ],
    steps: [
        {
            title: 'Read it',
            points: [
                'The tree is the map. Start at the top and open what you need.',
                'Each heading is a feature. The names under it are the code files it is linked to.',
                'Clicking one of those names opens that code.',
                'Search it with Cmd+F, which also replaces across it.',
            ],
            figure: {
                todo: 'A feature heading with its code names underneath, one being clicked, and the '
                    + 'source file opening beside it.',
            },
        },
        {
            title: 'Ask it',
            points: [
                'Type /codoc:ask followed by a question in the agent terminal.',
                'It draws a numbered path through the features that answer your question.',
                'Step through the path with the arrows.',
            ],
            figure: {
                todo: 'The result of /codoc:ask, showing the numbered walkthrough with its arrows.',
            },
        },
        {
            title: 'See what changed',
            points: [
                'When the code changes, the description updates to match, and the changed '
                    + 'words are highlighted where they are.',
                'A rewritten paragraph shows "Keep" and "Restore mine" beside it.',
                'What the agent only PROPOSES is written in grey, where it would go — '
                    + 'accept or reject it on its heading.',
                'Nothing grey has been built, and you do not have to answer it.',
            ],
            figure: {
                todo: 'A description with a highlighted rewritten sentence, Keep and Restore mine '
                    + 'beside it, and a greyed proposed feature in place below.',
            },
        },
        {
            title: 'Change it',
            points: [
                'The text is yours to edit. Type into it like any document.',
                'What you type turns blue until the code catches up with it.',
                'Select a sentence and leave a comment on it to tell the agent what to change.',
                'Click "Build it" in the comment box to make the agent carry out your request.',
            ],
            figure: {
                todo: 'A sentence selected, the comment box open beside it, and the Build it button.',
            },
        },
    ],
    // The description marks THREE different things, and a reader who has not been
    // told reads all three as damage. Each is a different property of the text —
    // colour, fading, highlight — so they can appear on the same words at once;
    // said plainly here because that is not guessable.
    //
    // Four lines rather than three because green and red are one channel split by
    // direction, and a participant reading "green or red" has to hold a compound
    // where four short lines cost nothing.
    marks: [
        ['Blue', 'you wrote it, and the code has not caught up yet'],
        ['Grey', 'the agent proposes it, and nothing has been built'],
        ['Green', 'the code now says this'],
        ['Red', 'the code no longer says this'],
    ],
};

const BASELINE = {
    title: 'The description of this project',
    lead: 'It is a file called CLAUDE.md in the project root. It says what the '
        + 'project does, and why it was built that way.',
    hero: {
        todo: 'CLAUDE.md open in the editor, beside a source file.',
        caption: 'The description opens as a tab beside your code.',
    },
    parts: [
        ['One file', 'Everything about the project is in it, top to bottom.'],
        ['Ordinary Markdown', 'Headings and paragraphs. Nothing special about the format.'],
        ['Read by the agent', 'It reads the file on its own, without being asked.'],
        ['Yours to edit', 'It is a file in your project like any other.'],
    ],
    steps: [
        {
            title: 'Read it',
            points: [
                'Open CLAUDE.md from the file tree.',
                'The headings are the parts of the project.',
                'Search it with Cmd+F like any file.',
            ],
            figure: {
                todo: 'CLAUDE.md open in the editor with its headings visible.',
            },
        },
        {
            title: 'Ask about it',
            points: [
                'Ask the agent a question in your own words.',
                'It has already read the file and answers from it and from the code.',
            ],
            figure: {
                todo: 'A question typed to the agent and the answer it gives.',
            },
        },
        {
            title: 'See what changed',
            points: [
                'The agent keeps the file current after it changes the code.',
                'Use git diff, or the editor\'s own source control view, to see what moved.',
            ],
            figure: {
                todo: 'The source control view showing changes to CLAUDE.md beside changes to code.',
            },
        },
        {
            title: 'Change it',
            points: [
                'Edit it in the editor and save.',
                'Anything you write there is something the agent reads next time.',
            ],
            figure: {
                todo: 'CLAUDE.md being edited in the ordinary editor.',
            },
        },
    ],
    marks: [],
};

export const TUTORIAL = Object.freeze({ codoc: CODOC, baseline: BASELINE });
