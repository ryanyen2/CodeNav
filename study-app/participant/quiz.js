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
            "question": "You had a short report written beside the Markdown. What does it list?",
            "options": [
                {
                    "letter": "a",
                    "text": "How long each rule took to run"
                },
                {
                    "letter": "b",
                    "text": "The original text, with everything the conversion removed crossed out"
                },
                {
                    "letter": "c",
                    "text": "The lines it removed, the words it rejoined, and the notes it moved"
                },
                {
                    "letter": "d",
                    "text": "The parts of the document the conversion could not handle"
                }
            ]
        },
        {
            "n": 2,
            "band": "Extension",
            "question": "You had the rules' settings taken out of the code. Where are they set now?",
            "options": [
                {
                    "letter": "a",
                    "text": "In a settings file that scribe looks for near the document"
                },
                {
                    "letter": "b",
                    "text": "In each rule module, at the top, as before"
                },
                {
                    "letter": "c",
                    "text": "On the command line, given again on every run"
                },
                {
                    "letter": "d",
                    "text": "In an environment variable read when the program starts"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "You had the keep-hyphen prefix list moved into the config. What happens to a word broken at the end of a line in a document that has no config file?",
            "options": [
                {
                    "letter": "a",
                    "text": "It keeps its hyphen, exactly as before"
                },
                {
                    "letter": "b",
                    "text": "It loses its hyphen, because the list of prefixes that keep one is now empty by default"
                },
                {
                    "letter": "c",
                    "text": "The line break is kept along with the hyphen"
                },
                {
                    "letter": "d",
                    "text": "The run refuses until the document says which it wants"
                }
            ]
        },
        {
            "n": 4,
            "band": "Change",
            "question": "The report you had asked for lists the notes it moved, and says the marker beside each is the one to search for in the Markdown. For a two-page document with one note on each page, what does it print?",
            "options": [
                {
                    "letter": "a",
                    "text": "`[^1]` and `[^2]`, which is what the Markdown holds"
                },
                {
                    "letter": "b",
                    "text": "`[^1]` beside both, so the marker does not tell them apart"
                },
                {
                    "letter": "c",
                    "text": "No markers at all, only the text of each note"
                },
                {
                    "letter": "d",
                    "text": "One entry, because the two notes are treated as the same note"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "Your change lowered the share of pages a line has to appear on before it counts as page furniture. What else does that affect?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing; furniture and headings never look at the same lines"
                },
                {
                    "letter": "b",
                    "text": "Page numbers can no longer be used to order the sections"
                },
                {
                    "letter": "c",
                    "text": "A real heading that repeats across the document is removed before the heading rule sees it, and that now happens to more documents"
                },
                {
                    "letter": "d",
                    "text": "The first page loses its heading, because there is nothing before it to compare against"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "You had a weekly view added beside the monthly one. What does it show for each week?",
            "options": [
                {
                    "letter": "a",
                    "text": "Only a total, with no breakdown"
                },
                {
                    "letter": "b",
                    "text": "One line for each transaction, in date order"
                },
                {
                    "letter": "c",
                    "text": "The difference from the week before, as a percentage"
                },
                {
                    "letter": "d",
                    "text": "A breakdown by category and a total, the same as a month gets"
                }
            ]
        },
        {
            "n": 2,
            "band": "Extension",
            "question": "You had the merchant rules taken out of the code. Where does a colleague add a rule for a new shop now?",
            "options": [
                {
                    "letter": "a",
                    "text": "In the settings file, which is where every rule now lives"
                },
                {
                    "letter": "b",
                    "text": "In the code, in the list of rules, as before"
                },
                {
                    "letter": "c",
                    "text": "On the command line, giving the shop and the category on every run"
                },
                {
                    "letter": "d",
                    "text": "In the statement itself, by editing what the shop is called"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "In the weekly view you had added, what does tally no longer look at when it decides two rows are the same row twice?",
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
            "n": 4,
            "band": "Change",
            "question": "You had the merchant rules moved into a file you can edit. What happens now when a shop on the statement matches no rule in that file?",
            "options": [
                {
                    "letter": "a",
                    "text": "It goes to the uncategorised bucket, and the run finishes"
                },
                {
                    "letter": "b",
                    "text": "The run stops, and no summary is written at all"
                },
                {
                    "letter": "c",
                    "text": "It is filed under the rule whose wording is closest to it"
                },
                {
                    "letter": "d",
                    "text": "It is left out, and the rest of the summary still prints"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "Your change lines the weekly view up on the date the bank posted a transaction, while the monthly view still files it by the date it was made. What else does that affect?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing, because the two views count the same transactions either way"
                },
                {
                    "letter": "b",
                    "text": "A transaction the bank has not posted yet can no longer appear at all"
                },
                {
                    "letter": "c",
                    "text": "One transaction can land in January in the monthly view and in February in the weekly one, so the two summaries of one statement disagree"
                },
                {
                    "letter": "d",
                    "text": "Transactions made at a weekend are left out, because the bank posts them later"
                }
            ]
        }
    ]
});
