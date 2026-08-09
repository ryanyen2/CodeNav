# codoc demo video — storyboard

> ## ⚠️ Superseded — do not shoot this as written
>
> **Scene 6 demonstrates a retired input path.** Typing a `> …` blockquote into a
> description no longer creates a steering comment; that text channel died in U7
> when the webview stopped writing `tree.codoc` (`loop_b.py` step 2.7). A `> ` line
> is now ordinary prose and will produce no directive. Steering notes are authored
> through the inline-comment surface (select prose → comment bubble → composer).
>
> The replacement scenario is
> [`docs/paper/uist-walkthrough-storyboard.md`](../docs/paper/uist-walkthrough-storyboard.md)
> — a maintainer whose retry policy drifted across three files while their design
> doc stayed frozen. It leads with the beat this one lacks: the document detecting
> that it has gone stale.
>
> Keep this file for the shot list and the scene-to-paper-section mapping at the
> bottom, both of which still hold.

Scenario: the UIST paper's running example. Maya inherits `mini-coding-agent`
(a small local coding agent) and has to make its Ollama model calls resilient
to a flaky server, in code she hasn't read.

Workspace: `/Users/ryanyen2/repos/test-workspace/mini-coding-agent` — already
reset to a clean pre-fix state (no retry logic, 19 passing tests, fresh
25-feature tree, Edit/Write permissions pre-granted so recording won't hit
permission prompts).

## Before you hit record

- [ ] Open a **fresh VS Code window** on the workspace above (the extension
  auto-starts the daemon on activation — you never run `codoc watch` by hand).
- [ ] Open `.codoc/tree.codoc` — it should open in the **Codoc Tree** editor by
  default. If it opens as raw text, right-click → "Reopen Editor With..." →
  Codoc Tree.
- [ ] Close unrelated panes so the layout is: Codoc Tree (left/full) → will
  split to show code (right) once you touch a feature's prose.
- [ ] Have a terminal + the Claude Code panel ready for the "implement" scene.
- [ ] **Contingency for Scene 9** (the new-feature proposal): whether Loop A
  proposes a standalone "retry helper" feature depends on how the agent
  implements the fix — if it inlines the retry loop into the existing method,
  there's nothing new to propose. If you get to Scene 9 and there's no ghost
  row, fall back to running `codoc propose add_node --title "Retry-with-backoff
  helper" --description "..." --parent <ollama-client-feature-id>` in a
  terminal right before recording that beat — it produces the same ghost row
  and Accept flow.

## Storyboard

| # | Do this | What's on screen | Say / highlight |
|---|---|---|---|
| 1 | Open `tree.codoc` in the Codoc Tree editor. Don't touch anything yet. | The whole feature outline: Core agent engine → tool-use loop, model parsing, tool dispatch, sandbox guard; Model backends; CLI; Tests. | "This is codoc's outline of the codebase — not written by a person, built from the code itself. It's a map of *intent*, one node per feature." |
| 2 | Scroll to "Model backends and clients" → "Ollama model backend." Click the `[OllamaModelClient](codoc:...)` code link in its description. | Editor splits; `mini_coding_agent.py` opens beside the tree, jumped straight to the `OllamaModelClient` class. | "One click, no search — every feature links to the exact code that implements it." |
| 3 | Point at the line just above `class OllamaModelClient:` | A CodeLens reading `codoc: Ollama model backend`. | "And it works in reverse too — in the source file itself, a lens over every function tells you which feature it belongs to. Click it, jump back to the tree." |
| 4 | Read `complete()` out loud — note it sets a timeout but never retries. | Code on screen, cursor on the bare `try/except` with no retry loop. | "One dropped request from the local model server and the whole run dies. That's the bug." |
| 5 | Back in the tree, click into the "Ollama model backend" feature's description text to start editing. | The bound file auto-opens/refreshes beside the tree; the `complete()` declaration lights up **green** with a `◇ implicated by "Ollama model backend"` lens. | "The moment I start editing this feature's intent, codoc shows me exactly which code will be affected — before I've written a single line." |
| 6 | Type a steering note as a quoted `>` line: *"On a transient HTTP error or timeout, retry with **exponential backoff** (max 3 attempts) instead of aborting the run. Add a test for the retry path."* Bold "exponential backoff". Paste a link to a runbook page as `[flaky local inference](https://...)`. | The note renders as a blockquote under the feature; the bold span and the link are visibly styled. | "I'm not writing a chat prompt — I'm editing the document itself. The quote is a direct instruction to the agent. The bold phrase is the one requirement I most want it to get right. The link is a page I want it to read first." |
| 7 | Hand off the edit (save / the hand-off action in the UI). | Tree's status line (top CodeLens) flips from "draft" to something like "1 queued — awaiting implementation." | "Until I hand it off, this is just a draft — nothing runs behind my back. Handing off is the explicit go-ahead." |
| 8 | Switch to the Claude Code panel in the same window, run `/codoc:sync`. | The agent reads `.codoc/realize.md`, calls the codoc MCP tools, edits `mini_coding_agent.py` (adds the retry/backoff loop) and the test file. Live activity ticks show up on the feature in the tree ("implementing 1 of 1"). | "This is Loop B — tree to code. The agent isn't guessing from a chat transcript; its instructions are the directive built from exactly what I wrote: the note, the bolded requirement, the runbook link." |
| 9 | Back in the tree/diff view, show the change as tracked edits with inline **Accept/Reject**. Accept it. | A diff block under "Ollama model backend" — added retry loop, added test — with ✓/✗ actions. If a *new* "Retry-with-backoff helper" child feature also appears as a `+` ghost row, accept that too (see contingency note above). | "The result comes back as a normal diff, not a wall of chat text. I review it right where the feature lives, and accept." |
| 10 | Point at the "Ollama model backend" feature's description — it now mentions retry/backoff even though Maya never typed that. | Description prose updated automatically, no proposal, no prompt. | "That's Loop A, the other direction — codoc watches the code too. This was a small enough change that it just kept the prose faithful, automatically." |
| 11 | Run the tests in a terminal (`pytest -k ollama`). | 3 passed. | "The fix — and the test the note asked for — both land, and they pass." |
| 12 | Show the tree's top status line reading "in sync." | Clean state, 0 pending. | "codoc and the code agree again. The reason for the change — the runbook, the bolded requirement — stays attached to the feature, not lost in a closed ticket." |

## Optional closing beat (if time allows)

| # | Do this | Say |
|---|---|---|
| 13 | Briefly show `codoc status` in a terminal, or the extension's status bar item. | "Everything you just saw runs today, end to end, in VS Code — reading the outline, editing it, directing the agent, and reviewing what it did." |

## Design points this covers (cross-reference to the paper)

- **§3 outline + navigation** — Scenes 1–3 (the tree, code links, reverse CodeLens).
- **§4 editing intent, not chat** — Scenes 5–7 (bridge highlight, steering note, bold Focus, Consult link, draft/hand-off gate).
- **§4 Loop B realize + reviewable diff** — Scenes 8–9 (directive, MCP tool calls, tracked-change accept).
- **§5 Loop A reflects code back / conflict priority** — Scene 10 (auto-amend) and Scene 9's optional ghost row (structural proposal).
