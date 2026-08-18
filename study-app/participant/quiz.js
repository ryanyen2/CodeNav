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
            "question": "Your change decides which passages become block quotes. What does scribe know about a line that a rule could be based on?",
            "options": [
                {
                    "letter": "a",
                    "text": "Its font and size, which the extracted text records"
                },
                {
                    "letter": "b",
                    "text": "Its text, which page it came from, and where it sat on that page — nothing else"
                },
                {
                    "letter": "c",
                    "text": "Its colour and how far it was indented in the PDF"
                },
                {
                    "letter": "d",
                    "text": "Where it ends up in the finished Markdown"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "A line that your change now puts inside a quote used to be joined into the paragraph around it. Which existing behaviour most likely treats it differently now?",
            "options": [
                {
                    "letter": "a",
                    "text": "The characters in it, which are tidied separately"
                },
                {
                    "letter": "b",
                    "text": "Rejoining paragraphs, because a quote is a block and the prose around it stops flowing into it"
                },
                {
                    "letter": "c",
                    "text": "Which page it is recorded on"
                },
                {
                    "letter": "d",
                    "text": "Whether it counted towards the repeated-line threshold"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "The running header is removed before your change runs. For a quote that carries on across a page break, what does that ordering do?",
            "options": [
                {
                    "letter": "a",
                    "text": "It splits the quote, because the header lands between the two halves"
                },
                {
                    "letter": "b",
                    "text": "It lets the halves join, because the header is gone before quotes are looked for"
                },
                {
                    "letter": "c",
                    "text": "It drops the quote, because removing the header takes the whole block"
                },
                {
                    "letter": "d",
                    "text": "Nothing: the two never see the same lines"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "Suppose you had looked for quotes BEFORE the running header was removed. What would have started going wrong?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: the two are independent"
                },
                {
                    "letter": "b",
                    "text": "Quotes would lose their indentation"
                },
                {
                    "letter": "c",
                    "text": "A quote crossing a page break would have the running header sitting inside it"
                },
                {
                    "letter": "d",
                    "text": "Headings would stop being recognised"
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
            "question": "Your change lets one purchase be split across two categories. Where does a split have to be written down?",
            "options": [
                {
                    "letter": "a",
                    "text": "In a separate file the run is pointed at"
                },
                {
                    "letter": "b",
                    "text": "In the transaction's own row or rows, because that is where every other fact about it lives"
                },
                {
                    "letter": "c",
                    "text": "On the command line, as an argument"
                },
                {
                    "letter": "d",
                    "text": "In the summary, after the fact"
                }
            ]
        },
        {
            "n": 2,
            "band": "Rationale",
            "question": "A purchase that your change splits in two. Which existing rule most likely treats it differently now?",
            "options": [
                {
                    "letter": "a",
                    "text": "The month it is counted in"
                },
                {
                    "letter": "b",
                    "text": "Whether it is recognised as a recurring payment"
                },
                {
                    "letter": "c",
                    "text": "Duplicate removal, because two equal halves on one day at one merchant is exactly the shape it matches"
                },
                {
                    "letter": "d",
                    "text": "The sign convention applied to its amount"
                }
            ]
        },
        {
            "n": 3,
            "band": "Change",
            "question": "Your change decides whether a split counts as one transaction or two. Why is that a decision rather than a detail?",
            "options": [
                {
                    "letter": "a",
                    "text": "It changes how the rows are stored on disk"
                },
                {
                    "letter": "b",
                    "text": "It changes which month the halves land in"
                },
                {
                    "letter": "c",
                    "text": "The number of transactions is in the summary somebody reads, so a loop that settles it by accident still publishes it"
                },
                {
                    "letter": "d",
                    "text": "It changes whether the duplicate rule fires"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "After your change, one half of a split matches a category rule and the other half matches none. What happens?",
            "options": [
                {
                    "letter": "a",
                    "text": "The whole transaction is dropped, because it is ambiguous"
                },
                {
                    "letter": "b",
                    "text": "The whole transaction goes to the uncategorised bucket, because any doubt sends it there"
                },
                {
                    "letter": "c",
                    "text": "Each half is categorised on its own, so only the unmatched half lands in that bucket"
                },
                {
                    "letter": "d",
                    "text": "The run stops and asks which category to use"
                }
            ]
        },
        {
            "n": 5,
            "band": "Extension",
            "question": "Suppose you had made the duplicate check finer — adding a reference — so the two halves stopped matching. What else would that have changed?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: that check is only used for splits"
                },
                {
                    "letter": "b",
                    "text": "Transfers would stop being left out"
                },
                {
                    "letter": "c",
                    "text": "Duplicate detection would loosen for every ordinary transaction, not only for splits"
                },
                {
                    "letter": "d",
                    "text": "The months would all be recomputed"
                }
            ]
        }
    ]
});
