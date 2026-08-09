/**
 * commands-from-doc.sequence.props.test.ts — Phase 3: SEQUENCE-level robustness proof.
 *
 * The pairwise property harness (commands-from-doc.props.test.ts) proves that ONE settle
 * — commandsForSettle(prev, next) — is safe for any single transition. This file proves
 * the stronger claim: no *sequence* of messy, char-by-char edits can drive the system to
 * a destructive or malformed command. It drives a REAL ProseMirror EditorState — with the
 * actual keep-owner + unique-localId appendTransaction plugins firing on every
 * transaction — through a random script of taxonomy-derived edit operations (type, split,
 * merge, delete a heading/paragraph, insert a heading above prose, toggle retire, PASTE a
 * multi-block slice, an IME composition, and a drag-to-reorder). After EACH gesture it
 * serializes the live doc, runs featureUnits + commandsForSettle against the evolving
 * baseline, and asserts the invariants at every intermediate settle.
 *
 * The last three arrive as SEVERAL transactions with no settle between them, which is
 * where the interesting states are: a composition holds provisional text, and a drag
 * passes through a doc that is briefly missing the feature it is moving. An alphabet of
 * single-transaction keystrokes never visits either (review finding #18).
 *
 * Crown-jewel invariant (I1): if the user NEVER toggles a retired flag, ZERO retire
 * commands are emitted across the whole sequence — messy editing cannot destroy a feature.
 * With retire toggles allowed, retires correspond EXACTLY to false→true flag transitions.
 * Plus: featureUnits/commandsForSettle never throw (I-total), every command names a live
 * identity in the settled doc, and every `add` is localId-keyed (never carries a fid).
 *
 * Determinism: a seeded mulberry32 drives op selection AND localId minting, so a failing
 * sequence is reproducible from its (seed) and shrinkable into a permanent regression.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState, Transaction } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { uniqueLocalIdPlugin } from '../webview/tiptap/feature-heading';
import { keepParagraphOwnerPlugin } from '../webview/tiptap/paragraph-owner';
import { commandsForSettle, featureUnits, FeatureUnit } from '../state/commands-from-doc';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, PMNode } from '../state/pm-doc';
import type { CommandEntry } from '../state/edits-channel';

const schema = codocSchema();

// ── Seeded PRNG (mulberry32) — reproducible without Math.random ────────────────
function mulberry32(seed: number): () => number {
    let a = seed >>> 0;
    return () => {
        a |= 0; a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
const int = (rng: () => number, lo: number, hi: number) => lo + Math.floor(rng() * (hi - lo + 1));

// ── A random starting projection: headings (fids) + owned description paragraphs ───
function startDoc(rng: () => number): PMNode {
    const n = int(rng, 1, 4);
    const blocks: PMNode[] = [];
    let depth = 0;
    for (let i = 0; i < n; i++) {
        const level = Math.max(0, Math.min(depth + int(rng, -1, 1), 3));
        depth = level;
        blocks.push(featureHeadingNode(
            { fid: `f-${i}`, localId: `lid-${i}`, level, retired: false, realized: true },
            textToInlineRuns(`Feature ${i}`)));
        // 0–2 owned description paragraphs (as a real projection stamps them).
        for (let p = 0, k = int(rng, 0, 2); p < k; p++) {
            blocks.push(paragraphNode(textToInlineRuns(`f${i} para ${p}`), `f-${i}`));
        }
    }
    return makeDoc(blocks);
}

interface BlockPos { pos: number; name: string; size: number; contentSize: number; }
function topBlocks(state: EditorState): BlockPos[] {
    const out: BlockPos[] = [];
    state.doc.forEach((node, pos) => out.push(
        { pos, name: node.type.name, size: node.nodeSize, contentSize: node.content.size }));
    return out;
}

/** The block run a feature owns: its heading plus every block up to the next heading.
 *  This is the slice feature-drag.ts moves, so a modelled drag has to move the same one —
 *  taking the heading alone would leave its prose behind under whoever now precedes it. */
