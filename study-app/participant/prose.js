// Participant-facing copy for the two projects, and for the task page.
//
// Written for somebody who has five minutes, has never seen a codebase like
// this, and will not see it again. Every sentence assumes the reader knows
// nothing about the project. Short sentences, one idea each, no jargon.
//
// Every input and output below was produced by running the project.
//
// `prompt` is the request pasted into the agent terminal, word for word.

const SCRIBE = Object.freeze({
    oneLine: 'Scribe takes text copied from a PDF and turns it into clean Markdown.',

    why: Object.freeze([
        'Text copied from a PDF is often messy. A PDF stores where text appears on the page, rather than storing it like a normal document.',
        'Because of this, sentences can be broken across several lines, and long words can be split when they reach the edge of the page.',
    ]),

    worked: Object.freeze({
        inputLabel: 'Text copied from a PDF',
        input: `A fixed-wing drone flew each site at ninety metres. Ground control
points were re-surveyed at the start of each visit, because settle-
ment had moved two of the 2019 markers.`,
        outputLabel: 'After scribe',
        output: `A fixed-wing drone flew each site at ninety metres. Ground control points were re-surveyed at the start of each visit, because settlement had moved two of the 2019 markers.`,
        caption: 'It joins the three lines into one paragraph and puts settle- and ment back together to make settlement.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: 'It joins broken lines',
            what: 'If a sentence was split across lines because it reached the edge of the page, scribe joins the lines back together. A blank line still means a new paragraph starts.',
        }),
        Object.freeze({
            name: 'It puts split words back together',
            what: 'If a word was split between two lines, scribe joins it back together. For example, settle- and ment become settlement. It keeps normal hyphens in words such as well-known and self-employed.',
        }),
        Object.freeze({
            name: 'It removes repeated page headers',
            what: 'If the same line appears near the top or bottom of most pages, scribe assumes it is a page header and removes it. It also removes page numbers.',
        }),
        Object.freeze({
            name: 'It turns headings and notes into the right format',
            what: 'A short numbered line such as 3.1 Sites is turned into a heading. Numbered notes that appear at the bottom of pages are collected and placed at the end of the document.',
        }),
    ]),

    limits: 'Scribe does not open PDF files itself. You must first copy the text out of the PDF. Tables, images and columns may be lost when copying.',

    failure: Object.freeze({
        lead: 'Different documents can need different rules. One of the sample documents, survey.txt, is a five page report. The first three pages have one header. The last two pages have a different header.',
        input: `page 1   Coastal Erosion Survey 2026     Marine Institute
page 2   Coastal Erosion Survey 2026     Marine Institute
page 3   Coastal Erosion Survey 2026     Marine Institute
page 4   Appendix A: Site Photographs    Marine Institute
page 5   Appendix A: Site Photographs    Marine Institute`,
        output: `Ardmore retreated 0.1 metres per year, which is within measurement error of no change at all. The revetment appears to be holding for now.

Appendix A: Site Photographs            Marine Institute`,
        caption: 'Scribe removes a header only when it appears on most of the pages. The first header appears on 3 out of 5 pages, so scribe removes it. The second header only appears on 2 out of 5 pages, so scribe does not remove it. That leaves unwanted text in the converted document. You can see this yourself by converting survey.txt.',
    }),

    ask: Object.freeze([
        'Each document should be able to have its own settings. I should not need to change the source code every time a document needs different rules.',
        'Scribe should tell me what it did. I want to see which rules were used and what changes were made to each document.',
    ]),

    repl: Object.freeze({
        lead: 'Converting all four sample documents prints one line each. This is what they print now, before anything changes.',
        command: '.venv/bin/scribe check fixtures/',
        before: `handbook.txt: 4 pages, 9 headings, 10 paragraphs, 3 bullets, 0 notes, 12 lines of furniture
memo.txt: 2 pages, 0 headings, 7 paragraphs, 0 bullets, 0 notes, 0 lines of furniture
report.txt: 3 pages, 8 headings, 12 paragraphs, 6 bullets, 2 notes, 6 lines of furniture
survey.txt: 5 pages, 3 headings, 12 paragraphs, 0 bullets, 0 notes, 8 lines of furniture`,
        caption: '"Furniture" is just the project\'s name for repeated page headers and page numbers. If the changes cause more or fewer headers or page numbers to be removed, these numbers will change.',

        after: Object.freeze({
            lead: 'When the agent says it has finished, run these three commands yourself and read what they print.',
            commands: Object.freeze([
                ['.venv/bin/scribe check fixtures/', 'The same four documents as above, counted the same way, so you can hold the two sets of numbers side by side.'],
                ['.venv/bin/scribe convert fixtures/report.txt -', 'Prints one whole converted document to the screen instead of writing a file, so you can read the Markdown the way somebody receiving it would read it.'],
                ['cat scribe.toml', 'The settings file the change added, which is the thing you asked for, so check that the values written in it are the values you wanted.'],
            ]),
            caption: 'Where a number has moved, work out whether it moved because of something you asked for or for some other reason. Read the converted document as well as the counts, because a count can stay the same while what comes out of the conversion changes, and the converted document is the thing somebody would actually read.',
        }),
    }),

    prompt: 'Different documents need different rules, and I should not have to edit the source to convert one properly. Make that configurable, and tell me what the conversion actually did to each document.',
});

