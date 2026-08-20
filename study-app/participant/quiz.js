// The quiz, as the participant sees it.
//
// Simplified for clarity. THE RIGHT ANSWER IS NOT HERE: this file ships to a
// browser. Marking happens in the dashboard, against its own copy.
export const QUIZZES = Object.freeze({
    "scribe": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "A page header appears on every page of a three-page document. What does scribe do with it?",
            "options": [
                {
                    "letter": "a",
                    "text": "Removes it, because it repeats on most pages"
                },
                {
                    "letter": "b",
                    "text": "Keeps it, because it is real text"
                },
                {
                    "letter": "c",
                    "text": "Keeps the first one and removes the rest"
                },
                {
                    "letter": "d",
                    "text": "Turns it into a heading"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "The word \"well-being\" is split across two lines as \"well-\" then \"being\". What does scribe produce?",
            "options": [
                {
                    "letter": "a",
                    "text": "wellbeing, with the hyphen removed"
                },
                {
                    "letter": "b",
                    "text": "well-being, with the hyphen kept"
                },
                {
                    "letter": "c",
                    "text": "well- being, with the line break still there"
                },
                {
                    "letter": "d",
                    "text": "well being, with both the hyphen and line break removed"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "What does scribe do with page numbers?",
            "options": [
                {
                    "letter": "a",
                    "text": "Removes them along with other page furniture"
                },
                {
                    "letter": "b",
                    "text": "Keeps them at the bottom of each page"
                },
                {
                    "letter": "c",
                    "text": "Moves them to the end of the document"
                },
                {
                    "letter": "d",
                    "text": "Turns them into section numbers"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "A heading like \"3.1 Sites\" appears on most pages of a document. What happens to it?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is removed as page furniture, because it repeats on most pages"
                },
                {
                    "letter": "b",
                    "text": "It is kept as a heading, because it is numbered"
                },
                {
                    "letter": "c",
                    "text": "The first one is kept and the repeats are removed"
                },
                {
                    "letter": "d",
                    "text": "It is kept, because furniture removal happens after heading detection"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "A one-page document has a header at the top. Can scribe detect and remove it?",
            "options": [
                {
                    "letter": "a",
                    "text": "No, because the header needs to repeat across pages to be detected"
                },
                {
                    "letter": "b",
                    "text": "Yes, because it is at the top of the page"
                },
                {
                    "letter": "c",
                    "text": "Yes, if you tell scribe what to remove"
                },
                {
                    "letter": "d",
                    "text": "No, because one-page documents are not supported"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "You bought the same £3 coffee twice on the same day at the same shop. What does the summary show?",
            "options": [
                {
                    "letter": "a",
                    "text": "One, because the two rows look identical and one is treated as a duplicate"
                },
                {
                    "letter": "b",
                    "text": "Both, because they are two separate purchases"
                },
                {
                    "letter": "c",
                    "text": "Both, with the second one flagged as a possible duplicate"
                },
                {
                    "letter": "d",
                    "text": "Neither, because duplicates are removed entirely"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "You move £300 from your current account to your savings account. How does tally treat this?",
            "options": [
                {
                    "letter": "a",
                    "text": "Leaves it out, because it is a transfer, not spending"
                },
                {
                    "letter": "b",
                    "text": "Counts it as spending in a transfers category"
                },
                {
                    "letter": "c",
                    "text": "Counts it as spending, because it left the current account"
                },
                {
                    "letter": "d",
                    "text": "Asks you whether to include it"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "You make a payment on the last day of January. The bank processes it on the first day of February. Which month does the summary put it in?",
            "options": [
                {
                    "letter": "a",
                    "text": "January, because tally uses the date you made the payment"
                },
                {
                    "letter": "b",
                    "text": "February, because tally uses the date the bank processed it"
                },
                {
                    "letter": "c",
                    "text": "Both months, split equally"
                },
                {
                    "letter": "d",
                    "text": "Whichever month the bank says"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "A shop name does not match anything on the list. What happens to that payment?",
            "options": [
                {
                    "letter": "a",
                    "text": "It goes under uncategorised"
                },
                {
                    "letter": "b",
                    "text": "It is left out of the summary"
                },
                {
                    "letter": "c",
                    "text": "Tally stops and reports an error"
                },
                {
                    "letter": "d",
                    "text": "It is put in the category closest to its name"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "The list of shop names is written into the code. What is the problem with that?",
            "options": [
                {
                    "letter": "a",
                    "text": "You have to change the code to add a new shop"
                },
                {
                    "letter": "b",
                    "text": "The list cannot be shared with other people"
                },
                {
                    "letter": "c",
                    "text": "The list is too slow to search"
                },
                {
                    "letter": "d",
                    "text": "The list cannot handle shops with similar names"
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
            "question": "Your change added a report beside the Markdown output. What does the report show?",
            "options": [
                {
                    "letter": "a",
                    "text": "What scribe changed: lines removed, words rejoined, and notes moved"
                },
                {
                    "letter": "b",
                    "text": "How long each step of the conversion took"
                },
                {
                    "letter": "c",
                    "text": "The parts of the document scribe could not handle"
                },
                {
                    "letter": "d",
                    "text": "A comparison of the original and converted text side by side"
                }
            ]
        },
        {
            "n": 2,
            "band": "Extension",
            "question": "Where are the conversion settings stored now?",
            "options": [
                {
                    "letter": "a",
                    "text": "In a settings file that scribe looks for near the document"
                },
                {
                    "letter": "b",
                    "text": "In the code, where they were before"
                },
                {
                    "letter": "c",
                    "text": "On the command line, given on every run"
                },
                {
                    "letter": "d",
                    "text": "In an environment variable"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "A document has no settings file. What happens when you convert it?",
            "options": [
                {
                    "letter": "a",
                    "text": "It works the same as before, using the default rules"
                },
                {
                    "letter": "b",
                    "text": "Scribe refuses to convert it"
                },
                {
                    "letter": "c",
                    "text": "It skips all the rules and just joins lines"
                },
                {
                    "letter": "d",
                    "text": "It creates a settings file with empty values"
                }
            ]
        },
        {
            "n": 4,
            "band": "Change",
            "question": "What did the change do to the furniture threshold — the share of pages a header has to appear on before it is removed?",
            "options": [
                {
                    "letter": "a",
                    "text": "Moved it into the settings file so you can change it per document"
                },
                {
                    "letter": "b",
                    "text": "Kept it the same but made it stricter"
                },
                {
                    "letter": "c",
                    "text": "Removed it, so all repeated lines are removed"
                },
                {
                    "letter": "d",
                    "text": "Did not change it at all"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "If you lower the furniture threshold so that fewer repeats are needed, what else could that affect?",
            "options": [
                {
                    "letter": "a",
                    "text": "A real heading that repeats across pages could be removed as furniture"
                },
                {
                    "letter": "b",
                    "text": "Nothing, because furniture and headings are completely separate"
                },
                {
                    "letter": "c",
                    "text": "Page numbers would stop being removed"
                },
                {
                    "letter": "d",
                    "text": "The document would get longer"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "Your change added a weekly view. What does each week show?",
            "options": [
                {
                    "letter": "a",
                    "text": "A breakdown by category and a total, the same as a month gets"
                },
                {
                    "letter": "b",
                    "text": "Only a total, with no breakdown"
                },
                {
                    "letter": "c",
                    "text": "One line for each transaction"
                },
                {
                    "letter": "d",
                    "text": "The difference from the previous week"
                }
            ]
        },
        {
            "n": 2,
            "band": "Extension",
            "question": "Where does a colleague add a rule for a new shop now?",
            "options": [
                {
                    "letter": "a",
                    "text": "In the settings file, which is where the rules now live"
                },
                {
                    "letter": "b",
                    "text": "In the code, in the same list as before"
                },
                {
                    "letter": "c",
                    "text": "On the command line, on every run"
                },
                {
                    "letter": "d",
                    "text": "In the bank export file itself"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "A shop on the statement matches no rule in the settings file. What happens?",
            "options": [
                {
                    "letter": "a",
                    "text": "It goes under uncategorised"
                },
                {
                    "letter": "b",
                    "text": "The run stops with an error"
                },
                {
                    "letter": "c",
                    "text": "It is left out of the summary entirely"
                },
                {
                    "letter": "d",
                    "text": "It is matched to the closest rule"
                }
            ]
        },
        {
            "n": 4,
            "band": "Change",
            "question": "The monthly view uses the date you made the payment. What date does the weekly view use?",
            "options": [
                {
                    "letter": "a",
                    "text": "The same date — when you made the payment"
                },
                {
                    "letter": "b",
                    "text": "The date the bank processed the payment"
                },
                {
                    "letter": "c",
                    "text": "Whichever date comes first"
                },
                {
                    "letter": "d",
                    "text": "The date is not used — it just counts seven days"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "If the monthly and weekly views use different dates, what can happen?",
            "options": [
                {
                    "letter": "a",
                    "text": "A payment could appear in January in the monthly view but February in the weekly view"
                },
                {
                    "letter": "b",
                    "text": "Nothing, because they always add up to the same total"
                },
                {
                    "letter": "c",
                    "text": "Some payments would be counted twice"
                },
                {
                    "letter": "d",
                    "text": "Weekend payments would be left out"
                }
            ]
        }
    ]
});
