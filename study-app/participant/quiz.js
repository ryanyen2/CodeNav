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
            "question": "Your change writes a second file beside the Markdown. What is in it?",
            "options": [
                {
                    "letter": "a",
                    "text": "What the conversion did to the document, rule by rule, and the settings the run used"
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
                    "text": "The original text and the converted text side by side"
                }
            ]
        },
        {
            "n": 2,
            "band": "Extension",
            "question": "A colleague wants one awkward document converted with different rules from the rest. Where do they put that now?",
            "options": [
                {
                    "letter": "a",
                    "text": "In a section for that document in the settings file, scribe.toml"
                },
                {
                    "letter": "b",
                    "text": "In the code, next to the rule they want to change"
                },
                {
                    "letter": "c",
                    "text": "On the command line, every time they convert it"
                },
                {
                    "letter": "d",
                    "text": "Nowhere, because every document is converted with the same rules"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "There is no settings file anywhere near the document. What happens when you convert it?",
            "options": [
                {
                    "letter": "a",
                    "text": "It converts using the values scribe has always used"
                },
                {
                    "letter": "b",
                    "text": "Scribe refuses to convert until a settings file exists"
                },
                {
                    "letter": "c",
                    "text": "Scribe writes a settings file full of empty values and carries on"
                },
                {
                    "letter": "d",
                    "text": "Scribe converts the document and skips every rule"
                }
            ]
        },
        {
            "n": 4,
            "band": "Change",
            "question": "Before the change, a footnote came out as a marker in the sentence and a matching `[^1]: ...` line at the end of the Markdown. What comes out now?",
            "options": [
                {
                    "letter": "a",
                    "text": "The marker in the sentence, and the note text as an ordinary paragraph where it sat on the page"
                },
                {
                    "letter": "b",
                    "text": "The marker and the matching line at the end, exactly as before"
                },
                {
                    "letter": "c",
                    "text": "Neither the marker nor the note text, because notes are dropped"
                },
                {
                    "letter": "d",
                    "text": "The line at the end, with the marker taken out of the sentence"
                }
            ]
        },
        {
            "n": 5,
            "band": "Change",
            "question": "Somebody opens the converted report in a Markdown reader and clicks the `[^1]` in the second paragraph. What happens?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing, because the document no longer contains a `[^1]:` line for it to jump to"
                },
                {
                    "letter": "b",
                    "text": "It jumps to the note text further down the document"
                },
                {
                    "letter": "c",
                    "text": "It jumps to the end of the document, where the notes are gathered"
                },
                {
                    "letter": "d",
                    "text": "The reader shows the note in a tooltip, because the marker carries the text with it"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "Your change added a weekly view. What does one week show?",
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
                    "text": "The difference from the week before"
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
                    "text": "In the settings file, which is where the merchant rules now live"
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
            "question": "A shop on the statement matches no rule in the settings file. What does the summary do with that payment, as the project is set up now?",
            "options": [
                {
                    "letter": "a",
                    "text": "Counts it in the month's total and in the count line at the end, without printing it in any category row"
                },
                {
                    "letter": "b",
                    "text": "Prints it as an uncategorised row alongside the other categories"
                },
                {
                    "letter": "c",
                    "text": "Leaves it out of the summary and out of the total"
                },
                {
                    "letter": "d",
                    "text": "Stops the run and lists the shop it could not match"
                }
            ]
        },
        {
            "n": 4,
            "band": "Change",
            "question": "You already have a monthly summary written out for a statement, and you run the same command again with `--by-week`. What happens to the monthly file?",
            "options": [
                {
                    "letter": "a",
                    "text": "It stays where it is, and the weekly summary is written beside it under a different name"
                },
                {
                    "letter": "b",
                    "text": "It is overwritten, because both summaries are written to the same file"
                },
                {
                    "letter": "c",
                    "text": "It is deleted, because a weekly summary replaces a monthly one"
                },
                {
                    "letter": "d",
                    "text": "Nothing is written at all, because `--by-week` only prints to the screen"
                }
            ]
        },
        {
            "n": 5,
            "band": "Change",
            "question": "You add up the category rows printed under a month heading, and you compare your figure with the total printed underneath them. What do you find?",
            "options": [
                {
                    "letter": "a",
                    "text": "The two disagree, by exactly the amount that went to shops matching no rule"
                },
                {
                    "letter": "b",
                    "text": "They agree, because every row under the heading is counted in the total"
                },
                {
                    "letter": "c",
                    "text": "They disagree, because transfers are in the total but have no row of their own"
                },
                {
                    "letter": "d",
                    "text": "They disagree by a penny or two, because each row is rounded before the total is added up"
                }
            ]
        }
    ]
});
