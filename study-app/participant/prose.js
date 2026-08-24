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
            caption: 'Then read the rest of this section. It says exactly what those commands print after the change, which part of it is right, which part of it is wrong, and what to do about the part that is wrong.',

            // What the commands actually print, and the verdict on each line that
            // moved. This used to say only "work out whether a number moved
            // because of something you asked for". Two of the four lines move, the
            // moved digits sit in the middle of a line of seven numbers, and a
            // reader comparing the blocks by eye reads them as identical. The
            // researcher did. So the page states the comparison instead of asking
            // for it.
            expected: Object.freeze({
                lead: 'This is what the first command prints after the change. Two of the four lines have moved. Put it beside the block above.',
                out: `handbook.txt: 4 pages, 9 headings, 10 paragraphs, 3 bullets, 0 notes, 12 lines of furniture
memo.txt: 2 pages, 0 headings, 7 paragraphs, 0 bullets, 0 notes, 0 lines of furniture
report.txt: 3 pages, 8 headings, 14 paragraphs, 6 bullets, 2 notes, 6 lines of furniture
survey.txt: 5 pages, 3 headings, 10 paragraphs, 0 bullets, 0 notes, 10 lines of furniture`,
                moved: Object.freeze([
                    Object.freeze({
                        file: 'survey.txt',
                        what: '12 paragraphs became 10. 8 lines of furniture became 10.',
                        verdict: 'asked',
                        why: 'This is the change you asked for, and it is right. The appendix header sits on 2 of the 5 pages, which was under the share a line had to repeat on. The new settings file lowers that share for this one document, so those 2 lines are removed as furniture now. Two more lines removed is two fewer paragraphs.',
                    }),
                    Object.freeze({
                        file: 'report.txt',
                        what: '12 paragraphs became 14.',
                        verdict: 'wrong',
                        why: 'You asked for nothing about report.txt. This is the mistake, and the rest of this section is about it.',
                    }),
                ]),
            }),

            fault: Object.freeze({
                lead: 'While it was making the settings configurable, the agent also rewrote the order the rules run in. It deleted the step that collects footnotes and folded that work into the step that rejoins paragraphs. The result is that footnotes are not collected at all any more.',
                what: Object.freeze([
                    'The body of the document still points at them. It still says `comparable.[^1]` and `provisional.[^2]`.',
                    'The footnotes themselves are gone from the end of the document. There is no line starting with `[^1]:` anywhere in it.',
                    'The text of each footnote is left where it was, in the middle of the document. One now sits between a bullet list and a heading. The other one splits a sentence in half.',
                    'Those two stranded footnotes are the two extra paragraphs.',
                ]),
                evidenceLead: 'This is the second one, as `.venv/bin/scribe convert fixtures/report.txt -` prints it. One sentence, cut in two by a footnote.',
                evidence: `A cliff can stand for a decade and then lose four metres in one winter, and a mean taken over

2 Provisional flights are excluded from the means in section 3.

too few years will read that winter as a trend.`,
                record: 'The written record still says the opposite. The conversion note beside the document, `fixtures/report.conversion.md`, still says "Gathered 2 footnotes at the end." The check line still says `2 notes`. Both of those count the footnote markers the rule found, not the footnotes it placed, so neither of them reports this.',
                todo: Object.freeze([
                    'Run `.venv/bin/scribe convert fixtures/report.txt -`. Find `[^1]` near the top of what it prints, then go to the end of what it prints and confirm there is no `[^1]:` line there.',
                    'Tell the agent that the footnotes have to go back to the end of the document, and that the settings work it did is fine and should stay.',
                    'Run `.venv/bin/scribe check fixtures/` again. report.txt should be back to 12 paragraphs. survey.txt should stay at 10 paragraphs and 10 lines of furniture, because that part was right.',
                    'Check that anything in the project that describes what the conversion does still matches what it does.',
                ]),
            }),
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
other-bank.csv: 13 rows, 3 months, 0 duplicates, 0 transfers, 3 uncategorised, 1 recurring
checked 3 statements`,
        caption: 'There are three sample files. boundary.csv is the tricky one. Every payment in it was made at the end of one month but processed by the bank at the beginning of the next month. This means the result can change depending on which date Tally uses.',

        after: Object.freeze({
            lead: 'When the agent says it has finished, run these four commands yourself and read what they print.',
            commands: Object.freeze([
                ['.venv/bin/tally check fixtures/', 'The same three exports as above, counted the same way, so you can hold the two sets of numbers side by side.'],
                ['.venv/bin/tally summarise fixtures/current.csv -', 'Prints the whole summary for the largest sample to the screen instead of writing a file, one heading per month with the category rows under it and a total underneath them.'],
                ['.venv/bin/tally summarise fixtures/current.csv - --by-week', 'The same payments grouped into weeks rather than months, which is the second thing you asked for.'],
                ['cat tally/rules.toml', 'The settings file the change added, so check that the categories and the other values written in it are the values you wanted.'],
            ]),
            caption: 'Then read the rest of this section. It says exactly what those commands print after the change, which part of it is right, which part of it is wrong, and what to do about the part that is wrong.',

            // Tally's mistake does not reach these counts at all, so the page has
            // to say so. A participant told to compare numbers, who compares them
            // and finds nothing, reasonably concludes the change was clean.
            expected: Object.freeze({
                lead: 'This is what the first command prints after the change. Read it, but note that the mistake in this project does not show up here at all. It shows up in the summary itself, further down.',
                out: `boundary.csv: 7 rows, 3 months, 0 duplicates, 0 transfers, 0 uncategorised, 0 recurring
