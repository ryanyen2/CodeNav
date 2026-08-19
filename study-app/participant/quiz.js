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
            "question": "A three-page report repeats its section title at the top of every page. It is a real heading, not a running header. What does scribe do with it?",
            "options": [
                {
                    "letter": "a",
                    "text": "Keeps it as a heading, because it is numbered like the others"
                },
                {
                    "letter": "b",
                    "text": "Drops it, because it repeats near the edge of most pages and that is all scribe can see"
                },
                {
                    "letter": "c",
                    "text": "Keeps the first one and drops the repeats"
                },
                {
                    "letter": "d",
                    "text": "Keeps it, and marks the repeats for review"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "A word is split across a line break as `well-` then `being`. What comes out?",
            "options": [
                {
                    "letter": "a",
                    "text": "`wellbeing`, because a hyphen at a line end is the typesetter's, not the writer's"
                },
                {
                    "letter": "b",
                    "text": "`well-being`, because a short list of prefixes is allowed to keep its hyphen"
                },
                {
                    "letter": "c",
                    "text": "`well- being`, because the break is preserved along with the hyphen"
                },
                {
                    "letter": "d",
                    "text": "`well-being`, because a dictionary is consulted for the compound"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "Footnote markers used to be found after any full stop, and the rule was tightened. What was going wrong?",
            "options": [
                {
                    "letter": "a",
                    "text": "A marker at the very end of a paragraph was being missed"
                },
                {
                    "letter": "b",
                    "text": "Two markers next to each other were being read as one"
                },
                {
                    "letter": "c",
                    "text": "Every decimal number in the document was being read as a footnote reference"
                },
                {
                    "letter": "d",
                    "text": "A page number at the foot of a page was being taken for a marker"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "Page furniture is removed before anything looks for headings. What does that ordering cost?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: the two rules never look at the same lines"
                },
                {
                    "letter": "b",
                    "text": "Page numbers can no longer be used to order the sections"
                },
                {
                    "letter": "c",
                    "text": "A real heading that repeats on most pages is gone before the heading rule can see it"
                },
                {
                    "letter": "d",
                    "text": "A heading on the first page is missed, because there is nothing before it to compare against"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "You want the running header kept on a one-page letter but still dropped from a long report. What stands in the way?",
            "options": [
                {
                    "letter": "a",
                    "text": "Markdown has no way to mark a line as a page header"
                },
                {
                    "letter": "b",
                    "text": "Repetition across pages is the only signal there is, and one page cannot show it"
                },
                {
                    "letter": "c",
                    "text": "The header is removed before anything could tell the two documents apart"
                },
                {
                    "letter": "d",
                    "text": "The page number would have to be kept along with it"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "You really did buy the same £3 coffee twice on the same day at the same shop. What does the summary show?",
            "options": [
                {
                    "letter": "a",
                    "text": "Both, because they are two separate purchases"
                },
                {
                    "letter": "b",
                    "text": "One, because nothing in the row tells a real repeat from a repeated row"
                },
                {
                    "letter": "c",
                    "text": "Both, with the second marked as a possible duplicate"
                },
                {
                    "letter": "d",
                    "text": "It stops and asks which one to keep"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "A row whose description mentions a transfer is left out of duplicate removal. Why?",
            "options": [
                {
                    "letter": "a",
                    "text": "Because a transfer is not spending, so it never reaches the totals anyway"
                },
                {
                    "letter": "b",
                    "text": "Because the two legs of a move between your own accounts look exactly like a duplicate"
                },
                {
                    "letter": "c",
                    "text": "Because the two legs arrive on different dates and would never collide"
                },
                {
                    "letter": "d",
                    "text": "Because the bank marks transfers already, so the rule is not needed"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "Amounts are rounded once at the summary rather than on every transaction. What does that give up?",
            "options": [
                {
                    "letter": "a",
                    "text": "Speed, because every exact amount has to be carried until the end"
                },
                {
                    "letter": "b",
                    "text": "Agreeing line by line with a printed receipt"
                },
                {
                    "letter": "c",
                    "text": "Being able to show the totals in another currency"
                },
                {
                    "letter": "d",
                    "text": "Accuracy, because many small amounts drift further apart this way"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "A bank export lists every amount as a positive number, spending included. What does tally do?",
            "options": [
                {
                    "letter": "a",
                    "text": "Refuses the file, because the direction cannot be known from it"
                },
                {
                    "letter": "b",
                    "text": "Leaves the amounts alone and reads the direction from a separate column"
                },
                {
                    "letter": "c",
                    "text": "Takes the file's own shape as the convention and flips every sign"
                },
                {
                    "letter": "d",
                    "text": "Treats the largest amounts as spending and the rest as money coming in"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "You add a rule for one coffee shop, but a broader \"cafe\" rule already matches it. What decides which one applies?",
            "options": [
                {
                    "letter": "a",
                    "text": "The more specific pattern wins, whichever order they are in"
                },
                {
                    "letter": "b",
                    "text": "Where it sits in the list, because the first pattern that matches wins"
                },
                {
                    "letter": "c",
                    "text": "Both apply, and the amount is split between them"
                },
                {
                    "letter": "d",
                    "text": "Neither: two matching rules send it to the uncategorised bucket"
                }
            ]
        }
    ]
});

// Asked straight after the task, closed book, about the change they just made.
export const AFTER_QUIZZES = Object.freeze({
    "scribe": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "You had a config file added. What does scribe now do when it runs with no config file at all?",
            "options": [
                {
                    "letter": "a",
                    "text": "It refuses to run until a config file exists"
                },
                {
                    "letter": "b",
                    "text": "It converts as before, except that a line repeated on two pages is now removed"
                },
                {
                    "letter": "c",
                    "text": "It writes out a config file with the current settings and stops"
                },
                {
                    "letter": "d",
                    "text": "It converts exactly as it did before, with nothing changed"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "You had the settings threaded through the rules instead of read from module constants. Which rule ended up running at a different point because of it?",
            "options": [
                {
                    "letter": "a",
                    "text": "The one that tidies up characters"
                },
                {
                    "letter": "b",
                    "text": "The one that removes what repeats across pages"
                },
                {
                    "letter": "c",
                    "text": "The one that joins a word broken at the end of a line"
                },
                {
                    "letter": "d",
                    "text": "None of them; moving settings around cannot change when a rule runs"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "Besides the three things you had asked for, the agent changed one more rule. Which one?",
            "options": [
                {
                    "letter": "a",
                    "text": "The rule that decides what counts as a heading"
                },
                {
                    "letter": "b",
                    "text": "The rule that numbers the notes collected at the end"
                },
                {
                    "letter": "c",
                    "text": "The rule that collapses runs of blank lines"
                },
                {
                    "letter": "d",
                    "text": "Nothing else changed"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "Your change leaves one pair of rules running in the opposite order for a document that has a config file. What does the new order cost?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing; the two rules never look at the same lines"
                },
                {
                    "letter": "b",
                    "text": "Page numbers can no longer be used to order the sections"
                },
                {
                    "letter": "c",
                    "text": "A real heading that repeats on most pages is removed before the heading rule can see it"
                },
                {
                    "letter": "d",
                    "text": "The first page loses its heading, because there is nothing before it to compare against"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "Someone picks this up tomorrow and adds another rule. What do they have to decide that they would not have to if the rules were independent?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which file to put it in"
                },
                {
                    "letter": "b",
                    "text": "Whether to give its threshold a name"
                },
                {
                    "letter": "c",
                    "text": "Where it goes in the fixed order the stages run in, because each stage sees what the ones before it left"
                },
                {
                    "letter": "d",
                    "text": "Whether to add a sample document for it"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "You had the merchant rules moved into a file you can edit. What does tally now do with money moved between your own accounts?",
            "options": [
                {
                    "letter": "a",
                    "text": "Leaves it out of the totals, as it did before"
                },
                {
                    "letter": "b",
                    "text": "Counts it in the totals, because the new setting arrives switched off"
                },
                {
                    "letter": "c",
                    "text": "Lists it separately at the bottom of the summary"
                },
                {
                    "letter": "d",
                    "text": "Refuses to run until you say which you want"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "You had a weekly view added beside the monthly one. What does the weekly view no longer look at when it decides two rows are the same row twice?",
            "options": [
                {
                    "letter": "a",
                    "text": "The date"
                },
                {
                    "letter": "b",
                    "text": "The amount"
                },
                {
                    "letter": "c",
                    "text": "The merchant"
                },
                {
                    "letter": "d",
                    "text": "The category"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "Besides the three things you had asked for, the agent changed one more rule. Which one?",
            "options": [
                {
                    "letter": "a",
                    "text": "The rule that decides what counts as recurring"
                },
                {
                    "letter": "b",
                    "text": "The rule that decides which month a transaction belongs to"
                },
                {
                    "letter": "c",
                    "text": "The rule that nets refunds against a category"
                },
                {
                    "letter": "d",
                    "text": "Nothing else changed"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "Your change leaves two rules disagreeing about the same pair of rows. Which two?",
            "options": [
                {
                    "letter": "a",
                    "text": "Recurring payments and refunds"
                },
                {
                    "letter": "b",
                    "text": "Leaving out money moved between your own accounts, and removing a row recorded twice"
                },
                {
                    "letter": "c",
                    "text": "Categorising and rounding"
                },
                {
                    "letter": "d",
                    "text": "Month attribution and the sign convention"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "Someone picks this up tomorrow and wants the weekly view to agree with the monthly one again. What do they have to settle first?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which file the weekly code lives in"
                },
                {
                    "letter": "b",
                    "text": "Whether weeks start on Monday or Sunday"
                },
                {
                    "letter": "c",
                    "text": "Which date a transaction belongs to, because the two views answer that differently now"
                },
                {
                    "letter": "d",
                    "text": "Whether to add a sample file for it"
                }
            ]
        }
    ]
});