function featureSlice(blocks: BlockPos[], headingIdx: number): { from: number; to: number } {
    const start = blocks[headingIdx];
    let end = start.pos + start.size;
    for (let i = headingIdx + 1; i < blocks.length && blocks[i].name !== 'featureHeading'; i++) {
        end = blocks[i].pos + blocks[i].size;
    }
    return { from: start.pos, to: end };
}

/** Build a random edit against the live state, or null if no valid op exists.
 *
 *  Returns a LIST because some gestures are several transactions with no settle in
 *  between, and that gap is where the interesting failures live: an IME composition is
 *  provisional text replaced by the committed word, and a drag is a delete followed by an
 *  insert. Modelling either as one transaction would test a state the editor never has.
 *
 *  `allowRetire` gates the only legitimate destroy signal. `mint` yields deterministic
 *  localIds so the sequence is reproducible. */
interface Gesture { kind: string; trs: Transaction[]; }
function randomEdit(state: EditorState, rng: () => number, allowRetire: boolean, mint: () => string): Gesture | null {
    const blocks = topBlocks(state);
    const headings = blocks.filter(b => b.name === 'featureHeading');
    const paras = blocks.filter(b => b.name === 'paragraph');
    const kinds = ['type', 'split', 'delete', 'insertHeading', 'deleteHeadingText',
                   'paste', 'ime', 'drag'];
    if (allowRetire) kinds.push('retire');
    const kind = kinds[int(rng, 0, kinds.length - 1)];
    try {
        switch (kind) {
            case 'type': {                       // type a char into some textblock's content
                const b = blocks[int(rng, 0, blocks.length - 1)];
                return { kind, trs: [state.tr.insertText('x', b.pos + 1)] };
            }
            case 'split': {                      // press Enter inside a block (clone/split)
                const b = (paras.length ? paras : blocks)[int(rng, 0, (paras.length ? paras : blocks).length - 1)];
                const at = b.pos + 1 + int(rng, 0, b.contentSize);
                return { kind, trs: [state.tr.split(at)] };
            }
            case 'delete': {                     // delete a whole block (class A: vanish)
                if (blocks.length <= 1) return null;   // keep ≥1 block (doc needs block+)
                const b = blocks[int(rng, 0, blocks.length - 1)];
                return { kind, trs: [state.tr.delete(b.pos, b.pos + b.size)] };
            }
            case 'insertHeading': {              // insert a NEW heading at a block boundary
                const at = int(rng, 0, blocks.length) < blocks.length
                    ? blocks[int(rng, 0, blocks.length - 1)].pos : state.doc.content.size;
                const node = schema.nodes.featureHeading.create(
                    { fid: null, localId: mint(), level: int(rng, 0, 3), retired: false, realized: true },
                    schema.text('New'));
                return { kind, trs: [state.tr.insert(at, node)] };
            }
            case 'deleteHeadingText': {          // erase a heading's title char-by-char (A4)
                if (!headings.length) return null;
                const h = headings[int(rng, 0, headings.length - 1)];
                if (h.contentSize === 0) return null;
                return { kind, trs: [state.tr.delete(h.pos + 1, h.pos + 1 + h.contentSize)] };
            }
            case 'paste': {
                // A pasted slice, not a keystroke: several blocks land at once, and one of
                // them is a heading with no localId of its own. The mint plugin has to give
                // it one on the SAME transaction, or the settle emits an unkeyable add.
                const at = blocks[int(rng, 0, blocks.length - 1)].pos;
                const pasted = [
                    schema.nodes.paragraph.create({ ownerId: null }, schema.text('pasted prose')),
                    schema.nodes.featureHeading.create(
                        { fid: null, localId: null, level: int(rng, 0, 2), retired: false, realized: true },
                        schema.text('Pasted heading')),
                    schema.nodes.paragraph.create({ ownerId: null }, schema.text('more pasted prose')),
                ];
                return { kind, trs: [state.tr.insert(at, pasted)] };
            }
            case 'ime': {
                // A composition: provisional text, then the committed word replacing it. The
                // editor suppresses settles while `view.composing` is true, so both
                // transactions land before anything is emitted — which is the point, since
                // settling the provisional half would mint an id-stamped edit from garbled
                // input (whole-doc-editor's composing guard).
                const b = blocks[int(rng, 0, blocks.length - 1)];
                const at = b.pos + 1;
                const t1 = state.tr.insertText('\u306b\u307d', at);
                const mid = state.apply(t1);
                const t2 = mid.tr.replaceWith(at, at + 2, schema.text('\u65e5\u672c'));
                return { kind, trs: [t1, t2] };
            }
            case 'drag': {
                // Drag-to-reorder: the whole feature slice (heading + its prose) is cut and
                // re-inserted at another block boundary, exactly as feature-drag.ts does it.
                // Two transactions with no settle between them — the doc is briefly missing
                // a feature that is NOT being destroyed (invariant I1's hardest case).
                if (headings.length < 2) return null;
                const hIdx = blocks.findIndex(b =>
                    b === headings[int(rng, 0, headings.length - 1)]);
                const { from, to } = featureSlice(blocks, hIdx);
                const slice = state.doc.slice(from, to).content;
                const cut = state.tr.delete(from, to);
                const mid = state.apply(cut);
                // Land on a block boundary in the shortened doc (start, end, or before some block).
                const targets = [0, ...topBlocks(mid).map(b => b.pos), mid.doc.content.size];
                const at = targets[int(rng, 0, targets.length - 1)];
                return { kind, trs: [cut, mid.tr.insert(Math.min(at, mid.doc.content.size), slice)] };
            }
            case 'retire': {                     // the ONLY legitimate destroy: flag a heading
                if (!headings.length) return null;
                const h = headings[int(rng, 0, headings.length - 1)];
                const node = state.doc.nodeAt(h.pos)!;
                if (node.attrs.retired) return null;
                return { kind, trs: [state.tr.setNodeMarkup(h.pos, undefined, { ...node.attrs, retired: true })] };
            }
        }
    } catch {
        return null;                             // an invalid transaction is just a skipped op
    }
    return null;
}

