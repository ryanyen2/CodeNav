// Participant-facing copy for the two projects, and for the task page.
//
// Written for somebody who has five minutes, has never seen the codebase, and
// will not see it again. Each project reads from the top down: one sentence
// saying what it is, why anybody wants it, one worked example, four rules, what
// it does not do, and one case where the current behaviour is unhelpful.
//
// Every input and output below was produced by running the project. The scribe
// worked example is a slice of fixtures/report.txt. The tally worked example is
// four rows of fixtures/current.csv. The tally failure is fixtures/other-bank.csv.
// The scribe failure is a five page document built to sit under the repeat
// threshold; the output shown is what the converter printed for it.
//
// `prompt` is the request file collapsed to one line, word for word, so it can
// be pasted into a terminal.

const SCRIBE = Object.freeze({
    oneLine: 'Turns text copied out of a PDF into clean Markdown.',

    why: Object.freeze([
        'Text copied out of a PDF arrives broken, because a PDF stores where each line sat on the page rather than the actual text.',
        'Sentences come out cut into pieces, and long words come out split wherever the line ran out of space.',
    ]),

    worked: Object.freeze({
        inputLabel: 'Copied out of a PDF',
        input: `A fixed-wing drone flew each site at ninety metres. Ground control
points were re-surveyed at the start of each visit, because settle-
ment had moved two of the 2019 markers.`,
        outputLabel: 'After scribe',
        output: `A fixed-wing drone flew each site at ninety metres. Ground control points were re-surveyed at the start of each visit, because settlement had moved two of the 2019 markers.`,
        caption: 'The three lines become one paragraph, and settlement, which was split into settle and ment across two of them, is put back together.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: 'Rejoins broken lines',
            what: 'Lines broken only because the text reached the edge of the page are joined back into one paragraph, and a blank line still starts a new paragraph.',
        }),
        Object.freeze({
            name: 'Rejoins split words',
            what: 'A word split across two lines is put back together, the way settlement was above, and words starting with well or self keep their hyphen.',
        }),
        Object.freeze({
            name: 'Drops repeated headers',
            what: 'A line that appears near the top or bottom of most of the pages is treated as a repeating page header and removed. Page numbers are removed too.',
        }),
        Object.freeze({
            name: 'Restores headings and notes',
            what: 'A short numbered line such as 3.1 Sites becomes a heading, and the numbered notes printed at the foot of each page are gathered at the end of the document.',
        }),
    ]),

    limits: 'It does not open PDF files itself. It reads text that somebody has already copied out of one, and tables, images and columns do not survive the copying.',

    failure: Object.freeze({
        lead: 'One of the sample documents, survey.txt, is a five page report. The first three pages all have the same header. The last two, the appendix, have a different header.',
        input: `page 1   Coastal Erosion Survey 2026     Marine Institute
page 2   Coastal Erosion Survey 2026     Marine Institute
page 3   Coastal Erosion Survey 2026     Marine Institute
page 4   Appendix A: Site Photographs    Marine Institute
page 5   Appendix A: Site Photographs    Marine Institute`,
        output: `Ardmore retreated 0.1 metres per year, which is within measurement error of no change at all. The revetment appears to be holding for now.

Appendix A: Site Photographs            Marine Institute`,
        caption: 'A header is only removed when it appears on most of the pages. "Coastal Erosion Survey 2026" was on three of five pages, so it was removed. "Appendix A" was only on two, so it was kept and ended up in the middle of the converted text. Convert survey.txt yourself to see it.',
    }),

    ask: Object.freeze([
        'Let each document be converted with its own rules, without editing the code.',
        'And tell me what the conversion did, so I can see which rules fired on which document.',
    ]),

    // What a run prints today, so a number that moves is visible without anybody
    // having to remember what it used to be. This is the whole debugging surface:
    // one command, three documents, one line each.
    repl: Object.freeze({
        lead: 'Converting all four sample documents prints one line each. This is what they print now, before anything changes.',
        command: '.venv/bin/scribe check fixtures/',
        before: `handbook.txt: 4 pages, 9 headings, 10 paragraphs, 3 bullets, 0 notes, 12 lines of furniture
memo.txt: 2 pages, 0 headings, 7 paragraphs, 0 bullets, 0 notes, 0 lines of furniture
report.txt: 3 pages, 8 headings, 12 paragraphs, 6 bullets, 2 notes, 6 lines of furniture
survey.txt: 5 pages, 3 headings, 12 paragraphs, 0 bullets, 0 notes, 8 lines of furniture`,
        caption: '"Furniture" is the project\'s word for repeated page headers and page numbers. If a change causes more or fewer lines of furniture to be removed, the numbers here will change.',

        // WHAT TO RUN, NOT WHAT TO FIND.
        //
        // Both planted problems are reachable from a terminal in under a minute
        // and neither is reachable by reading, so a participant who did not think
        // to run anything scored zero for a reason that has nothing to do with the
        // way of working they were given. Naming the commands raises that floor
        // for both conditions equally.
        //
        // What is NOT here is any statement that something is wrong, or which
        // line to look at. That is the thing being measured, and the page has
        // never handed it over.
        after: Object.freeze({
            lead: 'When it says it has finished, run these yourself.',
            commands: Object.freeze([
                ['.venv/bin/scribe check fixtures/', 'The same four documents as above'],
                ['cat scribe.toml', 'The settings the change added'],
            ]),
            caption: 'Compare the first against the table above, line by line, and both against what the settings say should happen. Every number that moved was moved by this change. Decide for each whether it should have, and put right anything that should not have.',
        }),
    }),

    prompt: 'Different documents need different rules, and I should not have to edit the source to convert one properly. Make that configurable, and tell me what the conversion actually did to each document.',
});

