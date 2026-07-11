---
title: "Feature edit activity fans out to every sibling feature sharing the same file"
date: 2026-07-10
last_updated: 2026-07-10
category: docs/solutions/logic-errors
module: "codoc/agent/hook.py + vscode-codoc/src/state/activity-model.ts"
problem_type: logic_error
component: tooling
symptoms:
  - "Editing one feature (e.g. the Ollama model backend) that has dependencies all in one file shows the 'Claude is working' editing indicator on sibling features that merely share that file"
  - "The VS Code agent ribbon / editor decorations mark multiple unrelated features as actively being realized during a single realize session"
  - ".codoc/activity.json lists every feature bound anywhere to a touched file instead of only the feature actually being edited"
root_cause: logic_error
resolution_type: code_fix
severity: low
tags:
  - activity-json
  - feature-attribution
  - realize-directive
  - ribbon
  - sidecar-bindings
  - by-file-index
  - multi-feature-file
  - editing-indicator
---

# Feature edit activity fans out to every sibling feature sharing the same file

## Problem

A file bound to several features (e.g. a shared helper module) has its edit activity attributed to *every* feature bound anywhere in the file, not just the one actually being edited — both when `codoc/agent/hook.py` writes `.codoc/activity.json` and, independently, when `vscode-codoc/src/state/activity-model.ts` reads it back through the webview.

## Symptoms

The user reported: "currently when I have some edits in a node, given that there's dependencies (and are all in one single file?) the indicator of claude is working kind of showcasing in many different places whereas I only modified the 'Ollama model backend' node." A screenshot showed the "Claude · editing mini_coding_agent.py" ribbon (rendered by `vscode-codoc/src/webview/tiptap/agent-ribbon.ts`, which paints `payload.sync.steps[fid]` per feature per its own comment at `agent-ribbon.ts:9` — that file itself was not buggy, it faithfully rendered whichever `fid`s it was handed) appearing under THREE sibling features all bound to `mini_coding_agent.py` — "Git workspace context gathering", "Ollama model backend" (the one actually being edited, marked with the pending-change diamond), and "Fake model client test double" — instead of only the one node the agent was actually touching.

## What Didn't Work

The first draft of `tests/agent/test_hook.py::test_resolve_features_narrows_to_in_flight_directive` wrote a `handed_off=True` `Directive` via `write_manifest` and then asserted `_resolve_features` narrowed to the single feature — but it failed, because `read_manifest` (`codoc/loop/edits.py:725-746`) treats a manifest as **stale** unless `.codoc/realize.md` also exists alongside it. Its docstring spells out the exact rule (`codoc/loop/edits.py:726-730`): "A manifest with no `realize.md` beside it is stale — the agent finished and deleted the queue — UNLESS it still holds DRAFT directives (`handed_off=False`) … So: no realize.md + a held draft → keep; no realize.md + all handed-off → stale (cleared)." Concretely, `read_manifest` at line 740 checks `if not (Path(codoc_dir) / REALIZE_FILENAME).exists()`, and when true and there are no draft directives left, it calls `clear_manifest(codoc_dir)` and returns `[]` (line 744-745) — so a manifest containing only handed-off directives, written without a companion `realize.md`, silently reads back empty. The test had to be corrected to also write `.codoc/realize.md` (e.g. `(codoc_dir / "realize.md").write_text("### d-1\n")`) alongside the manifest before the narrowing logic in `_realizing_features_for_file` had anything to narrow with. This is a sharp, easy-to-miss edge for anyone calling `read_manifest` in a test or a new caller: a handed-off directive is not "live" from `read_manifest`'s point of view unless `realize.md` is present too.

## Solution

**Python side** — `codoc/agent/hook.py`. Before the fix, `_resolve_features` returned every `feature_id` bound anywhere in the file via the sidecar's `by_file[rel_path]`, with no narrowing:

```python
# before
by_file: dict = sidecar.get("by_file", {})
entries = by_file.get(rel_path, [])
return [e["feature_id"] for e in entries if "feature_id" in e]
```