/** Identities present in a settled unit list, split by whether they are minted (fid) or not. */
function identities(units: FeatureUnit[]) {
    const fids = new Set(units.filter(u => u.fid).map(u => u.fid!));
    const localIds = new Set(units.filter(u => !u.fid && u.localId).map(u => u.localId!));
    return { fids, localIds };
}

/** Assert one settle's command set is well-formed against the doc it was computed from. */
function assertSettleInvariants(cmds: CommandEntry[], prev: FeatureUnit[], next: FeatureUnit[]): void {
    const { fids, localIds } = identities(next);
    const prevByFid = new Map(prev.filter(u => u.fid).map(u => [u.fid!, u] as const));
    const retiredSeen: string[] = [];
    for (const c of cmds) {
        if (c.kind === 'add') {
            // adds are localId-keyed and NEVER carry an fid; the id is deterministic.
            expect(c.feature_id).toBeUndefined();
            expect(c.local_id && localIds.has(c.local_id)).toBe(true);
            expect(c.id).toBe(`c-add-${c.local_id}`);
        } else {
            // every other command names a live minted feature in the settled doc.
            expect(c.feature_id && fids.has(c.feature_id)).toBe(true);
            if (c.kind === 'retire') {
                retiredSeen.push(c.feature_id!);
                // I1: a retire fires ONLY where the flag went false→true vs the baseline.
                const b = prevByFid.get(c.feature_id!);
                expect(b && b.retired === false).toBe(true);
                expect(next.find(u => u.fid === c.feature_id)!.retired).toBe(true);
            }
        }
    }
    // retires ⟺ the exact set of false→true transitions (no extra, no missing).
    const transitioned = next.filter(u =>
        u.fid && prevByFid.get(u.fid)?.retired === false && u.retired).map(u => u.fid!);
    expect(new Set(retiredSeen)).toEqual(new Set(transitioned));
}

