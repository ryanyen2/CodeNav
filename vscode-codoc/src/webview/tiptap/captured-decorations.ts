/**
 * captured-decorations.ts — the "recorded, not sent" mark on EVERY user edit (U3).
 *
 * The first phase of the edit lifecycle. The moment the human changes a feature's
 * text — prose or code-implying, large or small — that feature wears a quiet
 * "captured" rail + dot meaning "your edit is recorded locally; it has NOT gone to
 * the agent yet." This is purely CLIENT-SIDE: it compares the live editor doc against
 * the last canonical baseline (the doc as last loaded from a payload), so it does NOT
 * depend on the daemon's code-implying classification — removing the "is my edit big
 * enough to show up?" guessing the hold-set gate created.
 *
 * The lifecycle, by decoration family:
 *   captured (here)         — changed-vs-baseline ∪ held drafts, MINUS handed-off.
 *   pending (hold-decorations) — held & handed-off: staged & sent, the agent will act.
 *   resolving (activity)    — the agent is mid-edit.
 *   review (suggestion)     — the agent's edits land as a sentence-level accept/reject diff.
 *
 * A feature that the user staged & sent (Save / Commit → hand-off) leaves `drafts`
 * and enters the handed-off set, which SUPPRESSES its captured mark so the pending
 * badge takes over cleanly (no double mark). Drafts that the daemon already rendered
 * back to tree.codoc (so they no longer diff vs baseline) are still captured via the
 * explicit `getDrafts` union — they remain "recorded, not sent" until hand-off.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { inlineRunsToText, type PMNode } from '../../state/pm-doc';

export const CAPTURED_UPDATED = 'codocCapturedUpdated';
const capturedKey = new PluginKey('codocCapturedDecorations');

export interface CapturedDecorationsOptions {
    /** fid → canonical feature text (title + description), from the last payload load. */
    getBaseline: () => Map<string, string>;
    /** Held drafts (recorded, not yet handed off) — always captured even once the daemon
     *  rendered their prose back so they no longer diff vs the baseline. */
    getDrafts: () => Set<string>;
    /** Handed-off features (staged & sent) — never captured; they wear the pending badge. */
    getHandedOff: () => Set<string>;
}

/** fid → concatenated text (heading title + each description paragraph) for a doc in
 *  the JSON PMNode shape. The canonical projection used for BOTH the baseline and the
 *  live current (via `doc.toJSON()`), so the comparison is whitespace/ref-consistent. */
export function featureTextFromJson(doc: PMNode): Map<string, string> {
    const map = new Map<string, string>();
    let fid: string | null = null;
    let buf: string[] = [];
    const flush = (): void => { if (fid) map.set(fid, buf.join('\n')); };
    for (const node of doc.content ?? []) {
        if (node.type === 'featureHeading') {
            flush();
            fid = (node.attrs as { fid?: string | null } | undefined)?.fid ?? null;
            buf = [inlineRunsToText(node.content ?? [])];
        } else if (fid && node.type === 'paragraph') {
            buf.push(inlineRunsToText(node.content ?? []));
        }
    }
    flush();
    return map;
}

/**
 * The captured set: features the user has edited but not yet staged & sent.
 * A feature is captured when it is a held draft OR its current text diverges from the
 * baseline — EXCEPT handed-off features (those are pending, not captured). A feature
 * absent from the baseline (a brand-new heading) is left to the ADD proposal flow, not
 * marked captured here. Pure — unit-tested.
 */
export function capturedFids(
    baseline: Map<string, string>, current: Map<string, string>,
    drafts: Set<string>, handedOff: Set<string>,
): Set<string> {
    const out = new Set<string>();
    for (const [fid, text] of current) {
        if (handedOff.has(fid)) continue;
        if (drafts.has(fid)) { out.add(fid); continue; }
        const base = baseline.get(fid);
        if (base !== undefined && base !== text) out.add(fid);
    }
    return out;
}

/** Same projection as featureTextFromJson but over the live ProseMirror model doc,
 *  by round-tripping through toJSON so the tokenization is identical. */
function currentFeatureText(doc: PMModelNode): Map<string, string> {
    return featureTextFromJson(doc.toJSON() as PMNode);
}

/** Build the captured decorations: a quiet rail on each captured feature's body + a
 *  "recorded" dot on its heading. No per-word underline (that precise highlight is
 *  the pending state's job) — captured is a calm feature-level "saved locally" cue.
 *  Exported for headless tests (the widget DOM factory only runs when the view renders). */
export function buildCapturedDecorations(doc: PMModelNode, captured: Set<string>): DecorationSet {
    if (!captured.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    let activeFid: string | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            if (!fid || !captured.has(fid)) { activeFid = null; return; }
            activeFid = fid;
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-captured' }));
            decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
                const dot = document.createElement('span');
                dot.className = 'ce-captured-dot';
                dot.contentEditable = 'false';
                dot.title = 'Recorded — your edit is saved locally. Press ⌘S / Commit to stage & send it to the agent.';
                return dot;
            }, { side: 1, key: 'cap-' + fid }));
            return;
        }
        if (activeFid && node.type.name === 'paragraph' && node.content.size > 0) {
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-captured-rail' }));
        }
    });
    return DecorationSet.create(doc, decos);
}

export const CapturedDecorations = Extension.create<CapturedDecorationsOptions>({
    name: 'capturedDecorations',

    addOptions() {
        return { getBaseline: () => new Map(), getDrafts: () => new Set(), getHandedOff: () => new Set() };
    },

    addProseMirrorPlugins() {
        const compute = (doc: PMModelNode): Set<string> => capturedFids(
            this.options.getBaseline(), currentFeatureText(doc),
            this.options.getDrafts(), this.options.getHandedOff(),
        );
        return [
            new Plugin({
                key: capturedKey,
                state: {
                    init: (_c, state) => buildCapturedDecorations(state.doc, compute(state.doc)),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(CAPTURED_UPDATED) || tr.docChanged) {
                            return buildCapturedDecorations(newState.doc, compute(newState.doc));
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return capturedKey.getState(state); } },
            }),
        ];
    },
});
