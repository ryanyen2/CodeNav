// The quiz, as the participant sees it.
//
// Generated from the projects' STUDY.md by scripts/extract-questions.mjs, so
// there is one source of truth for the wording. The RIGHT ANSWER IS NOT HERE:
// this file ships to a browser, and a participant who opened the console would
// find it. Marking happens in the dashboard, against its own copy.
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
                    "text": "Reading PDF files"
                },
                {
                    "letter": "b",
                    "text": "Turning text already extracted from a PDF into readable Markdown"
                },
                {
                    "letter": "c",
                    "text": "Converting Markdown into PDF"
                },
                {
                    "letter": "d",
                    "text": "Checking that a PDF's text layer is complete"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "What does scribe assume about its input?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is a PDF file"
                },
                {
                    "letter": "b",
                    "text": "It is Markdown with some errors in it"
                },
                {
                    "letter": "c",
                    "text": "It is plain text with a form feed between pages"
                },
                {
                    "letter": "d",
                    "text": "It is HTML from a PDF viewer"
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
                    "text": "Removing a header repeated on every page"
                },
                {
                    "letter": "b",
                    "text": "Joining a word split across a line break"
                },
                {
                    "letter": "c",
                    "text": "Collecting footnotes at the end"
                },
                {
                    "letter": "d",
                    "text": "Recovering a table's column boundaries"
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
                    "text": "A printer"
                },
                {
                    "letter": "b",
                    "text": "Somebody who will grep, diff and paste it into other things"
                },
                {
                    "letter": "c",
                    "text": "An archive that must preserve the original exactly"
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
            "question": "Why are headings found by their numbering rather than by being short?",
            "options": [
                {
                    "letter": "a",
                    "text": "Numbering is faster to match"
                },
                {
                    "letter": "b",
                    "text": "Short-line matching promoted captions, list items and names"
                },
                {
                    "letter": "c",
                    "text": "Markdown requires numbered headings"
                },
                {
                    "letter": "d",
                    "text": "Because the fixtures all use numbering"
                }
            ]
        },
        {
            "n": 6,
            "band": "Rationale",
            "question": "Why does a hyphen at a line end usually disappear?",
            "options": [
                {
                    "letter": "a",
                    "text": "Markdown does not allow hyphens inside words"
                },
                {
                    "letter": "b",
                    "text": "In a justified column most of them were put there by the typesetter"
                },
                {
                    "letter": "c",
                    "text": "It is faster than checking a dictionary"
                },
                {
                    "letter": "d",
                    "text": "Because the alternative loses the word entirely"
                }
            ]
        },
        {
            "n": 7,
            "band": "Rationale",
            "question": "Why does a document under three pages have no furniture removed?",
            "options": [
                {
                    "letter": "a",
                    "text": "Short documents never have headers"
                },
                {
                    "letter": "b",
                    "text": "It would be too slow on long documents otherwise"
                },
                {
                    "letter": "c",
                    "text": "Under three pages there is no pattern, so a coincidence would be treated as one"
                },
                {
                    "letter": "d",
                    "text": "The page numbers are unreliable"
                }
            ]
        },
        {
            "n": 8,
            "band": "Rationale",
            "question": "Why are ligatures and curly quotes replaced?",
            "options": [
                {
                    "letter": "a",
                    "text": "They are not valid Markdown"
                },
                {
                    "letter": "b",
                    "text": "They render badly in a browser"
                },
                {
                    "letter": "c",
                    "text": "The output is meant to be grepped, diffed and pasted"
                },
                {
                    "letter": "d",
                    "text": "They take more bytes"
                }
            ]
        },
        {
            "n": 9,
            "band": "Change",
            "question": "Footnote markers used to be found after any full stop. Why did that change?",
            "options": [
                {
                    "letter": "a",
                    "text": "It missed markers at the end of a paragraph"
                },
                {
                    "letter": "b",
                    "text": "Every decimal number became a footnote reference"
                },
                {
                    "letter": "c",
                    "text": "Markdown changed its footnote syntax"
                },
                {
                    "letter": "d",
                    "text": "It was too slow on long documents"
                }
            ]
        },
        {
            "n": 10,
            "band": "Change",
            "question": "Why does heading detection look at the following line?",
            "options": [
                {
                    "letter": "a",
                    "text": "To get the heading's depth right"
                },
                {
                    "letter": "b",
                    "text": "To find the section's first paragraph"
                },
                {
                    "letter": "c",
                    "text": "The first line of a wrapped list item looks exactly like a heading"
                },
                {
                    "letter": "d",
                    "text": "To decide how much space to leave"
                }
            ]
        },
        {
            "n": 11,
            "band": "Change",
            "question": "Why does furniture removal run before heading detection, and not after?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is faster that way"
                },
                {
                    "letter": "b",
                    "text": "A running header is often the section title, so it would be promoted on every page"
                },
                {
                    "letter": "c",
                    "text": "Heading detection needs the page numbers gone first"
                },
                {
                    "letter": "d",
                    "text": "The two do not interact; the order is arbitrary"
                }
            ]
        },
        {
            "n": 12,
            "band": "Extension",
            "question": "To keep the running header on a one-off document while still removing it from a report, what has to be decided first?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which Markdown syntax a header should use"
                },
                {
                    "letter": "b",
                    "text": "Whether to read the PDF metadata"
                },
                {
                    "letter": "c",
                    "text": "What distinguishes the two kinds of document, since scribe sees only text"
                },
                {
                    "letter": "d",
                    "text": "Whether to make it a command line flag"
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