current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 4 recurring
other-bank.csv: 13 rows, 3 months, 0 duplicates, 0 transfers, 0 uncategorised, 1 recurring
checked 3 statements, 0 stopped`,
                moved: Object.freeze([
                    Object.freeze({
                        file: 'other-bank.csv',
                        what: '3 uncategorised became 0.',
                        verdict: 'asked',
                        why: 'This is the change you asked for, and it is right. The three shops tally did not recognise are named in the new settings file now, so they land in a category instead of in the uncategorised bucket.',
                    }),
                    Object.freeze({
                        file: 'current.csv',
                        what: '3 recurring became 4.',
                        verdict: 'side',
                        why: 'The monthly transfer to savings is now counted as recurring, because transfers are shown separately from spending now. It is defensible and it is not the mistake.',
                    }),
                ]),
            }),

            fault: Object.freeze({
                lead: 'The mistake is in the summary. The agent stopped printing the uncategorised row under each month heading, but it still counts that money in the total printed underneath. The summary no longer adds up.',
                what: Object.freeze([
                    'A payment tally cannot categorise used to get its own row, so you could see both how much it was and that nobody had classified it.',
                    'That row is gone. The money is still in the total.',
                    'So the rows a person reads and the total under them disagree, and nothing on the page says why.',
                ]),
                evidenceLead: 'This is January, as `.venv/bin/tally summarise fixtures/current.csv -` prints it. Add up the eight rows above the total and compare them with it.',
                evidence: `## 2026-01

  housing             -950.00
  groceries           -104.80
  utilities            -88.00
  fuel                 -61.20
  transport            -43.09
  health                -8.60
  eating out            -4.85
  income              2400.00

  total               1115.46
  transfers           -600.00   (not in the total)`,
                record: 'The eight rows come to 1139.46. The total says 1115.46. The difference of 24.00 is one payment tally could not categorise. The check line still says `1 uncategorised` for this file, so the money is counted and reported everywhere except in the one place somebody would actually read it.',
                todo: Object.freeze([
                    'Run `.venv/bin/tally summarise fixtures/current.csv -`. Add up the rows under `## 2026-01` and compare the answer with the total printed under them.',
                    'Tell the agent that the uncategorised row has to be printed again under every month, and that the settings work and the weekly grouping are fine and should stay.',
                    'Run it again and check that the rows now add up to the total.',
                    'Check that anything in the project that describes what the summary contains still matches what it prints.',
                ]),
            }),
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
