# Figure plan — the System section

Two figures. Figure 1 is a polished illustrated mockup of the two-pane CoDoc
editor (full width). Figure 2 is a compact vector schematic of the round-trip
(single column). Between them, Figure 1 shows the four concepts *as interface*
and Figure 2 shows the same four concepts *as mechanism*, so each concept
paragraph can point at one or both.

Concept → figure map (the four concepts of the draft):

| Concept in the draft | Figure 1 callout | Figure 2 element |
|---|---|---|
| The feature document | ① feature + description + binding link | the two poles + the DOCUMENT object |
| Keeping the document true to the code (Loop A) | ② ghost-row proposal with accept/reject | bottom arrow (code → document) |
| Turning intent into code (Loop B) | ③ held-draft / hand-off control; ④ tracked-change return | top arrow (document → code) with the hand-off gate |
| When document and code disagree | ⑤ hold indicator on the edited feature | the lock at the DOCUMENT pole |

---

## Figure 1 — the hero (full width, `figure*`)

A single illustrated frame of the **CoDoc Tree editor in VS Code**, redrawn to
match the real tool. Two panes side by side.

**Left pane — the feature document.** A nested outline. The running example is
front and center:

- The feature **Ollama model backend client** is expanded, showing its short
  description prose.
- A **binding link** rendered inline in the description, styled as the real
  citation link (e.g. `mini_coding_agent.py › OllamaModelClient`).
- The description carries **tracked changes** — the sentence about retrying on
  timeout with growing backoff shown as inserted text (underline) returned by
  the agent, with small accept/reject controls per change.
- A **held-draft badge and hand-off control** attached to the feature, showing
  that the edit is a draft that has not run yet.
- A **ghost-row proposal** below the feature — the structural change Loop A
  surfaced (the retry helper being attached, or a new child feature) — rendered
  as a faint row with inline ✓ / ✗.

**Right pane — the code.** `mini_coding_agent.py` with the `OllamaModelClient`
class, the newly added retry helper highlighted, and a diff gutter so the reader
sees code and document are the same object seen two ways.

**Callouts (5), numbered, cited from the prose:**

1. Feature + description + binding link. → §"The feature document"
2. Ghost-row proposal with accept/reject. → §"Keeping the document true"
3. Held-draft / hand-off control on the edited description. → §"Turning intent into code"
4. Tracked-change text returned into the description. → §"Turning intent into code"
5. Hold indicator on the feature being edited. → §"When they disagree"

**Production notes**

- Full-width `figure*`, placed top or bottom of a page, caption below.
- Build in Figma matching VS Code layout, colors, and the editor font. Real
  chrome (title bar, pane divider), so it reads as the tool.
- Callouts and number badges as vector overlay; export the whole thing as PDF.
- Any real text ≥ 6–7pt at final printed width; text meant only as texture can
  be smaller and is what a callout zooms into.
- Accept/reject differ by shape (check vs cross), not only color; palette
  colorblind-safe.
- Staging is allowed: proposal, held draft, and tracked-change return appear in
  one frame though they occur at different moments. Do not invent UI that does
  not exist.

---

## Figure 2 — the round-trip schematic (single column)

A compact vector diagram. Two poles, two arrows, one lock.

```
        edit description → held draft →|gate|→ agent writes code → tracked-change diff
   +----------+  ─────────────────────────────────────────────────────►  +--------+
   | DOCUMENT |                                                            |  CODE  |
   |  (intent)|  ◄─────────────────────────────────────────────────────   | (runs) |
   +----------+   mechanical refresh / reattach + one model call → proposal +--------+
       🔒 document wins on conflict; binding maintenance continues
```

- **Top arrow, document → code (Loop B):** label the sequence and draw the
  **hand-off as an explicit gate** on the arrow, since that gesture is the key
  decision of the concept. Nothing crosses the gate until the person opens it.
- **Bottom arrow, code → document (Loop A):** label it as mostly mechanical
  (refresh / reattach across moves and renames) with **one model call** for new
  or orphaned code, and mark that structural changes emerge as a **proposal**.
- **The lock at the DOCUMENT pole:** the precedence rule. Caption: the document
  wins on conflict, and binding maintenance is never held back.

**Production notes**

- Single-column `figure`, ~3.3in wide. Vector only, no raster.
- Keep labels to the short phrases above; the prose carries the detail.
- Consistent arrow/gate/lock iconography with Figure 1's callout style.

---

## How the text references them

- "The feature document" cites Figure 1 (callout ①) and Figure 2 (the poles).
- "Keeping the document true" cites Figure 1 (②) and Figure 2 (bottom arrow).
- "Turning intent into code" cites Figure 1 (③, ④) and Figure 2 (top arrow + gate).
- "When they disagree" cites Figure 1 (⑤) and Figure 2 (the lock).
