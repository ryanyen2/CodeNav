/**
 * adopt.ts: a projection becomes ProseMirror nodes, per block.
 *
 * `blockNode` builds one block's node from its projected Text field. For a
 * heading or a prose block that is the live text with the projection's spans
 * as marks, plan-tagged runs as `plan` marks and suggested deletions as
 * `suggest_del` marks; for a source block (table, diagram, code) it is the raw
 * `source`. Any other kind is a card (editor/card.ts) whose node carries the
 * projected fields and its layout; the card keeps its own
 * shadows. `adoptionPlan` says which blocks to replace, insert, remove or
 * reorder given the document's current blocks and the set of held blocks
 * (editor/outbox.ts). Pure: no editor, no DOM.
 */
import type { Node as PMNode, Schema } from '@tiptap/pm/model';

import { SOURCE, isBuiltin, layoutOf, refsOf, textField, type ProjectedBlock, type Projection, type TextField } from './projection';
import { Shadow } from './shadow';

/** The blocks the document shows: everything in the reading flow. Comment
 *  threads, and any kind whose layout says it is out of the flow, are not;
 *  the editor draws comments in the margin beside their anchor. */
export function flowBlocks(proj: Projection): ProjectedBlock[] {
    return proj.blocks.filter(b => b.kind !== 'comment' && b.layout?.flow !== false);
}

/** One block's node with its marks, and the shadow that matches it. */
export function blockNode(schema: Schema, b: ProjectedBlock): { node: PMNode; shadow: Shadow | null } {
    if (SOURCE.has(b.kind)) {
        const f = textField(b, 'source');
        const attrs = { bid: b.id, head: b.head, kind: b.kind, lang: (b.fields.lang as string | undefined) || null, derived: b.fields.derived === true };
        const node = schema.nodes.source.create(attrs, f?.text ? schema.text(f.text) : undefined);
        return { node, shadow: f ? Shadow.fromField(f.text, refsOf(f)) : null };
    }
    if (!isBuiltin(b.kind)) {
        return { node: schema.nodes.kindBlock.create({ bid: b.id, head: b.head, kind: b.kind, fields: b.fields, layout: layoutOf(b) }), shadow: null };
    }
    const f = textField(b);
    const type = schema.nodes[b.kind];
    const attrs = b.kind === 'paragraph' ? { bid: b.id, head: b.head } : { bid: b.id, head: b.head, level: b.level || 1 };
    if (!f) return { node: type.create(attrs), shadow: null };
    return { node: type.create(attrs, inlineFrom(schema, f)), shadow: Shadow.fromField(f.text, refsOf(f)) };
}

/** Inline content for a field: text split at every mark boundary. */
function inlineFrom(schema: Schema, f: TextField): PMNode[] {
    const ranges: MarkRange[] = [];
    for (const s of f.spans) ranges.push({ start: s.start, end: s.end, name: s.kind, attrs: s.attrs });
    for (const s of f.suggest_del) ranges.push({ start: s.start, end: s.end, name: 'suggest_del', attrs: { plan: s.plan } });
    for (const r of f.runs) if (r.plan) ranges.push({ start: r.start, end: r.end, name: 'plan', attrs: { pid: r.plan } });
    return inlineNodes(schema, f.text, ranges);
}

export interface MarkRange { start: number; end: number; name: string; attrs: Record<string, string> }

/** `text` as text nodes carrying the marks of `ranges` (scalar offsets, end
 *  exclusive). A mark the schema does not know is left out. */
export function inlineNodes(schema: Schema, text: string, ranges: MarkRange[]): PMNode[] {
    const chars = Array.from(text);
    if (!chars.length) return [];
    const known = ranges.filter(r => r.end > r.start && schema.marks[r.name]);
    const cuts = new Set<number>([0, chars.length]);
    for (const r of known) { cuts.add(Math.min(r.start, chars.length)); cuts.add(Math.min(r.end, chars.length)); }
    const points = Array.from(cuts).sort((a, b) => a - b);
    const out: PMNode[] = [];
    for (let i = 0; i + 1 < points.length; i++) {
        const [a, b] = [points[i], points[i + 1]];
        const marks = known.filter(r => r.start <= a && r.end >= b).map(r => schema.marks[r.name].create(r.attrs));
        out.push(schema.text(chars.slice(a, b).join(''), marks));
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
 * leaving `held` blocks exactly as they are. Order is the
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
        // A held block the page does not have is one the person removed:
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