After the fix (`hook.py:64-111`), when a file resolves to more than one candidate feature, a new helper `_realizing_features_for_file` narrows the set using `.codoc/realize.json`:

```python
# after
all_fids = list(dict.fromkeys(e["feature_id"] for e in entries if "feature_id" in e))
if len(all_fids) <= 1:
    return all_fids
narrowed = _realizing_features_for_file(rel_path, codoc_dir, sidecar, all_fids)
return narrowed if narrowed else all_fids

def _realizing_features_for_file(rel_path, codoc_dir, sidecar, candidate_fids):
    from codoc.loop.edits import read_manifest
    candidates = set(candidate_fids)
    directive_fids = {d.feature_id for d in read_manifest(codoc_dir)
                       if d.handed_off and d.feature_id in candidates}
    if not directive_fids:
        return []
    by_feature: dict = sidecar.get("by_feature", {})
    return [fid for fid in candidate_fids if fid in directive_fids
            and rel_path in {e.get("file") for e in by_feature.get(fid, [])}]
```

It reads the realize manifest via `codoc.loop.edits.read_manifest`, filters to `handed_off=True` directives whose `feature_id` is among the candidates, and further cross-checks via the sidecar's `by_feature[fid]` index that the directive's own feature is actually bound to the touched file — then falls back to the full candidate set when nothing narrows (e.g. ad hoc editing outside any realize session, where there's no better signal, so showing all bound features remains the correct, honest fallback). This feeds both `_handle_tool` (`hook.py:191-243`, which calls `_resolve_features` and merges the result into `touched[rel].feature_ids` and into each `recent[]` entry's `feature_ids`) and, transitively, `codoc/loop/sdk_realize.py`'s `RealizeMonitor._record_touch` (`sdk_realize.py:212-220`), which imports and calls `agent.hook._handle_tool` directly — so the fix covers both the interactive Claude Code hook path and the SDK-driven realize engine with a single change.

