// The quiz, as the participant sees it.
//
// Generated from the projects' STUDY.md, so there is one source of truth for the
// wording. THE RIGHT ANSWER IS NOT HERE: this file ships to a browser. Marking
// happens in the dashboard, against its own copy.
//
// Do not edit by hand. Run: npm run questions
export const QUIZZES = Object.freeze({
    "scribe": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "What is scribe for?",
            "options": [
                {
                    "letter": "a",
                    "text": "Pulling the text layer out of a PDF file"
                },
                {
                    "letter": "b",
                    "text": "Turning text already extracted from a PDF into readable Markdown"
                },
                {
                    "letter": "c",
                    "text": "Converting a PDF to HTML and then to Markdown"
                },
                {
                    "letter": "d",
                    "text": "Tidying up Markdown that somebody wrote by hand"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "What does scribe expect to be handed?",
            "options": [
                {
                    "letter": "a",
                    "text": "A PDF file"
                },
                {
                    "letter": "b",
                    "text": "One text file per page"
                },
                {
                    "letter": "c",
                    "text": "One text file, with a form feed between pages"
                },
                {
                    "letter": "d",
                    "text": "Text with each page's number on a line of its own"
                }
            ]
        },
        {
            "n": 3,
            "band": "Purpose",
            "question": "Which of these is out of scope for scribe?",
            "options": [
                {
                    "letter": "a",
                    "text": "Dropping a header repeated on every page"
                },
                {
                    "letter": "b",
                    "text": "Joining a word split across a line break"
                },
                {
                    "letter": "c",
                    "text": "Working out where a table's columns were"
                },
                {
                    "letter": "d",
                    "text": "Gathering footnotes at the end of the document"
                }
            ]
        },
        {
            "n": 4,
            "band": "Purpose",
            "question": "Who is the output written for?",
            "options": [
                {
                    "letter": "a",
                    "text": "An archive that has to preserve the original exactly"
                },
                {
                    "letter": "b",
                    "text": "Somebody who will search it, diff it and paste it elsewhere"
                },
                {
                    "letter": "c",
                    "text": "A typesetter laying the document out again"
                },
                {
                    "letter": "d",
                    "text": "A screen reader"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "A word is split across a line break as `well-` then `being`. What comes out?",
            "options": [
                {
                    "letter": "a",
                    "text": "`wellbeing`, because the hyphen was the typesetter's"
                },
                {
                    "letter": "b",
                    "text": "`well-being`, because the hyphen is part of the word"
                },
                {
                    "letter": "c",
                    "text": "`well- being`, leaving the break visible"
                },
                {
                    "letter": "d",
                    "text": "`well-being` only if the word appears elsewhere in the document unbroken"
                }
            ]
        },
        {
            "n": 6,
            "band": "Rationale",
            "question": "A word is split as `photogram-` then `metric`. What comes out?",
            "options": [
                {
                    "letter": "a",
                    "text": "`photogram-metric`, keeping the hyphen"
                },
                {
                    "letter": "b",
                    "text": "`photogram metric`, as two words"
                },
                {
                    "letter": "c",
                    "text": "`photogrammetric`, joined with the hyphen dropped"
                },
                {
                    "letter": "d",
                    "text": "It is left as it was, because the word is not in the exception list"
                }
            ]
        },
        {
            "n": 7,
            "band": "Rationale",
            "question": "A three-page report has the same line at the top of two of its pages. What happens to it?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is kept, because two pages is not a pattern"
                },
                {
                    "letter": "b",
                    "text": "It is dropped, because two of three is over the threshold"
                },
                {
                    "letter": "c",
                    "text": "It is kept on the first page and dropped on the second"
                },
                {
                    "letter": "d",
                    "text": "It is dropped only if a page number appears with it"
                }
            ]
        },
        {
            "n": 8,
            "band": "Rationale",
            "question": "A line reads `3. We asked each participant to describe what they had understood.` What does scribe make of it?",
            "options": [
                {
                    "letter": "a",
                    "text": "A second-level heading, from the numbering"
                },
                {
                    "letter": "b",
                    "text": "A heading, but only if a blank line follows"
                },
                {
                    "letter": "c",
                    "text": "Not a heading: it is too long and it ends in a full stop"
                },
                {
                    "letter": "d",
                    "text": "A numbered list item, rendered as a bullet"
                }
            ]
        },
        {
            "n": 9,
            "band": "Change",
            "question": "Footnote markers used to be found after any full stop. What went wrong?",
            "options": [
                {
                    "letter": "a",
                    "text": "A marker at the very end of a paragraph was missed"
                },
                {
                    "letter": "b",
                    "text": "Two markers next to each other were read as one"
                },
                {
                    "letter": "c",
                    "text": "Every decimal number in the document became a footnote reference"
                },
                {
                    "letter": "d",
                    "text": "A page number at the foot of a page was taken for a marker"
                }
            ]
        },
        {
            "n": 10,
            "band": "Change",
            "question": "Page furniture is removed before headings are found. What does that cost?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: the two rules do not interact"
                },
                {
                    "letter": "b",
                    "text": "Page numbers can no longer be used to order the sections"
                },
                {
                    "letter": "c",
                    "text": "A real heading that happens to repeat is gone before anything can rescue it"
                },
                {
                    "letter": "d",
                    "text": "The line count per page is wrong by the time headings are found"
                }
            ]
        },
        {
            "n": 11,
            "band": "Change",
            "question": "A document of four lines has a repeated first line. Is it removed?",
            "options": [
                {
                    "letter": "a",
                    "text": "Yes, repetition is repetition"
                },
                {
                    "letter": "b",
                    "text": "No: the page is too short for anything to count as being in the margin"
                },
                {
                    "letter": "c",
                    "text": "Yes, but only if it is also on the last page"
                },
                {
                    "letter": "d",
                    "text": "No, because a four-line document has only one page"
                }
            ]
        },
        {
            "n": 12,
            "band": "Extension",
            "question": "To keep the running header on a one-off document while still dropping it from a report, what has to be settled first?",
            "options": [
                {
                    "letter": "a",
                    "text": "Whether the header becomes a heading or ordinary text"
                },
                {
                    "letter": "b",
                    "text": "How to keep the page numbers while dropping the header"
                },
                {
                    "letter": "c",
                    "text": "What tells the two kinds of document apart, given that scribe sees only text"
                },
                {
                    "letter": "d",
                    "text": "Where the setting that turns it off should live"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "What is tally for?",
            "options": [
                {
                    "letter": "a",
                    "text": "Connecting to a bank and downloading transactions"
                },
                {
                    "letter": "b",
                    "text": "Turning a CSV of transactions into a summary of what was spent, by month and category"
                },
                {
                    "letter": "c",
                    "text": "Checking a statement for fraud"
                },
                {
                    "letter": "d",
                    "text": "Preparing a tax return"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "What does tally assume about its input?",
            "options": [
                {
                    "letter": "a",
                    "text": "It comes from one particular bank"
                },
                {
                    "letter": "b",
                    "text": "It is already sorted by date"
                },
                {
                    "letter": "c",
                    "text": "It is a CSV whose columns may be named any of several ways"
                },
                {
                    "letter": "d",
                    "text": "It has been checked for errors first"
                }
            ]
        },
        {
            "n": 3,
            "band": "Purpose",
            "question": "Which of these is out of scope?",
            "options": [
                {
                    "letter": "a",
                    "text": "Deciding which month a transaction belongs to"
                },
                {
                    "letter": "b",
                    "text": "Spotting a payment that comes round every month"
                },
                {
                    "letter": "c",
                    "text": "Leaving out money moved between your own accounts"
                },
                {
                    "letter": "d",
                    "text": "Telling you whether you can afford something"
                }
            ]
        },
        {
            "n": 4,
            "band": "Purpose",
            "question": "Who is the output for?",
            "options": [
                {
                    "letter": "a",
                    "text": "An accountant reconciling against the bank"
                },
                {
                    "letter": "b",
                    "text": "The person whose statement it is, asking what they spent"
                },
                {
                    "letter": "c",
                    "text": "A budgeting app that will import it"
                },
                {
                    "letter": "d",
                    "text": "A tax authority"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "Why does the first matching category rule win, rather than requiring exactly one?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is faster"
                },
                {
                    "letter": "b",
                    "text": "Banks guarantee only one will match"
                },
                {
                    "letter": "c",
                    "text": "Requiring one would stop the whole summary over a single ambiguous merchant"
                },
                {
                    "letter": "d",
                    "text": "The rules are guaranteed not to overlap"
                }
            ]
        },
        {
            "n": 6,
            "band": "Rationale",
            "question": "Why is a transaction filed under the date it was made rather than posted?",
            "options": [
                {
                    "letter": "a",
                    "text": "The posting date is often missing"
                },
                {
                    "letter": "b",
                    "text": "A card payment on the 31st can post on the 2nd, and the summary should match what the person remembers doing"
                },
                {
                    "letter": "c",
                    "text": "It is what the bank's statement does"
                },
                {
                    "letter": "d",
                    "text": "Posting dates are unreliable across banks"
                }
            ]
        },
        {
            "n": 7,
            "band": "Rationale",
            "question": "Why does recurring detection need the amount to match, not just the merchant?",
            "options": [
                {
                    "letter": "a",
                    "text": "Merchant names change between statements"
                },
                {
                    "letter": "b",
                    "text": "Merchant alone calls a supermarket recurring, which is true and useless"
                },
                {
                    "letter": "c",
                    "text": "Amounts are easier to compare than text"
                },
                {
                    "letter": "d",
                    "text": "To avoid matching refunds"
                }
            ]
        },
        {
            "n": 8,
            "band": "Rationale",
            "question": "Why does rounding happen at the total rather than on each row?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is faster"
                },
                {
                    "letter": "b",
                    "text": "Decimals cannot be rounded twice"
                },
                {
                    "letter": "c",
                    "text": "A hundred small transactions would accumulate a hundred small errors"
                },
                {
                    "letter": "d",
                    "text": "The bank rounds that way"
                }
            ]
        },
        {
            "n": 9,
            "band": "Change",
            "question": "Why does `drop_duplicates` treat transfers differently?",
            "options": [
                {
                    "letter": "a",
                    "text": "Transfers are not real spending"
                },
                {
                    "letter": "b",
                    "text": "A transfer between your own accounts is two rows that look exactly like a duplicate"
                },
                {
                    "letter": "c",
                    "text": "Transfers have no category"
                },
                {
                    "letter": "d",
                    "text": "Banks export them twice by mistake"
                }
            ]
        },
        {
            "n": 10,
            "band": "Change",
            "question": "Why does `sign_convention` guess rather than ask?",
            "options": [
                {
                    "letter": "a",
                    "text": "Asking is impossible in a command line tool"
                },
                {
                    "letter": "b",
                    "text": "The guess is always right"
                },
                {
                    "letter": "c",
                    "text": "The tool is for one person's own statements, where the convention never changes"
                },
                {
                    "letter": "d",
                    "text": "The bank does not say which way round it is"
                }
            ]
        },
        {
            "n": 11,
            "band": "Change",
            "question": "Why does `COLUMNS` list \"transaction date\" before \"date\"?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is alphabetical"
                },
                {
                    "letter": "b",
                    "text": "A bank exporting both would otherwise give the posting date and shift every month-end transaction"
                },
                {
                    "letter": "c",
                    "text": "\"date\" is a reserved word"
                },
                {
                    "letter": "d",
                    "text": "The order does not matter; it is arbitrary"
                }
            ]
        },
        {
            "n": 12,
            "band": "Extension",
            "question": "To make a refund reduce the month the purchase was in, rather than the month the refund arrived, what has to be decided first?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which category the refund belongs to"
                },
                {
                    "letter": "b",
                    "text": "Whether refunds should be positive or negative"
                },
                {
                    "letter": "c",
                    "text": "What happens when the refund arrives after that month's summary has already been read"
                },
                {
                    "letter": "d",
                    "text": "Whether to store the original purchase's date"
                }
            ]
        }
    ]
});
