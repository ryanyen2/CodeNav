# codoc — system walkthrough storyboard

A single continuous session with one maintainer, told through the VS Code
extension's **Codoc Tree** doc view. Replaces the `mini-coding-agent` scenario in
`paper/video-storyboard.md`, which demonstrates a retired input path (`> …`
steering text) and reads as a toy.

Dana's dialogue and typed text are plain English. The right-hand notes are for us,
not for the demo.

---

## Who this is

**Dana Okonjo**, sole maintainer of `relay` — a small async HTTP client library
for Python. About 11,000 lines. A few thousand projects depend on it. Dana has
been on it for six years, works on it two evenings a week, and merges drive-by
pull requests from people they will never meet.

Dana uses Claude Code every day and likes it. The problem is not the model.

### The complaint, in Dana's words

> I keep a CLAUDE.md and a design doc for how retries work. Both of them say the
> same thing: exponential backoff with jitter, three attempts, retry on 5xx and
> connection errors. That was true two years ago.
>
> Since then a contributor moved the retry loop out of the client and into the
> connection pool, so it could reuse a warm socket. Someone else added 429
> handling that reads the `Retry-After` header and skips backoff entirely. And
> "three attempts" quietly became a budget shared across every request to the same
> host, so ten concurrent requests get three attempts between them, not thirty.
>
> None of that is written down anywhere. So every time I ask Claude to touch
> retries, it reads my doc, believes it, and writes code against a policy that
> stopped existing a year ago. I spend twenty minutes correcting it in chat, the
> correction works, and then the session ends and the correction is gone. Next
> week I do it again.
>
> I don't need something to write my docs for me. I need the doc to be attached to
> the code well enough that it can tell me when it has gone wrong.

That last sentence is the thesis. A CLAUDE.md cannot notice it is stale, because it
is prose *about* code. It holds no record of which functions it was describing, so
nothing can ever contradict it.

---

## Scene 1 — The map disagrees with the maintainer, and it is right

**On screen.** Dana installs the extension and runs setup. A few minutes later
`.codoc/tree.codoc` opens in the Codoc Tree view: a document, not a sidebar. Left
pane is the outline of features. Right pane is one continuous rich-text editor
where each heading is a feature and the prose under it is what that feature is
for.

Dana scrolls to a heading called **Retry and backoff handling**. Under it, three
inline citations rendered as ordinary links: `_pool.py`, `_client.py`, and — Dana
stops — `_auth.py`.

**Dana:**

> Why is the auth module in the retry path?

Dana clicks the `_auth.py` citation. The code opens beside the document, jumped
to the exact function. It is a token-refresh helper that retries on a 401 with its
own loop, written by a contributor eighteen months ago. Dana had forgotten it
existed.

Dana clicks into the heading text and types over it:

> **Retry policy — where retries actually happen**

As the words change, a thin underline appears under exactly what they changed, and
a small hollow dashed circle shows up on the heading. Nothing else happens. No
spinner, no agent, no diff.

**Dana:**

> Good. It wrote that down and it didn't do anything with it.

| Note | |
|---|---|
| The citations are not the model's recollection. They are rows in a binding index built from tree-sitter symbol paths, with `UNIQUE(file, symbol_path)` — a chunk belongs to exactly one feature. That is why `_auth.py` showed up: something bound to it, and no amount of prose could have hidden that. | `codoc/store/db.py`, `codoc/lang/` |
| The underline is the "captured" decoration: changed-versus-baseline, computed client-side, persisting until an explicit send. The hollow circle means recorded and staged, not sent. | `tiptap/captured-decorations.ts` |
| Distinct from spec-driven flows: nothing was generated *for* Dana here. The tree is authored intent; the bindings are the derived part. | |

---

## Scene 2 — The document notices it is lying

**Two days later.** A contributor's PR merges. It moves the 429 `Retry-After`
handling out of the client and into the pool, and deletes the old helper. Dana
pulls, makes coffee, and opens the tree.

**On screen.** **Retry policy** now carries a small `?` next to it, and a faint
rail runs down the left of its description. Dana hovers. The tooltip says the code
this description is bound to changed, and the description did not.

Underneath, the citation list has moved on without the prose: two of the three
symbols this paragraph cited are gone, and one new symbol in `_pool.py` has
attached itself.

**Dana:**

> This is the thing I have never had. It didn't rewrite my paragraph and it didn't
> ask me a vague question. It told me which sentences are now describing code that
> isn't there.

**Dana, a beat later:**

> If I'd asked Claude to work on retries this morning it would have read my old
> paragraph and built on top of a lie. Twice now that's cost me a release.