**TypeScript side** — `vscode-codoc/src/state/activity-model.ts`. Before the fix, all three consumers (`computeActiveFeatureLines`, `activeFeatureModes`, and `featureSteps`'s internal `fidsFor` helper) unconditionally unioned the entry's `feature_ids` with a second, independent full `by_file`-style sidecar lookup:

```ts
// before (computeActiveFeatureLines, similarly in the other two)
for (const fid of entry.feature_ids) {
    featureIds.add(fid);
}
if (sidecar) {
    for (const fileEntry of entriesForFile(sidecar, filePath)) {
        featureIds.add(fileEntry.feature_id);   // re-widens back to ALL bound features
    }
}
```

After the fix, the sidecar fallback only runs when `feature_ids` is empty — an already-resolved (and possibly Python-narrowed) list is trusted as-is:

```ts
// after
if (entry.feature_ids.length) {
    for (const fid of entry.feature_ids) featureIds.add(fid);
} else if (sidecar) {
    for (const fileEntry of entriesForFile(sidecar, filePath)) {
        featureIds.add(fileEntry.feature_id);
    }
}
```

The same `if (explicit && explicit.length) return new Set(explicit); …` guard was applied to `featureSteps`'s `fidsFor` helper, and the analogous `if (entry.feature_ids.length) { … } else if (sidecar) { … }` shape to `activeFeatureModes`.

### Follow-up: symbol-level attribution (the deeper fix)

The directive-level narrowing above only disambiguates when a handed-off realize directive is in flight. For a **single-file project** (every feature bound to the one file), plain reads, ad-hoc edits, and reads of auxiliary files with no directive still fall back to "every feature bound to the file" — so the ribbon still fans out. The residual root cause is that Claude Code hooks report at *file* granularity, and the resolver used the file as the key.

The deeper fix (`hook.py`, `_symbol_scoped_features`) narrows by the *symbol actually touched*: for an `Edit`/`MultiEdit`, locate `old_string` (falling back to `new_string` once the edit has applied) in the file → the touched line(s) → the **innermost bound symbol** enclosing each line (a tree-sitter parse via `codoc.lang`'s `detect_language`/`get_adapter().extract_chunks()`, which yields `Chunk.symbol_path` in the exact `file::qualified.name` form the bindings use, with no store/index access) → that symbol's feature via the `by_file` `symbol` field (which `_resolve_features` previously discarded). Resolution priority becomes: **symbol level → directive level → file level**. Symbol level wins even over an active directive — if the agent edits `FakeModelClient`'s code while a directive for `OllamaModelClient` is queued, the touch attributes to `FakeModelClient`'s feature, because that is what is actually being edited. `Read` (no anchor) and whole-file `Write` carry no locatable symbol and fall back to the directive/file level. Because `codoc/loop/sdk_realize.py`'s `RealizeMonitor._record_touch` funnels through `_handle_tool`, the SDK-realize engine gets symbol-level attribution too, with no extra change.

## Why This Works

The bug existed as two structurally identical instances of the same over-broad "attribute a shared file to every feature that binds it" lookup, sitting on opposite sides of the same file-based sync boundary: the Python writer (`hook.py`'s `_resolve_features`, populating `activity.json` via `_handle_tool`/`mark_feature_phase`) and the TS reader (`activity-model.ts`'s three derivers, consuming `activity.json` plus the same `tree.bindings.json` sidecar written by `codoc/codoc_file/render.py:515-553`'s `write_sidecar`). Fixing only the Python side would still have shown the bug end to end, because the TS side independently re-derived the full fan-out by unioning whatever `feature_ids` it was given with its own fresh `by_file`/`entriesForFile` sidecar lookup — so even a perfectly narrowed single-`fid` `feature_ids` list coming out of `hook.py` would get widened straight back out to all three sibling features the moment the webview rendered it. The fix had to change the *trust relationship* on the TS side (treat a populated `feature_ids` as authoritative, only fall back to the sidecar union when it's empty) in addition to narrowing the source of truth on the Python side. Root cause, in one sentence: a signal (which feature is "being edited") was computed twice from the same underlying many-to-one file→features index, once on write and once on read, and neither computation had a way to know the other existed — narrowing one without the other is a silent no-op bug fix.

## Prevention

When a signal crosses a Python-writer / TS-reader boundary via a shared sidecar or control file (here `tree.bindings.json` feeding both `hook.py` and `activity-model.ts`), always check whether the same "resolve an id from a many-to-one index" lookup pattern exists on *both* sides before declaring a fix complete — grep for the sidecar's key names (`by_file`, `by_feature`, `entriesForFile`) across `codoc/` and `vscode-codoc/src/` and re-derive whether the reader trusts an already-resolved field or independently re-widens it. The new regression tests are the guard against this specific recurrence: `tests/agent/test_hook.py::test_resolve_features_narrows_to_in_flight_directive` / `test_resolve_features_falls_back_with_no_directive` / `test_resolve_features_ignores_draft_directive` (using the `shared_file_repo` fixture, a file bound to two features `f-one`/`f-two`) on the Python side, and one new test each in `vscode-codoc/src/test/feature-steps.test.ts` and `vscode-codoc/src/test/active-modes.test.ts` on the TS side, asserting a resolved single-fid touch does not widen back out to a sibling feature sharing the file. Each was verified via `git stash`/`git stash pop` on the fix files to fail pre-fix and pass post-fix. Separately, `codoc.loop.edits.read_manifest` (`codoc/loop/edits.py:725-746`) has a sharp, non-obvious edge that any future caller must respect: a manifest holding only `handed_off=True` directives reads back as `[]` (and is deleted) unless `.codoc/realize.md` also exists on disk — "handed off" alone does not mean "live" from `read_manifest`'s perspective; tests or new call sites exercising handed-off directives must also create the companion `realize.md`.

## Related Issues

- `docs/architecture.md` (the "Render + sidecar" section) documents `tree.bindings.json`'s `by_feature`/`by_file` shape correctly but is silent on the consumer-side resolution logic this bug lived in — worth a cross-reference if that section is ever expanded to describe how the sidecar is consumed, not just its shape.
- No existing `docs/solutions/` entries or open GitHub issues covered this problem — this is the first learning captured in `docs/solutions/`.
