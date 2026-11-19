/**
 * decorations.ts: everything drawn on the prose, as ProseMirror decorations.
 *
 * Nothing drawn is stored in the document. calc/claims.ts says what to paint
 * on each block in scalar offsets; `decorationsFor` places those paints at
 * document positions, draws removed text as a widget carrying the words the
 * code dropped, and puts a phase marker at each heading. `Paints` keeps the
 * result between transactions and redraws only the blocks whose paints
 * changed, because rebuilding every decoration on every keystroke is what
 * makes typing slow in a large tree. `listNumbers` puts each numbered list
 * item's number on it.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

import { PHASE_CHANNEL, PHASE_LABEL, paintsFor, theirEdits, type Paint } from '../calc/claims';
import { pathOf, refEq, refsOf, textField, type CharRef, type Phase, type ProjectedBlock, type Projection } from '../calc/projection';
import type { Shadow } from '../calc/shadow';
import { REPLACED } from './commands';
import { sameStructure } from './guards';

/** What the page is showing: the projection in hand, whether the History stance is on,
 *  and who the viewer is (only their own words are inked). */
export interface Shown {
    projection: Projection;
    history: boolean;
    viewer: () => string;
}

/** Transaction meta: redraw the decorations though the document did not change. */
export const REPAINT = 'codoc:repaint';

/** UTF-16 offset inside a textblock for a scalar offset. */
export function unitOffset(block: PMNode, scalar: number): number {
    let units = 0;
    let seen = 0;
    for (let i = 0; i < block.childCount && seen < scalar; i++) {
        const cps = Array.from(block.child(i).text ?? '');
        const take = Math.min(cps.length, scalar - seen);
        units += cps.slice(0, take).join('').length;
        seen += take;
    }
    return units;
}

/**
 * Each numbered item's number, as `data-n` on its node: the number the export
 * writes (`block_md.render`). Markdown nests a list one level at a time, so an
 * item is counted at `min(level, level of the item before it + 1)`, and at 1
 * after a block that is not an item. A numbered item counts among the numbered
 * items at that level; the count restarts after a shallower item, a bullet at
 * the same level, or a block that is not an item, and a deeper item in between
 * does not restart it (docs/model.md, "The Markdown form"). Rebuilt on every
 * change to the document: one pass over the top level.
 */
export function listNumbers(): Plugin<DecorationSet> {
    const build = (doc: PMNode): DecorationSet => {
        const decos: Decoration[] = [];
        const counters = new Map<number, number>();
        let prev = 0;
        doc.forEach((node, pos) => {
            const name = node.type.name;
            if (name !== 'bullet' && name !== 'numbered') { counters.clear(); prev = 0; return; }
            const level = Math.min(node.attrs.level as number, prev + 1);
            prev = level;
            for (const l of [...counters.keys()]) if (l > level) counters.delete(l);
            if (name === 'bullet') { counters.delete(level); return; }
            const n = (counters.get(level) ?? 0) + 1;
            counters.set(level, n);
            decos.push(Decoration.node(pos, pos + node.nodeSize, { 'data-n': `${n}.` }));
        });
        return DecorationSet.create(doc, decos);
    };
    const key = new PluginKey<DecorationSet>('codoc-numbers');
    return new Plugin<DecorationSet>({
        key,
        state: { init: (_c, state) => build(state.doc), apply: (tr, prev) => (tr.docChanged ? build(tr.doc) : prev) },
        props: { decorations: state => key.getState(state) ?? null },
    });
}

/** The decorations for the blocks in `only`, or for every block. */
function decorationsFor(doc: PMNode, paints: Map<string, Paint[]>, shown: Shown,
                        only?: ReadonlySet<string>): Decoration[] {
    const decos: Decoration[] = [];
    const viewer = shown.viewer();
    doc.forEach((block, pos) => {
        const bid = block.attrs.bid as string | null;
        if (!bid || (only && !only.has(bid))) return;
        if (block.type.name === 'heading') {
            const phase: Phase = shown.projection.phases[bid] ?? 'synced';
            const theirs = theirEdits(shown.projection, bid, viewer);
            if (phase !== 'synced') {
                decos.push(Decoration.widget(pos + 1, () => {
                    const el = document.createElement('span');
                    el.className = `codoc-marker codoc-marker-${phase} codoc-ch-${PHASE_CHANNEL[phase]}${theirs ? ' is-theirs' : ''}`;
                    el.title = theirs || PHASE_LABEL[phase];
                    el.setAttribute('aria-label', el.title);
                    return el;
                }, { side: -1, key: `marker:${bid}:${phase}:${theirs}` }));
            }
        }
        for (const p of paints.get(bid) ?? []) {
            const from = pos + 1 + unitOffset(block, p.start);
            if (p.cls === 'code-removed') {
                decos.push(Decoration.widget(from, () => {
                    const el = document.createElement('span');
                    el.className = 'codoc-paint codoc-code-removed';
                    el.textContent = p.text ?? '';
                    el.title = p.title;
                    el.setAttribute('aria-description', p.title);
                    return el;
                }, { side: 0, key: `removed:${bid}:${p.start}:${p.text?.length ?? 0}` }));
                continue;
            }
            const to = pos + 1 + unitOffset(block, p.end);
            if (to > from) {
                decos.push(Decoration.inline(from, to, { class: `codoc-paint codoc-${p.cls}`, title: p.title, 'aria-description': p.title }));
            }
        }
    });
    return decos;
}

