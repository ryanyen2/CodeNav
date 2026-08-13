/**
 * busy-decorations.ts — the skeleton/shimmer state for sections that are being
 * REWRITTEN under the reader right now, and the guard that keeps their hands off
 * mid-rewrite.
 *
 * Two producers share this one surface:
 *   • translating — a `codoc translate` run has this node in its pending set
 *     (payload.translation.pending). Its prose is about to be replaced wholesale,
 *     batch by batch; a keystroke typed into it now would be merged against text
 *     that is already gone.
 *   • applying — the agent is reflecting work back into THIS feature's entry
 *     (activity phase `reflecting`). Its description is the write target of a
 *     store AMEND already in flight.
 *
 * Both are drawn the same way — a shimmer sweep over the feature's heading + body
 * (the "UI skeleton" read) — because both mean the same thing to the reader: this
 * section is being produced, not awaiting your input. And both are guarded the same
 * way: a user transaction whose steps touch a busy feature's range is dropped
 * (filterTransaction), with the shimmer itself explaining why. Programmatic
 * transactions (REFLECT_META — projection adopts, mint patches) pass, or the very
 * update that finishes the rewrite could never land.
 *
 * The guard is deliberately per-section: everything else stays editable, so a long
 * translation never turns the document read-only — only the not-yet-translated
 * tail wears the skeleton, and it shrinks as batches land.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { nextDecorations } from './decoration-policy';
import { REFLECT_META } from './author-plugin';

export const BUSY_UPDATED = 'codocBusyUpdated';
const busyKey = new PluginKey('codocBusyDecorations');

export type BusyKind = 'translating' | 'applying';

export interface BusyInfo {
    kind: BusyKind;
    /** One hover sentence: what is happening and why typing here waits. */
    label: string;
}

export interface BusyDecorationsOptions {
    getBusy: () => ReadonlyMap<string, BusyInfo>;
}

/** [from, to) doc ranges of each busy feature — heading through its own body
 *  (the next heading at ANY level), the same span a rewrite replaces. */
export function busyRanges(
    doc: PMModelNode, busy: ReadonlyMap<string, BusyInfo>,
): { fid: string; from: number; to: number; info: BusyInfo }[] {
    if (!busy.size) return [];
    interface Head { fid: string | null; pos: number }
    const heads: Head[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') heads.push({ fid: node.attrs.fid as string | null, pos });
    });
    const out: { fid: string; from: number; to: number; info: BusyInfo }[] = [];
    heads.forEach((h, i) => {
        if (!h.fid) return;
        const info = busy.get(h.fid);
        if (!info) return;
        const to = heads[i + 1]?.pos ?? doc.content.size;
        out.push({ fid: h.fid, from: h.pos, to, info });
    });
    return out;
}

/** Whether a transaction's replaced ranges intersect any busy span. Positions are
 *  read per step against the doc BEFORE that step, mapping as we go. */
export function touchesBusy(
    tr: { docChanged: boolean; mapping: { maps: readonly { forEach(f: (from: number, to: number) => void): void }[] } },
    ranges: { from: number; to: number }[],
): boolean {
    if (!tr.docChanged || !ranges.length) return false;
    let hit = false;
    for (const stepMap of tr.mapping.maps) {
        stepMap.forEach((from: number, to: number) => {
            for (const r of ranges) {
                // Inclusive touch: a caret splice AT the boundary still lands inside
                // the section being replaced.
                if (from <= r.to && to >= r.from) hit = true;
            }
        });
        if (hit) return true;
    }
    return hit;
}

export function buildBusyDecorations(
    doc: PMModelNode, busy: ReadonlyMap<string, BusyInfo>,
): DecorationSet {
    const ranges = busyRanges(doc, busy);
    if (!ranges.length) return DecorationSet.empty;
    const decos: Decoration[] = [];
    for (const r of ranges) {
        // One node decoration per block in the span (heading + each body block) so
        // the shimmer hugs real content instead of painting one giant rectangle.
        doc.nodesBetween(r.from, r.to, (node, pos) => {
            if (pos < r.from || pos >= r.to) return false;
            if (!node.isBlock) return false;
            decos.push(Decoration.node(pos, pos + node.nodeSize, {
                class: 'ce-busy ce-busy-' + r.info.kind,
                title: r.info.label,
            }));
            return false; // top-level blocks only
        });
    }
    return DecorationSet.create(doc, decos);
}

export const BusyDecorations = Extension.create<BusyDecorationsOptions>({
    name: 'busyDecorations',

    addOptions() {
        return { getBusy: () => new Map<string, BusyInfo>() };
    },

    addProseMirrorPlugins() {
        const getBusy = (): ReadonlyMap<string, BusyInfo> => this.options.getBusy();
        return [
            new Plugin({
                key: busyKey,
                // The guard: drop a USER transaction that writes into a busy span.
                // Programmatic updates (projection adopts, mint patches) are tagged
                // REFLECT_META and pass — they are how the rewrite finishes.
                filterTransaction(tr, state) {
                    if (!tr.docChanged || tr.getMeta(REFLECT_META)) return true;
                    const busy = getBusy();
                    if (!busy.size) return true;
                    return !touchesBusy(tr, busyRanges(state.doc, busy));
                },
                state: {
                    init: (_c, state) => buildBusyDecorations(state.doc, getBusy()),
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(BUSY_UPDATED),
                        () => buildBusyDecorations(newState.doc, getBusy()),
                    ),
                },
                props: { decorations(state) { return busyKey.getState(state); } },
            }),
        ];
    },
});