| Note | |
|---|---|
| **This is the beat no spec-driven approach reaches.** Loop A diffs the chunk index and marks a feature `questioned` when it owns a bound chunk whose code changed while its prose did not — and `binding-lost` when it owns no code at all anymore. | `codoc/loop/loop_a.py:_compute_drift` |
| Note what it deliberately does *not* do: it does not silently regenerate the paragraph. Authored intent is not the model's to overwrite. It raises a question and waits. | decision table, `codoc/loop/classify.py` |
| **Gap to close before this can be demoed.** The `?` marker ships today only in the raw-text editor. The Codoc Tree webview — the default editor — sends `driftFids: []` and never renders it. The data is already in the sidecar (`feature_drift`) and the type already exists (`FeatureDrift`). See "What this needs" below. | `doc-view.ts:167`, `providers/decoration.ts:82` |

---

## Scene 3 — Answering the question *is* the change request

Dana does not open a chat. They click into the paragraph and rewrite it to the
truth, in the document, in their own words:

> Retries live in the pool, not the client. The pool holds one shared budget of
> three attempts per host, so a burst of concurrent requests can't multiply into
> thirty. A 429 that carries a Retry-After header honors the header and does not
> spend from the budget.

Then, on a new line, the part that is not a description but a request:

> **A 429 with no Retry-After header should fall back to normal backoff instead of
> retrying immediately.** Follow the parsing rules in
> [the Retry-After spec](https://httpwg.org/specs/rfc9110.html#field.retry-after).

Two things visibly change as Dana types. The bolded sentence stays bold. The link
picks up an underline of its own.

**Dana:**

> That underline is the editor telling me it caught the link. I've had enough
> tools quietly ignore half of what I wrote.

The `?` is gone — the paragraph is no longer describing absent code. The changed
words carry the captured underline. The heading still shows the hollow circle.
Dana reads the whole thing back once, then presses **⌘S**.

The hollow circle fills into a solid diamond. The toolbar reads **Commit & send (1)**.

**Dana:**

> One sentence of description, one sentence of instruction, and I pressed save. I
> did not write a prompt.

| Note | |
|---|---|
| Bold becomes a `Focus:` line in the directive; a markdown link becomes a `Consult:` line the realizing agent fetches first. Both are markdown-native — no syntax to learn — and both are visibly acknowledged, which is the point of the underline. | `codoc/loop/loop_b.py:_signal_lines`, `tiptap/consult-decorations.ts` |
| Descriptive prose and a code request are separated by the classifier, not by Dana. The first paragraph records intent and queues nothing. The bolded imperative queues work. | `codoc/loop/classify.py` |
| ⌘S is the only moment anything leaves the machine. Everything before it was local and reversible. This is the draft/hand-off gate, and it is what makes the tool safe to leave running. | `doc-view.ts:triggerCommit` |

---

## Scene 4 — The work lands in the document, not in a transcript

Dana switches to the Claude Code panel in the same window and runs `/codoc:sync`.

**On screen.** A quiet ribbon appears under the **Retry policy** heading and lists
what the agent is doing, one line at a time, each ticking to a check as it moves
on:

```
reading _pool.py          ✓
editing _pool.py          ✓
editing tests/test_retry.py   ✓
running pytest
```

While it works, the description text goes dim — staked out but unresolved. When
the agent finishes, the prose resolves word by word from grey back to full ink.

**Dana:**

> I can see it working on the paragraph I wrote. Not on a wall of chat.

What the agent actually received (Dana never sees this; it is the point):

```
UPDATE FEATURE: "Retry policy — where retries actually happen"
  New intent: Retries live in the pool, not the client. The pool holds one shared
    budget of three attempts per host … A 429 with no Retry-After header should
    fall back to normal backoff instead of retrying immediately.
  Bound code: relay/_pool.py::ConnectionPool.acquire, relay/_pool.py::_backoff,
    relay/_auth.py::TokenRefresher.refresh
  Edit only: relay/_pool.py, relay/_auth.py
  Align the bound code with the new intent.
  Author asked: "429 with no Retry-After should back off normally"
  Focus: A 429 with no Retry-After header should fall back to normal backoff …
  Consult: https://httpwg.org/specs/rfc9110.html#field.retry-after
```

| Note | |
|---|---|
| `Edit only:` is derived from the binding index, not from the model's judgement about scope. The agent is bounded by which files this intent actually owns. | `loop_b.py:_bound_code` |
| `Author asked:` carries Dana's own words from the session, so the agent implements the stated goal rather than a reconstruction of it from a tree diff. | `loop_b.py:_intent_line` |
| codoc never spawns a headless model. The work runs in Dana's interactive session under Dana's permissions. | |

---

## Scene 5 — The overreach, caught in the document

The ribbon collapses. Dana scrolls and finds a second feature — **Connection
reuse** — now wearing a small warning marker. Hovering: *the AI changed this while
realizing another of your edits.*

**Dana:**

> Right, because holding the socket open for a Retry-After means it's touching
> reuse. I'd have wanted to know that.

Dana reads the change inline, under the heading where it belongs. Two things
happened. The agent held the connection through the Retry-After wait — correct,
and Dana accepts it with the inline ✓. It also dropped the pool's idle timeout
from 90 seconds to 30, which Dana never asked for and which would change behavior
for everyone. Dana presses ✗.

**Dana:**

> That's the part that usually gets past me. It's in a diff, it looks reasonable,
> it's four lines away from the thing I asked for, and I scroll past it.

| Note | |
|---|---|
| Scope divergence: a realization touched a feature beyond the directive it was given. Detectable only because there is a binding index to notice *with* — a spec document has no way to know the agent went outside the lines. | `codoc/loop/phase.py` (`DIVERGENT`), `doc-view.ts:894` |
| Accept and reject are inline, at the feature, in the document. There is no separate review queue to remember to open. | `tiptap/suggestion-decorations.ts` |

---

## Scene 6 — Three weeks later, the reasoning is still there

A drive-by PR arrives touching `_pool.py`. Dana opens a Claude session and asks it
to review.

The session pulls the feature slice for the touched files — the retry feature, its
real bindings, and its immediate neighbors along call and import edges. Not the
whole repo. Not the old design doc. It reviews the PR against the policy Dana
wrote in Scene 3.

**Dana:**

> It's arguing with the contributor using my own sentence. Which is what I wanted
> to happen the whole time.

Dana turns on **History**. Every feature grows a small line saying who last
changed it and when, tinted by whether that was a person or an agent. **Retry
policy** reads *You edited · 3 weeks ago*. Hovering shows the trace: Dana
answering a question the tool raised, followed by the agent's realization,
followed by Dana rejecting one part of it.

**Dana, closing:**

> The doc used to be the last thing I updated, so it was always the thing that was
> most wrong. Now it's the thing that tells me it's wrong.

| Note | |
|---|---|
| `codoc_context` is the agent's primary read: the tree slice bounded by the edit, not by repo size. This is what replaces "paste the design doc into the prompt and hope." | `codoc/mcp/tools.py` |
| History reads the per-feature slice from the event log — who, when, and why, surviving the session that produced it. | `tiptap/blame-decorations.ts`, events schema v3 |

---

## Why a reviewer should find this hard to dismiss

Four claims, each demonstrated by one scene rather than asserted:

1. **A document can be bound tightly enough to code that it detects its own
   staleness.** (Scene 2.) Prose-only specs cannot, at any level of discipline,
   because they hold no record of what they were describing.
2. **Answering "your doc is stale" and requesting a change are the same gesture.**
   (Scene 3.) There is no translation step from decision to prompt, so no place
   for the decision to be lost.
3. **The agent's scope is bounded by an index, not by its own judgement — and
   when it exceeds that scope, the document says so.** (Scenes 4 and 5.)
4. **The reasoning outlives the session.** (Scene 6.) The correction Dana used to
   repeat every week is now written where the next agent reads.

The scenario is deliberately unglamorous: no greenfield app, no demo repo, no
feature invented to suit the tool. It is one maintainer, one paragraph that went
stale for an ordinary reason, and the twenty minutes a week they had been losing
to it.

---

## What this needs before it can be recorded

| # | Gap | Why it blocks the demo | Size |
|---|---|---|---|
| 1 | The `questioned` / `binding-lost` marker does not render in the Codoc Tree webview (`doc-view.ts` sends `driftFids: []`; the `?` badge is raw-text-editor only). | Scene 2 is the whole argument and currently has no visual in the default editor. | Small — thread the existing `feature_drift` sidecar slice through the payload and render it like the `divergent` badge. |
| 2 | No demo repo. `relay` is a stand-in. | Scenes 2 and 5 need a real merge that genuinely moves bound symbols. | Medium — fork a real async HTTP client, or stage a two-commit history on `test/requests/`. |
| 3 | Scene 5 depends on the agent overreaching. | Not reliably reproducible on demand. | Small — script the overreach as a real second edit rather than hoping for it; or accept a retake. |
| 4 | Scene 4's directive block is written from the builder source, not captured from a run. | Fine for a storyboard; must be a real capture for the paper. | Small — run it and paste the real `realize.md`. |

Items 2–4 are production work. Item 1 is a genuine product gap that also affects
ordinary users, not just the recording.
