// The project briefings, on the page.
//
// These used to be a markdown file the researcher shared on the call, and the
// page said "the researcher will show you a short page about this project". That
// was one context switch per condition at exactly the moment the participant was
// forming their model of the codebase, and it made the briefing depend on
// somebody remembering to send the right file. Everything is here now.
//
// Each one leads with the PROBLEM rather than the program, shows one worked
// before-and-after, then names what it does in plain words. The last section is
// the one that matters: every rule is a judgement call that could have gone the
// other way, and the code shows what was chosen without saying why. That is the
// study's premise, said in a way that primes without leading.
//
// The full versions live beside the code, in projects/<name>/ABOUT.md.

const SCRIBE = {
    name: 'scribe',
    oneLine: 'Text pulled out of a PDF, into clean Markdown.',
    problem: [
        'Copy text out of a PDF and paste it somewhere. It comes out broken.',
        'Those line breaks are not in the writing. They are where the line ran out on the page. A PDF stores text with the page baked in, so taking it back out gives you the page, not the writing.',
    ],
    before: `The survey covered four hundred kilometres of shoreline between March
and September. Rates of retreat were higher than the 2019 baseline at
every site except Ardmore, where a new revetment has held the line.`,
    after: `The survey covered four hundred kilometres of shoreline between March and
September. Rates of retreat were higher than the 2019 baseline at every site
except Ardmore, where a new revetment has held the line.`,
    afterNote: 'One paragraph, as it was written.',
    does: [
        ['Broken words', 'A long word split at the end of a line, like photogram- then metric, is joined back up.'],
        ['Broken paragraphs', 'Lines inside a paragraph are joined. The gap between paragraphs is kept.'],
        ['Repeated headers', 'A report often has the same line at the top of every page. Useful on paper, noise in the text. Page numbers too.'],
        ['Headings', '3.1 Sites becomes a real heading, so the result has structure.'],
        ['Footnotes', 'In a PDF the little number is stuck to a word and the note is at the bottom of the page. They are gathered at the end and linked.'],
        ['Typesetting characters', 'Printers use one character for fi and curly quotes for straight ones. Those are replaced, so the result can be searched normally.'],
    ],
    notScope: 'It does not read PDF files — something else does that first and hands scribe plain text. It does not recover tables, images or columns; that information is gone before scribe sees it.',
    judgement: [
        ['Repeated headers', 'Dropping them is right for a hundred-page report. For a one-page letter, that line is the letterhead, and dropping it loses something.'],
        ['Broken words', 'photogram-metric should join. But well-being split across two lines should keep its hyphen, because the hyphen is part of the word. Nothing in the text tells you which is which.'],
    ],
    commands: [
        ['.venv/bin/scribe convert fixtures/report.txt', 'Convert one file, write the .md beside it'],
        ['.venv/bin/scribe convert fixtures/report.txt -', 'Convert one file, print it instead'],
        ['.venv/bin/scribe check fixtures/', 'Convert everything, write nothing'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ],
    commandNote: 'The .venv/bin/ at the front matters: it runs the project\'s own copy of Python. A run prints one line, e.g. report.txt: 3 pages, 8 headings, 8 paragraphs, 6 bullets, 2 notes, 6 lines of furniture. "Furniture" is the project\'s word for repeated headers and page numbers.',
    layout: [
        ['scribe/lines.py', 'splits the input into pages and lines'],
        ['scribe/furniture.py', 'repeated headers, page numbers'],
        ['scribe/paragraphs.py', 'joining broken words and broken lines'],
        ['scribe/blocks.py', 'headings, bullets, blank space'],
        ['scribe/notes.py', 'footnotes'],
        ['scribe/text.py', 'typesetting characters'],
        ['scribe/convert.py', 'runs the rules, in order'],
    ],
    inputs: 'Three sample documents in fixtures/: a survey report, a short memo, and a field handbook. They are different on purpose — the memo has no repeated header, so a rule that helps the report can hurt the memo.',
};

const TALLY = {
    name: 'tally',
    oneLine: 'A bank export, into a monthly summary.',
    problem: [
        'Download your transactions from a bank and you get a CSV: one row per payment, hundreds of rows, in the order they happened.',
        'That answers "what happened on the 3rd". It does not answer "what did I spend on food last month", which is the question people actually have.',
    ],
    before: `2026-01-03,TESCO STORES 3241,-52.40
2026-01-04,Transfer to savings,-300.00
2026-01-06,PRET A MANGER,-4.85
2026-01-08,SHELL 4417,-61.20`,
    after: `## 2026-01

  housing             -950.00
  groceries           -104.80
  utilities            -88.00
  fuel                 -61.20
  eating out            -4.85

  total              -1208.85`,
    afterNote: 'What you spent, by month and category.',
    does: [
        ['Reads any bank\'s file', 'Every bank names its columns differently and writes dates differently. tally matches loosely against the names banks actually use.'],
        ['Puts each payment in a category', 'By matching the merchant name against a list of patterns: anything with "tesco" is groceries, anything with "shell" is fuel.'],
        ['Groups by month', 'So you can compare one month against another.'],
        ['Drops repeats', 'Banks sometimes export the same payment twice. The second one is dropped.'],
        ['Leaves out transfers', 'Moving money from your current account to your savings is not spending — it is still yours.'],
        ['Finds what recurs', 'A payment that appears every month at the same amount is a fixed commitment. Those are listed separately.'],
    ],
    notScope: 'It does not connect to a bank — something else downloads the file. It does not tell you whether you can afford anything, and it has no opinion about your spending.',
    judgement: [
        ['Which month a payment belongs to', 'You pay for something on the 31st of January; the bank processes it on the 2nd of February. tally says January, because that is the day you remember. Your bank\'s own statement says February. Both are right, for different questions.'],
        ['Transfers', 'Leaving them out is right if you are asking what you spent. If you are asking where your money went, you might want them in.'],
    ],
    commands: [
        ['.venv/bin/tally summarise fixtures/current.csv', 'Summarise one file, write the .md beside it'],
        ['.venv/bin/tally summarise fixtures/current.csv -', 'Summarise one file, print it instead'],
        ['.venv/bin/tally check fixtures/', 'Summarise everything, write nothing'],
        ['.venv/bin/python -m pytest tests/ -q', 'Run the tests'],
    ],
    commandNote: 'The .venv/bin/ at the front matters: it runs the project\'s own copy of Python. A run prints one line, e.g. current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring.',
    layout: [
        ['tally/rows.py', 'reads the CSV, whatever the bank called its columns'],
        ['tally/categories.py', 'merchant name to category'],
        ['tally/dedupe.py', 'repeats, and transfers between your own accounts'],
        ['tally/months.py', 'which month, and what a refund does'],
        ['tally/recurring.py', 'payments that come round every month'],
        ['tally/money.py', 'rounding, and which way round the signs are'],
        ['tally/summary.py', 'runs the rules, in order'],
    ],
    inputs: 'Three sample files in fixtures/: a current account over three months, an export from a different bank with different column names, and a short file of payments made at the end of one month and processed at the start of the next.',
};

export const PROJECTS = Object.freeze({ scribe: SCRIBE, tally: TALLY });

/** Said once per condition, and identical in both, which is the point. */
export const RESPONSIBILITY = Object.freeze([
    'Work however you normally would, and use the coding agent as much or as little as you like.',
    'The agent has already done the work the card describes. What is left is yours to decide: keep what is right, change what is not, and leave the written description saying what the code does. We will ask you about those decisions afterwards, so make them on purpose.',
    'We will also ask you to explain the code: what it does, why it is built that way, and what you would change to extend it.',
]);

/**
 * How each condition is started.
 *
 * The two differ, and that difference is the manipulation, so it is written down
 * once here rather than improvised on a call where one participant gets a fuller
 * explanation than the next.
 */
/**
 * The workspace trust prompt, in both conditions.
 *
 * VS Code asks whether you trust a folder the first time you open it, and until
 * you say yes it runs in Restricted Mode with every extension DISABLED. That is
 * silent: the editor looks normal, the files open, and the study logger and codoc
 * simply never start. The session would then record nothing at all, which is the
 * one failure that cannot be repaired afterwards.
 */
const TRUST = 'VS Code will ask whether you trust the folder. Choose "Yes, I trust '
    + 'the authors". Until you do, it turns off extensions, and this study needs '
    + 'them running.';

export const HOW_TO_START = Object.freeze({
    codoc: {
        title: 'The way of working with codoc',
        folder: (p) => `~/codoc-study/${p}`,
        steps: [
            ['Open this folder in VS Code.', '{folder}'],
            [TRUST, null],
            ['Open a terminal inside VS Code and run this. Leave it running for the whole task.', '~/codoc-study/codoc watch --root {folder}'],
            ['Open the written description: press Cmd+Shift+P and run "codoc: Open". It opens as a tab, beside your code.', null],
            ['Open a second terminal and start the coding agent with this. Use it rather than plain claude, so it runs on the study\'s account rather than yours.', './claude-study'],
            ['Open a third terminal for running the project.', null],
        ],
        about: [
            'The written description is a tree of features. Each one names something the project does and points at the code that does it.',
            'It is yours to edit. When the code changes underneath it, codoc proposes a change to the description, and you accept or reject it inline.',
            'The coding agent can read it too, so anything you write there is something the agent can act on.',
            'To find your way around it: press Cmd+F in the description to search it (and replace across it). To get oriented on something you do not understand, run "/codoc:ask" in the agent with a question about the project — it draws a numbered path through the description to the parts that answer it, which you step through with the arrows.',
        ],
    },
    baseline: {
        title: 'The way of working without codoc',
        folder: (p) => `~/codoc-study/${p}`,
        steps: [
            ['Open this folder in VS Code.', '{folder}'],
            [TRUST, null],
            ['Open a terminal inside VS Code and start the coding agent with this. Use it rather than plain claude, so it runs on the study\'s account rather than yours.', './claude-study'],
            ['Open a second terminal for running the project.', null],
        ],
        about: [
            'The written description is CLAUDE.md in the project root. It holds the same text, describing the same things.',
            'It is yours to edit, in the ordinary editor. The coding agent reads it on its own and has been told to keep it current after every change it makes.',
        ],
    },
});
