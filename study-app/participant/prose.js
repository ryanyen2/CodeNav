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
        caption: 'The three lines become one paragraph, and the word broken across two of them is put back together.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: 'Rejoins broken lines',
            what: 'Lines that belong to the same paragraph are joined into one, and a blank line still starts a new paragraph.',
        }),
        Object.freeze({
            name: 'Rejoins split words',
            what: 'A word broken across two lines is put back together, and a short list of prefixes such as well and self keeps its hyphen.',
        }),
        Object.freeze({
            name: 'Drops repeated headers',
            what: 'A line that sits near the top or bottom of enough pages is treated as a header and removed, and page numbers go the same way.',
        }),
        Object.freeze({
            name: 'Restores headings and notes',
            what: 'A numbered line such as 3.1 Sites becomes a heading, and footnotes are gathered at the end of the document.',
        }),
    ]),

    limits: 'It does not open PDF files itself, and tables, images and columns are already gone by the time the text reaches it.',

    failure: Object.freeze({
        lead: 'A long report often carries one running header over its main pages and a different one over its appendix.',
        input: `page 1   Coastal Erosion Survey 2026     Marine Institute
page 2   Coastal Erosion Survey 2026     Marine Institute
page 3   Coastal Erosion Survey 2026     Marine Institute
page 4   Appendix A: Site Photographs    Marine Institute
page 5   Appendix A: Site Photographs    Marine Institute`,
        output: `Ardmore retreated 0.1 metres per year, which is within measurement error of no change at all. The revetment appears to be holding for now.

Appendix A: Site Photographs            Marine Institute`,
        caption: 'The first header covered three of the five pages and was removed, and the second covered two, so it stayed in the middle of the writing.',
    }),

    ask: Object.freeze([
        'Add a settings file, so the rules can be set differently for each document.',
        'Write a short report next to the Markdown saying what the conversion did.',
        'Tidy up how the rules get their settings, because right now they read constants in the code.',
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
        caption: 'The two supermarket payments become one groceries figure, and the money moved into savings is left out because it was never spent.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: "Reads any bank's file",
            what: 'Column names and date formats are matched loosely, so an export from a different bank still reads.',
        }),
        Object.freeze({
            name: 'Sorts payments into categories',
            what: 'Each merchant name is matched against a list of patterns, so anything with tesco in it counts as groceries.',
        }),
        Object.freeze({
            name: 'Groups by month',
            what: 'A payment counts towards the month you made it in, not the month the bank got round to processing it.',
        }),
        Object.freeze({
            name: 'Leaves some rows out',
            what: 'A payment the bank exported twice is counted once, and money moved between your own accounts is not counted as spending.',
        }),
    ]),

    limits: 'It does not connect to a bank, and it has no opinion about whether any of the spending was a good idea.',

    failure: Object.freeze({
        lead: 'The merchant patterns are a fixed list written into the code, and a shop missing from the list matches nothing.',
        input: `03/01/2026,WAITROSE 220,44.10,Everyday
02/02/2026,WAITROSE 220,51.80,Everyday
03/03/2026,WAITROSE 220,39.95,Everyday`,
        output: `## 2026-01

  utilities            -74.00
  fuel                 -52.00
  uncategorised        -44.10
  subscriptions        -11.99`,
        caption: 'A supermarket that is missing from the patterns is counted as uncategorised, and no month in the file gets a groceries figure at all.',
    }),

    ask: Object.freeze([
        'Move the merchant rules into a file I can edit without touching code.',
        'Add a weekly mode next to the monthly summary.',
        'Tidy up how the rules get their settings, because right now they read constants in the code.',
    ]),

    prompt: 'Move the merchant rules out into rules.toml so I can edit them without touching code. Also add a --by-week mode next to the monthly summary. While you are in there, tidy up how the rules get their settings, because at the moment they read module constants directly.',
});

const TASK = Object.freeze({
    lead: 'You asked your coding agent for the following, and it is about to work on it.',
    stage1: 'First, build up a picture of what the agent changed and how the project works now.',
    stage2: 'Then decide what you want to keep, and leave the project in the state you would be happy to ship.',
});

export const COPY = Object.freeze({
    scribe: SCRIBE,
    tally: TALLY,
    task: TASK,
});