const TALLY = Object.freeze({
    oneLine: 'Tally takes a bank statement file and turns it into a simple summary of your spending.',

    why: Object.freeze([
        'A bank export lists every payment separately, in the order it happened. This tells you what happened on each day.',
        'But it does not easily tell you something like "How much did I spend on groceries in January?" Tally answers that question.',
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
        caption: 'The two Tesco payments are combined into one groceries total. The £300 transferred to savings is not included because you did not spend that money. You simply moved it from one account to another.',
    }),

    rules: Object.freeze([
        Object.freeze({
            name: 'It works with different banks',
            what: 'Banks do not all use the same column names or date formats. For example, one bank might call a column Transaction Date, while another might just call it Date. Tally recognises these differences, so you can use exports from different banks.',
        }),
        Object.freeze({
            name: 'It puts payments into categories',
            what: 'Tally looks at the description of each payment and uses the shop or company name to decide what category it belongs to. For example, if the description contains TESCO, Tally treats it as groceries.',
        }),
        Object.freeze({
            name: 'It groups spending by month',
            what: 'A payment is put into the month when you actually made the payment. It does not use the date when the bank finished processing the payment.',
        }),
        Object.freeze({
            name: 'It ignores things that are not really spending',
            what: 'If the same payment appears twice because the bank exported it twice, Tally only counts it once. It also ignores transfers between your own accounts, such as moving money from your current account into your savings account.',
        }),
    ]),

    limits: 'Tally does not connect directly to your bank. You export your transactions from your bank and give that file to Tally. Tally only reports what you spent. It does not tell you whether your spending was sensible or not.',

    failure: Object.freeze({
        lead: 'Tally decides what category a payment belongs to by checking the shop name against a list of known names. That list is currently written directly into the code. For example, Tesco is on the list, so Tesco payments are recognised as groceries. But Waitrose is not on the list.',
        input: `03/01/2026,WAITROSE 220,44.10,Everyday
02/02/2026,WAITROSE 220,51.80,Everyday
03/03/2026,WAITROSE 220,39.95,Everyday`,
        output: `## 2026-01

  utilities            -74.00
  fuel                 -52.00
  uncategorised        -44.10
  subscriptions        -11.99`,
        caption: 'These are all grocery purchases, but Tally does not recognise Waitrose. So they end up under uncategorised. The same thing happens in February and March. As a result, none of those months shows the correct amount spent on groceries.',
    }),

    ask: Object.freeze([
        'I want to manage the list of shops myself without changing the source code.',
        'I want to see my spending in smaller time periods than just a whole month, such as by week.',
    ]),

    repl: Object.freeze({
        lead: 'Summarising all three sample exports prints one line each. This is what they print now, before anything changes.',
        command: '.venv/bin/tally check fixtures/',
        before: `boundary.csv: 7 rows, 3 months, 0 duplicates, 0 transfers, 0 uncategorised, 0 recurring
current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring
other-bank.csv: 13 rows, 3 months, 0 duplicates, 0 transfers, 3 uncategorised, 1 recurring`,
        caption: 'There are three sample files. boundary.csv is the tricky one. Every payment in it was made at the end of one month but processed by the bank at the beginning of the next month. This means the result can change depending on which date Tally uses.',

        after: Object.freeze({
            lead: 'When the agent says it has finished, run these four commands yourself and read what they print.',
            commands: Object.freeze([
                ['.venv/bin/tally check fixtures/', 'The same three exports as above, counted the same way, so you can hold the two sets of numbers side by side.'],
                ['.venv/bin/tally summarise fixtures/current.csv -', 'Prints the whole summary for the largest sample to the screen instead of writing a file, one heading per month with the category rows under it and a total underneath them.'],
                ['.venv/bin/tally summarise fixtures/current.csv - --by-week', 'The same payments grouped into weeks rather than months, which is the second thing you asked for.'],
                ['cat tally/rules.toml', 'The settings file the change added, so check that the categories and the other values written in it are the values you wanted.'],
            ]),
            caption: 'Where a number has moved, work out whether it moved because of something you asked for or for some other reason. Read the summaries themselves as well as the counts, because the counts only say how many rows were read and a summary can be wrong in a way that no count reports.',
        }),
    }),

    prompt: 'I want to manage the categories myself without touching the source, and I want to see spending at a finer grain than a whole month. Make that work.',
});

const TASK = Object.freeze({
    lead: 'You asked your coding agent for the request below, and it is about to start work on it.',
    stage1: 'First the agent reads the project and comes back with what it plans to change, before it changes anything. Read what it plans and answer it.',
    stage2: 'Then it makes the changes. Read what it changed, and decide for each part whether it is a change you would keep.',
    stage3: 'Then run the project yourself, using the commands further down this page, and check that what comes out of it is right. Fix anything that is not.',
});

export const COPY = Object.freeze({
    scribe: SCRIBE,
    tally: TALLY,
    task: TASK,
});