const TALLY = Object.freeze({
    oneLine: 'Turns a bank export into a monthly spending summary.',

    why: Object.freeze([
        'A bank export gives you one row per payment in the order the payments happened, so it answers what happened on a given day.',
        'It does not answer what you spent on food last month, which is usually the question people have.',
    ]),

    worked: Object.freeze({
        inputLabel: 'Four rows of a bank export',
        input: `Transaction Date,Description,Amount
2026-01-03,TESCO STORES 3241,-52.40
2026-01-04,Transfer to savings,-300.00
2026-01-06,PRET A MANGER,-4.85
2026-01-16,TESCO STORES 3241,-52.40`,
        outputLabel: 'After tally',
        output: `## 2026-01

  groceries           -104.80
  eating out            -4.85

  total               -109.65`,
        caption: 'The two Tesco payments add up into the one groceries figure, and the 300.00 moved into savings is left out because it was never spent.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: "Reads any bank's file",
            what: 'Different banks use different column names (Transaction Date vs Date) and date formats. The tool matches them loosely, so exports from any bank are accepted.',
        }),
        Object.freeze({
            name: 'Sorts payments into categories',
            what: 'The shop name on each row is matched against a list of names, so anything with tesco in it counts as groceries.',
        }),
        Object.freeze({
            name: 'Groups by month',
            what: 'A payment counts towards the month you made it in, not the month the bank got round to processing it.',
        }),
        Object.freeze({
            name: 'Leaves some rows out',
            what: 'A payment the bank exported twice is counted once, and a row like Transfer to savings moves money between your own accounts, so it is not spending.',
        }),
    ]),

    limits: 'It does not connect to a bank, so you give it a file you exported yourself. It says nothing about whether any of the spending was a good idea.',

    failure: Object.freeze({
        lead: 'A payment is put in a category by looking for a known shop name in the description, and the list of names is written into the code. Waitrose is not on it.',
        input: `03/01/2026,WAITROSE 220,44.10,Everyday
02/02/2026,WAITROSE 220,51.80,Everyday
03/03/2026,WAITROSE 220,39.95,Everyday`,
        output: `## 2026-01

  utilities            -74.00
  fuel                 -52.00
  uncategorised        -44.10
  subscriptions        -11.99`,
        caption: 'The three Waitrose payments are food shopping, but they land under uncategorised. No month in the file gets a groceries figure at all.',
    }),

    ask: Object.freeze([
        'Let me manage the list of shop names myself, without editing the code.',
        'And show spending at a finer grain than a whole month.',
    ]),

    repl: Object.freeze({
        lead: 'Summarising all three sample exports prints one line each. This is what they print now, before anything changes.',
        command: '.venv/bin/tally check fixtures/',
        before: `boundary.csv: 7 rows, 3 months, 0 duplicates, 0 transfers, 0 uncategorised, 0 recurring
current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring
other-bank.csv: 13 rows, 3 months, 0 duplicates, 0 transfers, 3 uncategorised, 1 recurring`,
        caption: 'boundary.csv is the awkward one: every payment in it was made at the end of one month and processed at the start of the next, so which of the two dates a summary uses decides where its money lands. Once the change is in there is a finer-grained summary as well as this one \u2014 the project\u0027s own settings say which date each of them follows.',

        // See scribe's note above: the commands, and not what they show.
        //
        // The last two are the same seven payments summarised two ways, which is
        // a comparison nobody makes by accident and anybody can make once the
        // page has said the two commands out loud.
        after: Object.freeze({
            lead: 'When it says it has finished, run these yourself.',
            commands: Object.freeze([
                ['.venv/bin/tally check fixtures/', 'The same three exports as above'],
                ['cat tally/rules.toml', 'The settings the change added'],
                ['.venv/bin/tally summarise fixtures/boundary.csv -', 'boundary.csv in full, printed rather than written'],
                ['.venv/bin/tally summarise fixtures/boundary.csv - --by-week', 'The same seven payments, a week at a time'],
            ]),
            caption: 'Compare the first against the table above, line by line, and the last two against each other. Every number that moved was moved by this change. Decide for each whether it should have, and put right anything that should not have.',
        }),
    }),

    prompt: 'I want to manage the categories myself without touching the source, and I want to see spending at a finer grain than a whole month. Make that work.',
});

const TASK = Object.freeze({
    lead: 'You asked your coding agent for the following, and it is about to work on it.',
    stage1: 'The agent reads the code and proposes what it plans to do. Look at the proposals and accept or reject each one.',
    stage2: 'The agent writes the code. Read through what it changed and check that it makes sense.',
    stage3: 'Run the project yourself to confirm it works. Fix anything that is wrong before you finish.',
});

export const COPY = Object.freeze({
    scribe: SCRIBE,
    tally: TALLY,
    task: TASK,
});
