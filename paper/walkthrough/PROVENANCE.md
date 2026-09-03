# How each figure was produced

Every figure is a screenshot of the VS Code extension, captured with
`screencapture -l <windowId>` against a throwaway VS Code profile so the theme, zoom,
and extension set are controlled and no unrelated UI is in frame. The window id comes
from `tools/winlist.swift`, because reading window bounds through System Events needs
Accessibility permission and taking a full-screen shot would capture whatever else is
on the machine.

```
Code --user-data-dir /tmp/vsfig --extensions-dir /tmp/vsext \
     --disable-workspace-trust -n repos/boids-demo
```

`/tmp/vsfig/User/settings.json` pins `Default Light Modern`, a maximized window,
`window.zoomLevel: -3`, and `chat.disableAIFeatures`. The zoom level is what makes a
feature far down the document visible at all: the capture is 2x, so a shot at zoom -3
is about the pixel density of an ordinary screenshot once cropped.

| Figure | State | How it was reached |
| --- | --- | --- |
| 1 | The document at rest | `rehearse.sh reset-hard`, then open `.codoc/tree.codoc` |
| 2 | The ask overlay | a live `claude -p "/codoc:ask …"` run; the walkthrough it drew is `.codoc/ask.json` |
| 3 | A held draft | `demo_edit.py describe "Edge behaviour" …`, drained by the daemon |
| 4 | The same feature, built | `codoc sync` then `codoc realize` with the recorded take on PATH |
| 5 | The comment and the rewrite | `demo_edit.py comment "Panic" … --scope both`, then sync and realize |
| 6 | A proposal from code | `woz.py surprise` then `codoc sync`, which raised `add_node "Obstacle avoidance"` |
| 7 | A plan | a live `claude -p "/codoc:plan …"` run, captured once the three proposals landed |

Steps 2, 6 and 7 call a model, so their wording differs between runs. The titles and
descriptions in the figures are what those runs actually produced, read back out of
the store rather than transcribed.

## What is not in the figures

The document was never scrolled during capture, because driving the editor's UI needs
Accessibility permission that this machine does not grant to the shell. Every state
was reached by changing the workspace from the shell and letting the extension render
it. A figure therefore shows the feature wherever it already sat on the page.

`codoc serve` was tried first and abandoned. It runs the same editor bundle in a
browser and renders the walkthrough, drafts, and comment threads, but its payload
carries no `proposals` slice, so proposals, code diffs, and verdict controls cannot be
photographed from it.
