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
        'Text copied out of a PDF arrives broken, because a PDF stores where each line sat on the page rather than the writing itself.',
        'Sentences come out cut into pieces, and long words come out split wherever the line ran out.',
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
            what: 'A line printed near the top or bottom of enough of the pages is treated as a repeated page title rather than as writing, so it is removed, and page numbers go too.',
        }),
        Object.freeze({
            name: 'Restores headings and notes',
            what: 'A short numbered line such as 3.1 Sites becomes a heading, and the numbered notes printed at the foot of each page are gathered at the end of the document.',
        }),
    ]),

    limits: 'It does not open PDF files itself. It reads text that somebody has already copied out of one, and tables, images and columns do not survive the copying.',

    failure: Object.freeze({
        lead: 'Below is the top line of each page of a five page report. The first three pages share one line, and the two appendix pages at the end have a different one.',
        input: `page 1   Coastal Erosion Survey 2026     Marine Institute
page 2   Coastal Erosion Survey 2026     Marine Institute
page 3   Coastal Erosion Survey 2026     Marine Institute
page 4   Appendix A: Site Photographs    Marine Institute
page 5   Appendix A: Site Photographs    Marine Institute`,
        output: `Ardmore retreated 0.1 metres per year, which is within measurement error of no change at all. The revetment appears to be holding for now.

Appendix A: Site Photographs            Marine Institute`,
        caption: 'A line is removed only when it repeats on enough of the pages. Coastal Erosion Survey 2026 was on three of five and went. Appendix A was on two, so it stayed in the middle of the writing.',
    }),

    ask: Object.freeze([
        'Add a settings file, so different documents can use different rules.',
        'Write a short report beside the Markdown, saying what the program did to the text.',
        'Tidy up how the rules get their settings, because at the moment each rule reads a fixed value written into its own file.',
    ]),

    prompt: 'Add a config file so the rules can be changed per document. Also write a short report.md next to the Markdown saying what the conversion did. While you are in there, tidy up how the rules get their settings, because at the moment they read module constants directly.',
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
            what: 'One bank heads a column Transaction Date and another heads it Date, so column names and date formats are matched loosely and both files read.',
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
        'Move the list of shop names into a file I can edit without touching code.',
        'Add a way to see the same summary by week, next to the monthly one.',
        'Tidy up how the rules get their settings, because at the moment each rule reads a fixed value written into its own file.',
    ]),

    prompt: 'Move the merchant rules out into rules.toml so I can edit them without touching code. Also add a --by-week mode next to the monthly summary. While you are in there, tidy up how the rules get their settings, because at the moment they read module constants directly.',
});

const TASK = Object.freeze({
    lead: 'You asked your coding agent for the following, and it is about to work on it.',
    stage1: 'First, work out what the agent changed, and how the project works now.',
    stage2: 'Then decide what you want to keep, and leave the project in the state you would be happy to ship.',
});

export const COPY = Object.freeze({
    scribe: SCRIBE,
    tally: TALLY,
    task: TASK,
});