/**
 * A block's paints moved from the projection's text onto the page's. The two
 * differ while the block holds words the daemon has not answered, so each
 * painted character is found in the shadow by identity: a paint keeps the
 * characters still on the page, splits where the page has new ones between
 * them, and is dropped when none is left. A zero-width paint stays after the
 * character it followed.
 */
function onPage(paints: Paint[], b: ProjectedBlock, shadow: Shadow | undefined): Paint[] {
    const f = textField(b, pathOf(b.kind));
    if (!paints.length || !f || !shadow) return paints;
    const refs = refsOf(f);
    if (refs.length === shadow.length && refs.every((r, i) => refEq(r, shadow.refs[i]))) return paints;
    const key = (r: CharRef | undefined) => JSON.stringify(r);
    const index = new Map(shadow.refs.map((r, i) => [key(r), i]));
    const at = (k: number) => index.get(key(refs[k]));
    const out: Paint[] = [];
    for (const p of paints) {
        if (p.end <= p.start) {
            const i = p.start === 0 ? -1 : at(p.start - 1);
            if (i !== undefined) out.push({ ...p, start: i + 1, end: i + 1 });
            continue;
        }
        let run: Paint | null = null;
        for (let k = p.start; k < p.end; k++) {
            const i = at(k);
            if (i === undefined) run = null;
            else if (run && run.end === i) run.end = i + 1;
            else { run = { ...p, start: i, end: i + 1 }; out.push(run); }
        }
    }
    return out;
}

/** Where each of `bids` sits in the document, as a `[from, to)` range. */
function blockRanges(doc: PMNode, bids: ReadonlySet<string>): Array<{ from: number; to: number }> {
    const out: Array<{ from: number; to: number }> = [];
    doc.forEach((block, pos) => {
        if (bids.has(block.attrs.bid as string)) out.push({ from: pos, to: pos + block.nodeSize });
    });
    return out;
}

/**
 * The paints per block and the decoration set built from them, kept across
 * transactions.
 *
 * Whoever changes what a block should look like calls `mark`, for that block
 * or for all of them, and the plugin redraws what is marked at the next
 * transaction that changes the document or carries `REPAINT`. A change to the
 * document's structure marks every block by itself, because the feature a
 * block sits in (`featureOf`, which adoption reads to mark a feature's blocks)
 * is where it sits.
 */
export class Paints {
    readonly plugin: Plugin<DecorationSet>;
    /** The feature each block sits in, as of the last full redraw. */
    readonly featureOf = new Map<string, string | null>();
    private readonly byBlock = new Map<string, Paint[]>();
    private all = true;
    private readonly dirty = new Set<string>();

    /** `shadows` are the page's blocks with their character identities (calc/shadow.ts). */
    constructor(private readonly shown: Shown, private readonly shadows: Map<string, Shadow>) {
        const key = new PluginKey<DecorationSet>('codoc-paints');
        this.plugin = new Plugin<DecorationSet>({
            key,
            state: {
                init: (_config, state) => this.build(state.doc),
                apply: (tr, prev, _old, state) => {
                    if (!tr.docChanged && !tr.getMeta(REPAINT)) return prev;
                    // An adoption that rebuilds the document replaces it in one
                    // step, and mapping every decoration through that costs more
                    // than building them again, so `REPLACED` rebuilds too.
                    if (tr.docChanged && (tr.getMeta(REPLACED) || !sameStructure(tr.before, tr.doc))) this.all = true;
                    if (this.all) return this.build(state.doc);
                    const dirty = new Set(this.dirty);
                    const paints = this.refresh(state.doc);
                    let set = prev.map(tr.mapping, state.doc);
                    if (dirty.size) {
                        for (const r of blockRanges(state.doc, dirty)) set = set.remove(set.find(r.from, r.to));
                        set = set.add(state.doc, decorationsFor(state.doc, paints, this.shown, dirty));
                    }
                    return set;
                },
            },
            props: { decorations: state => key.getState(state) ?? null },
        });
    }

    /** Mark one block's paints stale, or every block's. */
    mark(bid?: string): void {
        if (bid === undefined) this.all = true;
        else this.dirty.add(bid);
    }

    /** Whether every block is already marked. */
    get allMarked(): boolean {
        return this.all;
    }

    private build(doc: PMNode): DecorationSet {
        return DecorationSet.create(doc, decorationsFor(doc, this.refresh(doc), this.shown));
    }

    /** Recompute the marked paints, clear the marks, and return the paints. */
    private refresh(doc: PMNode): Map<string, Paint[]> {
        const { projection, history } = this.shown;
        const viewer = this.shown.viewer();
        if (this.all) {
            this.byBlock.clear();
            this.featureOf.clear();
            const byId = new Map(projection.blocks.map(b => [b.id, b]));
            let fid: string | null = null;
            doc.forEach(block => {
                const bid = block.attrs.bid as string | null;
                if (!bid) return;
                if (block.type.name === 'heading') fid = bid;
                this.featureOf.set(bid, fid);
                const pb = byId.get(bid);
                if (pb) this.byBlock.set(bid, onPage(paintsFor(pb, projection, history, viewer), pb, this.shadows.get(bid)));
            });
        } else {
            for (const bid of this.dirty) {
                if (!this.featureOf.has(bid)) continue;
                const pb = projection.blocks.find(b => b.id === bid);
                if (pb) this.byBlock.set(bid, onPage(paintsFor(pb, projection, history, viewer), pb, this.shadows.get(bid)));
                else this.byBlock.delete(bid);
            }
        }
        this.all = false;
        this.dirty.clear();
        return this.byBlock;
    }
}
