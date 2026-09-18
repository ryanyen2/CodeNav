/**
 * adopt.ts — a projection becomes ProseMirror nodes, per block (09-editor §6).
 *
 * `blockNode` builds one block's node from its projected text field: the live
 * text with the projection's spans as marks, plan-tagged runs as `plan` marks
 * and suggested deletions as `suggest_del` marks. `adoptionPlan` says which
 * blocks to replace, insert, remove or reorder given the document's current
 * blocks and the set the person is holding (unacked local ops). Pure: no
 * editor, no DOM.
 */
import type { Node as PMNode, Schema } from '@tiptap/pm/model';

import { refsOf, textField, type ProjectedBlock, type Projection, type TextField } from './projection';
import { Shadow } from './shadow';

interface Boundary { at: number; open: Array<[string, Record<string, string>]>; close: Array<[string, Record<string, string>]> }

/** The blocks the document shows: everything but comment threads, which the
 *  editor draws in the margin beside their anchor (03-extensions §4). */
export function flowBlocks(proj: Projection): ProjectedBlock[] {
    return proj.blocks.filter(b => b.kind !== 'comment');
}

/** One block's node with its marks, and the shadow that matches it. */
export function blockNode(schema: Schema, b: ProjectedBlock): { node: PMNode; shadow: Shadow | null } {
    if (b.kind !== 'heading' && b.kind !== 'paragraph') {
        // A plugin kind: read-only in this editor, no shadow, no ops from here.
        return { node: schema.nodes.kindBlock.create({ bid: b.id, head: b.head, kind: b.kind, fields: b.fields }), shadow: null };
    }
    const f = textField(b);
    const type = schema.nodes[b.kind === 'heading' ? 'heading' : 'paragraph'];
    const attrs = b.kind === 'heading' ? { bid: b.id, head: b.head, level: b.level } : { bid: b.id, head: b.head };
    if (!f) return { node: type.create(attrs), shadow: null };
    return { node: type.create(attrs, inlineFrom(schema, f)), shadow: Shadow.fromField(f.text, refsOf(f)) };
}

/** Inline content for a field: text split at every mark boundary. */
export function inlineFrom(schema: Schema, f: TextField): PMNode[] {
    const chars = Array.from(f.text);
    if (!chars.length) return [];
    // Collect boundaries at scalar offsets.
    const ranges: Array<{ start: number; end: number; name: string; attrs: Record<string, string> }> = [];
    for (const s of f.spans) if (s.end > s.start) ranges.push({ start: s.start, end: s.end, name: s.kind, attrs: s.attrs });
    for (const s of f.suggest_del) if (s.end > s.start) ranges.push({ start: s.start, end: s.end, name: 'suggest_del', attrs: { plan: s.plan } });
    for (const r of f.runs) if (r.plan && r.end > r.start) ranges.push({ start: r.start, end: r.end, name: 'plan', attrs: { pid: r.plan } });
    const cuts = new Set<number>([0, chars.length]);
    for (const r of ranges) { cuts.add(r.start); cuts.add(r.end); }
    const points = Array.from(cuts).sort((a, b) => a - b);
    const out: PMNode[] = [];
    for (let i = 0; i + 1 < points.length; i++) {
        const [a, b] = [points[i], points[i + 1]];
        const marks = ranges.filter(r => r.start <= a && r.end >= b && schema.marks[r.name])
            .map(r => schema.marks[r.name].create(r.attrs));
        const text = chars.slice(a, b).join('');
        // A newline inside a field is a hard break in the editor.
        const parts = text.split('\n');
        parts.forEach((part, j) => {
            if (part) out.push(schema.text(part, marks));
            if (j < parts.length - 1) out.push(schema.nodes.hardBreak.create());
        });
    }
    return out;
}

export interface AdoptionStep {
    kind: 'replace' | 'insert' | 'remove' | 'move';
    bid: string;
    /** index in the target order */
    index: number;
}

/**
 * What to do to bring the document's blocks in line with the projection,
 * leaving `held` blocks (unacked local ops) exactly as they are. Order is the
 * projection's; a held block keeps its content but takes its projected place.
 */
export function adoptionPlan(current: Array<{ bid: string; head: number }>, proj: Projection, held: ReadonlySet<string>): AdoptionStep[] {
    const target = flowBlocks(proj);
    const have = new Map(current.map((c, i) => [c.bid, { ...c, index: i }]));
    const want = new Map(target.map((b, i) => [b.id, { b, index: i }]));
    const steps: AdoptionStep[] = [];
    for (const [bid, c] of have) {
        if (!want.has(bid) && !held.has(bid)) steps.push({ kind: 'remove', bid, index: c.index });
    }
    target.forEach((b, i) => {
        const c = have.get(b.id);
        // A held block the page does not have is one the person just removed:
        // the projection predates the gesture and does not get to bring it back.
        if (!c) { if (!held.has(b.id)) steps.push({ kind: 'insert', bid: b.id, index: i }); }
        else if (!held.has(b.id) && c.head !== b.head) steps.push({ kind: 'replace', bid: b.id, index: i });
    });
    // Order: any block whose relative position changed moves.
    const kept = current.filter(c => want.has(c.bid)).map(c => c.bid);
    const wantedOrder = target.map(b => b.id).filter(id => have.has(id));
    kept.forEach((bid, i) => { if (wantedOrder[i] !== bid) steps.push({ kind: 'move', bid, index: wantedOrder.indexOf(bid) }); });
    return steps;
}

/** Build a whole document from a projection (first mount, or a full reload). */
export function docFrom(schema: Schema, proj: Projection): { doc: PMNode; shadows: Map<string, Shadow> } {
    const shadows = new Map<string, Shadow>();
    const nodes: PMNode[] = [];
    for (const b of flowBlocks(proj)) {
        const { node, shadow } = blockNode(schema, b);
        nodes.push(node);
        if (shadow) shadows.set(b.id, shadow);
    }
    return { doc: schema.nodes.doc.create(null, nodes), shadows };
}
