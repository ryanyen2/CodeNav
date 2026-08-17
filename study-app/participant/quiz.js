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
            "question": "Jane wants scribe to keep the tables out of a report. Can it?",
            "options": [
                {
                    "letter": "a",
                    "text": "Yes, it rebuilds them from where the columns sat"
                },
                {
                    "letter": "b",
                    "text": "Yes, but only for tables with a header row"
                },
                {
                    "letter": "c",
                    "text": "No: the table is gone before scribe is handed anything"
                },
                {
                    "letter": "d",
                    "text": "No, but it marks the place where a table was"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "Raj makes heading detection stricter, so fewer lines become headings. Which other part of the output changes?",
            "options": [
                {
                    "letter": "a",
                    "text": "Footnotes, because a note number looks like a heading number"
                },
                {
                    "letter": "b",
                    "text": "Character normalising, because heading text is normalised separately"
                },
                {
                    "letter": "c",
                    "text": "The paragraphs, because a line that is no longer a heading joins the prose around it"
                },
                {
                    "letter": "d",
                    "text": "Nothing else: headings are decided line by line and touch nothing else"
                }
            ]
        },
        {
            "n": 3,
            "band": "Purpose",
            "question": "Raj moves the character normalising so it runs first instead of last. What breaks?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: normalising early or late comes to the same thing"
                },
                {
                    "letter": "b",
                    "text": "The footnote markers are normalised away before they can be found"
                },
                {
                    "letter": "c",
                    "text": "Rules that match on the characters as they came out of the PDF stop matching"
                },
                {
                    "letter": "d",
                    "text": "The output keeps its ligatures, because normalising happens before the text exists"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "A word is split across a line break as `photogram-` then `metric`. What comes out?",
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
            "n": 5,
            "band": "Rationale",
            "question": "A word is split as `well-` then `being`. What comes out?",
            "options": [
                {
                    "letter": "a",
                    "text": "`wellbeing`, because the hyphen was the typesetter's"
                },
                {
                    "letter": "b",
                    "text": "`well-being`, because that hyphen belongs to the word"
                },
                {
                    "letter": "c",
                    "text": "`well- being`, leaving the break visible"
                },
                {
                    "letter": "d",
                    "text": "`well-being`, but only if the word appears unbroken elsewhere in the document"
                }
            ]
        },
        {
            "n": 6,
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
            "n": 7,
            "band": "Change",
            "question": "A three-page report has the same line at the top of two of its three pages. What happens to that line?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is kept, because two pages is not a pattern"
                },
                {
                    "letter": "b",
                    "text": "It is dropped: two pages out of three is over the threshold"
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
            "band": "Change",
            "question": "Footnote markers used to be found after any full stop, and that rule was changed. What was going wrong?",
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
                    "text": "Every decimal number in the document was read as a footnote reference"
                },
                {
                    "letter": "d",
                    "text": "A page number at the foot of a page was taken for a marker"
                }
            ]
        },
        {
            "n": 9,
            "band": "Change",
            "question": "Page furniture is removed before headings are looked for. What does that ordering cost?",
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
                    "text": "A real heading that happens to repeat is gone before the heading rule can see it"
                },
                {
                    "letter": "d",
                    "text": "The line count per page is wrong by the time headings are found"
                }
            ]
        },
        {
            "n": 10,
            "band": "Extension",
            "question": "Jane wants scribe to recognise a new kind of block. Where does that go?",
            "options": [
                {
                    "letter": "a",
                    "text": "Into `lines.py`, with the rest of the parsing"
                },
                {
                    "letter": "b",
                    "text": "Into a policy module of its own, and into the order in `convert.py`"
                },
                {
                    "letter": "c",
                    "text": "Into `text.py`, with the other rewriting"
                },
                {
                    "letter": "d",
                    "text": "Anywhere: the rules do not depend on each other"
                }
            ]
        },
        {
            "n": 11,
            "band": "Extension",
            "question": "Two guards stop furniture removal firing on a short document. One is a minimum number of pages. What is the other?",
            "options": [
                {
                    "letter": "a",
                    "text": "A minimum number of words in the repeated line"
                },
                {
                    "letter": "b",
                    "text": "A minimum number of lines on a page, so that being near the edge means something"
                },
                {
                    "letter": "c",
                    "text": "A maximum number of pages, above which it is assumed to be a book"
                },
                {
                    "letter": "d",
                    "text": "There is only one guard"
                }
            ]
        },
        {
            "n": 12,
            "band": "Extension",
            "question": "Jane wants the running header kept on a one-page letter but still dropped from a long report. What stands in the way?",
            "options": [
                {
                    "letter": "a",
                    "text": "By the time anything could tell the two apart, the header has already been removed"
                },
                {
                    "letter": "b",
                    "text": "Nothing scribe can see tells them apart: it is handed text and nothing else"
                },
                {
                    "letter": "c",
                    "text": "Markdown has no way to mark a line as a page header"
                },
                {
                    "letter": "d",
                    "text": "The page numbers would have to be kept along with it"
                }
            ]
        }
    ],
    "tally": [
        {
            "n": 1,
            "band": "Purpose",
            "question": "Jane wants tally to tell her whether she can afford a holiday. Can it?",
            "options": [
                {
                    "letter": "a",
                    "text": "Yes, from the recurring payments and the monthly totals"
                },
                {
                    "letter": "b",
                    "text": "Yes, if she gives it a target to save towards"
                },
                {
                    "letter": "c",
                    "text": "No: it reports what was spent and has no opinion beyond that"
                },
                {
                    "letter": "d",
                    "text": "No, but it flags the months where spending rose"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "Raj makes the transfer rule stricter, so fewer rows count as transfers. Which other rule starts behaving differently?",
            "options": [
                {
                    "letter": "a",
                    "text": "Categorisation, because transfers have no category"
                },
                {
                    "letter": "b",
                    "text": "Rounding, because the totals change"
                },
                {
                    "letter": "c",
                    "text": "Duplicate removal, because transfers are the rows it was told to leave alone"
                },
                {
                    "letter": "d",
                    "text": "Nothing else: the two are applied to different columns"
                }
            ]
        },
        {
            "n": 3,
            "band": "Purpose",
            "question": "Raj moves the sign-flipping step so it runs last instead of first. What breaks?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: flipping signs is the same operation whenever it happens"
                },
                {
                    "letter": "b",
                    "text": "The totals come out positive instead of negative"
                },
                {
                    "letter": "c",
                    "text": "Every rule that reads an amount has already read it the wrong way round"
                },
                {
                    "letter": "d",
                    "text": "Refunds stop netting, because a refund is recognised by its sign"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "A merchant matches no category rule at all. What happens to that transaction?",
            "options": [
                {
                    "letter": "a",
                    "text": "The row is dropped"
                },
                {
                    "letter": "b",
                    "text": "The run stops and asks"
                },
                {
                    "letter": "c",
                    "text": "It is counted in a bucket of its own, which appears in the summary"
                },
                {
                    "letter": "d",
                    "text": "It is guessed at from the amount"
                }
            ]
        },
        {
            "n": 5,
            "band": "Rationale",
            "question": "A payment is made on the 31st of January and posted by the bank on the 2nd of February. Which month does tally put it in?",
            "options": [
                {
                    "letter": "a",
                    "text": "February, the month the bank processed it"
                },
                {
                    "letter": "b",
                    "text": "January, the month it was made"
                },
                {
                    "letter": "c",
                    "text": "Both, split across the boundary"
                },
                {
                    "letter": "d",
                    "text": "February, unless January's summary has already been written"
                }
            ]
        },
        {
            "n": 6,
            "band": "Rationale",
            "question": "A row's merchant matches both the utilities rule and the fuel rule. What happens?",
            "options": [
                {
                    "letter": "a",
                    "text": "It is reported as ambiguous and the run stops"
                },
                {
                    "letter": "b",
                    "text": "Whichever rule is listed first wins"
                },
                {
                    "letter": "c",
                    "text": "It goes to the uncategorised bucket, because the answer is unclear"
                },
                {
                    "letter": "d",
                    "text": "It is counted under both, and the total is adjusted"
                }
            ]
        },
        {
            "n": 7,
            "band": "Change",
            "question": "The same merchant charges £11.99 in each of three months. Does tally call that recurring?",
            "options": [
                {
                    "letter": "a",
                    "text": "No: three months is not long enough to be sure"
                },
                {
                    "letter": "b",
                    "text": "No: only a payment the bank marks as a standing order counts"
                },
                {
                    "letter": "c",
                    "text": "Yes: same merchant, same amount, three months"
                },
                {
                    "letter": "d",
                    "text": "Yes, and it would be recurring at three different amounts too"
                }
            ]
        },
        {
            "n": 8,
            "band": "Change",
            "question": "Transfers are exempted from duplicate removal. What would go wrong without that exemption?",
            "options": [
                {
                    "letter": "a",
                    "text": "Every transfer would be counted twice in the spending"
                },
                {
                    "letter": "b",
                    "text": "The two legs would end up in different months"
                },
                {
                    "letter": "c",
                    "text": "One leg of each transfer would be dropped, and the money would look like it went somewhere it did not"
                },
                {
                    "letter": "d",
                    "text": "Transfers would be categorised as spending"
                }
            ]
        },
        {
            "n": 9,
            "band": "Change",
            "question": "Rounding happens once, at the total, rather than on each row. What does that cost?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: the two come to the same figure"
                },
                {
                    "letter": "b",
                    "text": "A total that does not add up line by line against a printed statement"
                },
                {
                    "letter": "c",
                    "text": "Amounts under a penny are lost"
                },
                {
                    "letter": "d",
                    "text": "The recurring detection stops matching on amount"
                }
            ]
        },
        {
            "n": 10,
            "band": "Extension",
            "question": "Jane's bank exports a column tally does not recognise. What does she change?",
            "options": [
                {
                    "letter": "a",
                    "text": "The CSV itself, to rename the column"
                },
                {
                    "letter": "b",
                    "text": "The list of names in `rows.py` that each field is matched against"
                },
                {
                    "letter": "c",
                    "text": "`summary.py`, where the pipeline runs"
                },
                {
                    "letter": "d",
                    "text": "Nothing: an unknown column is worked out from what is in it"
                }
            ]
        },
        {
            "n": 11,
            "band": "Extension",
            "question": "Adding `shell energy` to the category rules means deciding one thing beyond the pattern itself. What?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which month it starts applying from"
                },
                {
                    "letter": "b",
                    "text": "Where in the list it goes, because the first rule that matches wins"
                },
                {
                    "letter": "c",
                    "text": "Whether it counts as a recurring payment"
                },
                {
                    "letter": "d",
                    "text": "What to do when the amount is positive"
                }
            ]
        },
        {
            "n": 12,
            "band": "Extension",
            "question": "Jane wants a refund to reduce the month the purchase was in. What stands in the way?",
            "options": [
                {
                    "letter": "a",
                    "text": "The refund row does not record which purchase it is for"
                },
                {
                    "letter": "b",
                    "text": "Refunds are recognised by sign, so income would be reduced too"
                },
                {
                    "letter": "c",
                    "text": "A summary for that month may already have been read, and there is no answer for what happens then"
                },
                {
                    "letter": "d",
                    "text": "The two months could be in different files"
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
            "question": "Your change turns some passages into Markdown block quotes. What decides which passages?",
            "options": [
                {
                    "letter": "a",
                    "text": "The words in them, matched against a list"
                },
                {
                    "letter": "b",
                    "text": "Something about their shape on the page, which is all `lines.py` keeps"
                },
                {
                    "letter": "c",
                    "text": "Their font, which the extracted text records"
                },
                {
                    "letter": "d",
                    "text": "Their position in the document, counted from the top"
                }
            ]
        },
        {
            "n": 2,
            "band": "Purpose",
            "question": "A line that is now inside a quote. Which existing behaviour is most likely to treat it differently than before?",
            "options": [
                {
                    "letter": "a",
                    "text": "Whether its ligatures were normalised"
                },
                {
                    "letter": "b",
                    "text": "Which page it is recorded on"
                },
                {
                    "letter": "c",
                    "text": "Whether it was joined into the paragraph around it"
                },
                {
                    "letter": "d",
                    "text": "Whether it counted toward the furniture threshold"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "You chose how a quote is recognised. What makes a rule based on font inconsistent with the rest of scribe, whatever its merits?",
            "options": [
                {
                    "letter": "a",
                    "text": "Markdown cannot express a font"
                },
                {
                    "letter": "b",
                    "text": "It would be slower than the other rules"
                },
                {
                    "letter": "c",
                    "text": "`lines.py` keeps text, page and index and nothing else, so no rule downstream has a font to look at"
                },
                {
                    "letter": "d",
                    "text": "The other rules are all in one file and it would have to be too"
                }
            ]
        },
        {
            "n": 4,
            "band": "Rationale",
            "question": "`furniture.strip` runs before anything looks for quotes. For a quote that runs across a page break, what does that ordering do?",
            "options": [
                {
                    "letter": "a",
                    "text": "It splits the quote, because the running header lands between the halves"
                },
                {
                    "letter": "b",
                    "text": "It joins them, because the running header is gone before quotes are looked for"
                },
                {
                    "letter": "c",
                    "text": "It drops the quote, because furniture removal takes the whole block"
                },
                {
                    "letter": "d",
                    "text": "Nothing: the two rules never see the same lines"
                }
            ]
        },
        {
            "n": 5,
            "band": "Change",
            "question": "Suppose you had put quote detection BEFORE `furniture.strip` instead. What would have started going wrong?",
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
                    "text": "A quote crossing a page break would have the running header inside it"
                },
                {
                    "letter": "d",
                    "text": "Headings would stop being recognised"
                }
            ]
        },
        {
            "n": 6,
            "band": "Extension",
            "question": "Somebody picks this up tomorrow and wants a rule that runs before yours. What do they have to decide that they would not have to in a codebase of independent rules?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which file to put it in"
                },
                {
                    "letter": "b",
                    "text": "Whether to give its threshold a named constant"
                },
                {
                    "letter": "c",
                    "text": "Where in `convert.py`'s fixed order it goes, because the order is load-bearing"
                },
                {
                    "letter": "d",
                    "text": "Whether to write a fixture for it"
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
            "band": "Purpose",
            "question": "A transaction that is now split in two. Which existing rule is most likely to treat it differently than before?",
            "options": [
                {
                    "letter": "a",
                    "text": "The month it is counted in"
                },
                {
                    "letter": "b",
                    "text": "Whether it is recognised as recurring"
                },
                {
                    "letter": "c",
                    "text": "Duplicate removal, because two equal halves on one day at one merchant is the shape it matches"
                },
                {
                    "letter": "d",
                    "text": "The sign convention applied to its amount"
                }
            ]
        },
        {
            "n": 3,
            "band": "Rationale",
            "question": "You decided whether a split counts as one transaction or two. What makes the count a decision rather than an implementation detail?",
            "options": [
                {
                    "letter": "a",
                    "text": "It changes how the rows are stored"
                },
                {
                    "letter": "b",
                    "text": "It changes which month the halves land in"
                },
                {
                    "letter": "c",
                    "text": "The number is in the summary the person reads, so a loop deciding it by accident still publishes it"
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
            "question": "`categorise` sends anything unmatched to `uncategorised`, and `summary` counts that bucket. For a split where one half matches a rule and the other does not, what does that mean?",
            "options": [
                {
                    "letter": "a",
                    "text": "The whole transaction is dropped, because it is ambiguous"
                },
                {
                    "letter": "b",
                    "text": "The whole transaction goes to `uncategorised`, because any doubt sends it there"
                },
                {
                    "letter": "c",
                    "text": "Each half can be categorised on its own, so only the unmatched half lands in the bucket"
                },
                {
                    "letter": "d",
                    "text": "The run stops and asks which category to use"
                }
            ]
        },
        {
            "n": 5,
            "band": "Change",
            "question": "Suppose you had made the duplicate key finer — adding the reference — so the halves stopped matching. What else would that have changed?",
            "options": [
                {
                    "letter": "a",
                    "text": "Nothing: the key is only used for splits"
                },
                {
                    "letter": "b",
                    "text": "Transfers would stop being exempted"
                },
                {
                    "letter": "c",
                    "text": "Duplicate detection would loosen for every ordinary transaction, not just for splits"
                },
                {
                    "letter": "d",
                    "text": "The months would be recomputed"
                }
            ]
        },
        {
            "n": 6,
            "band": "Extension",
            "question": "Somebody adds a new category rule tomorrow. What do they have to decide that they would not have to in a codebase of independent rules?",
            "options": [
                {
                    "letter": "a",
                    "text": "Which file to put it in"
                },
                {
                    "letter": "b",
                    "text": "Whether it applies to refunds"
                },
                {
                    "letter": "c",
                    "text": "Where in `categories.RULES` it goes, because the first rule that matches wins"
                },
                {
                    "letter": "d",
                    "text": "Whether to write a fixture for it"
                }
            ]
        }
    ]
});