const SEEDS = Array.from({ length: 150 }, (_u, i) => i * 40503);
const STEPS = 14;

interface SeqStats { retires: number; appliedSteps: number; addsSeen: number; byKind: Record<string, number>; }
function runSequence(seed: number, allowRetire: boolean): SeqStats {
    const rng = mulberry32(seed);
    let mintN = 0;
    const mint = () => `lid-new-${seed}-${mintN++}`;
    let state = EditorState.create({
        schema, doc: PMNodeType.fromJSON(schema, startDoc(rng) as never),
        plugins: [uniqueLocalIdPlugin(schema.nodes.featureHeading), keepParagraphOwnerPlugin()],
    });
    // Baseline models the daemon's last projection: it EXCLUDES retired features (as a real
    // projection does), so an already-retired node never re-emits a retire on later settles.
    let baseline = featureUnits(state.doc.toJSON() as PMNode).filter(u => !u.retired);
    const stats: SeqStats = { retires: 0, appliedSteps: 0, addsSeen: 0, byKind: {} };
    for (let step = 0; step < STEPS; step++) {
        const g = randomEdit(state, rng, allowRetire, mint);
        if (!g || !g.trs.some(t => t.docChanged)) continue;
        // Every transaction of the gesture lands before the settle — a composition or a
        // drag is one edit to the author, however many transactions it takes.
        for (const tr of g.trs) state = state.apply(tr);
        stats.appliedSteps++;
        stats.byKind[g.kind] = (stats.byKind[g.kind] ?? 0) + 1;
        const next = featureUnits(state.doc.toJSON() as PMNode);   // I-total: must not throw
        const cmds = commandsForSettle(baseline, next, 't');  // I-total: must not throw
        assertSettleInvariants(cmds, baseline, next);
        stats.retires += cmds.filter(c => c.kind === 'retire').length;
        stats.addsSeen += cmds.filter(c => c.kind === 'add').length;
        baseline = next.filter(u => !u.retired);                   // accept + re-project model
    }
    return stats;
}

describe('property: SEQUENCE robustness — messy edit scripts never destroy a feature (I1)', () => {
    it('with NO retire gesture, zero retire commands are emitted across any sequence', () => {
        let applied = 0, adds = 0;
        const byKind: Record<string, number> = {};
        for (const seed of SEEDS) {
            const s = runSequence(seed, /* allowRetire */ false);
            expect(s.retires).toBe(0);       // the crown jewel: messy editing never retires
            applied += s.appliedSteps; adds += s.addsSeen;
            for (const [k, n] of Object.entries(s.byKind)) byKind[k] = (byKind[k] ?? 0) + n;
        }
        // The three gestures the alphabet was missing must actually occur, or adding them
        // proved nothing (review finding #18: paste, IME and drag were absent).
        for (const kind of ['paste', 'ime', 'drag']) expect(byKind[kind] ?? 0).toBeGreaterThan(0);
        // Anti-vacuous: the fuzzer must actually mutate the docs (and exercise the add path
        // — inserting headings above prose is a core taxonomy case), or "zero retires" is
        // meaningless. These are floors, not exact counts.
        expect(applied).toBeGreaterThan(SEEDS.length * 4);
        expect(adds).toBeGreaterThan(0);
    });

    it('with retire gestures allowed, all settle invariants hold + the retire path is real', () => {
        // assertSettleInvariants already pins retire ⟺ flag transition + no phantom command
        // + add-has-no-fid at every step; here we also confirm retires actually occurred, so
        // the invariant is proven against genuine retire traffic (not vacuously).
        let retires = 0;
        for (const seed of SEEDS) retires += runSequence(seed, /* allowRetire */ true).retires;
        expect(retires).toBeGreaterThan(0);
    });
});
