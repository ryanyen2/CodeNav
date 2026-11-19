## Open your feature tree

The tree is a document of your codebase's features. Each feature is a heading, then
prose, lists, tables and diagrams that cite the code it is bound to. codoc keeps the
document and the code in step in both directions.

- **Read.** Click a citation to open the code it names. The tree pane on the left
  jumps to a heading.
- **Edit.** Type as in any document. Your words are drawn in blue until you hand them
  off. `#` and a space starts a new feature; Tab and Shift-Tab change a heading's level.
- **Hand off.** ⌘S, or Hand off in the toolbar, turns your edits into a request in
  `.codoc/realize.md`. Your Claude Code session builds it with `/codoc:realize`, or
  **Build now** runs `codoc realize` in a terminal.
- **Comment.** Select words and press Comment. **Build it** on a comment hands off its
  feature with the comment as your request and runs `codoc realize`.
- **Review.** A plan the agent or codoc's loop proposes is drawn in gray in the prose.
  Accept all or Reject all in the toolbar. After a build, green and red show what the
  code added and removed.
- **History** shows who wrote each run of words, and a scrubber over the recent edits.

[Open your feature tree](command:codoc.open)
