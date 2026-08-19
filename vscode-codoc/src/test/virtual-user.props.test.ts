/**
 * virtual-user.props.test.ts — the harness the tracking engine never had: one where
 * a KEYSTROKE and a SETTLE are different events.
 *
 * Every prior test in this suite models an edit and its acceptance as the same
 * instant: one transaction, one settle, a baseline that advances immediately. Real
 * editing has three clocks that never lined up — the settle debounce (many
 * keystrokes per command), the projection round-trip (the daemon answers later, and
 * the answer is stale by the time it lands), and the agent writing in parallel. The
 * failure class that matters lives in the gaps between them, and the gaps did not
 * exist anywhere in the suite.
 *
 * So this drives a REAL EditorState through a script of real transactions with a
 * real plugin stack, against a MODEL DAEMON that applies commands the way Loop B
 * does — including refusing a command whose base has moved — and pushes projections
 * back on its own schedule. Settles happen when the script pauses, not when it
 * types. Undo, joins, selection deletes, PASTE, IME composition and drag-to-reorder
 * are all in the alphabet, because those are the gestures that were never exercised.
 *
 * It also models the two roles the client splits into, because their disagreement was
 * a data-loss bug in its own right (review findings #2/#7): the EDITOR owns which
 * projection its content was typed against (`cited`), and the HOST owns the baselines +
 * the optimistic overlay a command's `base_text` comes from — through the real
 * `EditProvenance`, not a re-implementation, so client/daemon drift cannot hide in the
 * harness. The sequence that matters is a projection arriving while the author is
 * mid-word: the arrival FLUSHES the unsent text, and that flush must cite the baseline the
 * text was typed against, not the projection that triggered it.
 *
 * Invariants (see the architecture doc's N-series):
 *   N1  no silent loss  — settled text reaches the store or stays visibly pending
 *   N2  no silent revert — a write the author never saw is never overwritten as if it
 *                          were their own base (the #2/#7 class)
 *   N3  net-no-op ⇒ ∅   — an edit sequence that composes to nothing emits nothing
 *   N5  convergence     — at quiescence, editor and store agree
 *   I1  no destruction  — no sequence of messy editing emits a retire
 *   I-total             — nothing throws, on any script
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState, TextSelection, Transaction } from '@tiptap/pm/state';
import { history, undo, redo } from '@tiptap/pm/history';
import { codocSchema } from '../webview/tiptap/schema';
import { uniqueLocalIdPlugin } from '../webview/tiptap/feature-heading';
import { keepParagraphOwnerPlugin } from '../webview/tiptap/paragraph-owner';
import { markHygienePlugin } from '../webview/tiptap/mark-hygiene';
import { backspaceVerdict, verdictTransaction } from '../webview/tiptap/block-boundary';
import { commandsForSettle, featureUnits, type FeatureUnit } from '../state/commands-from-doc';
import { EditProvenance } from '../state/edit-provenance';
import { gateProjection } from '../webview/doc-gate';
import { normalizeDescription, type PMNode } from '../state/pm-doc';
import type { CommandEntry } from '../state/edits-channel';

const schema = codocSchema();

/** Deterministic RNG — a failing seed is a reproducible script. */
function mulberry32(seed: number): () => number {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
const pick = <T>(rng: () => number, xs: readonly T[]): T => xs[Math.floor(rng() * xs.length) % xs.length];
const int = (rng: () => number, lo: number, hi: number): number => lo + Math.floor(rng() * (hi - lo + 1));

// ── the model daemon ─────────────────────────────────────────────────────────

interface StoredFeature { title: string; description: string; version: number; retired: boolean }

/**
 * A store that applies commands the way Loop B does — the same identity keying, the
 * same base check, the same per-feature version bump. It is the reference the
 * client's beliefs are checked against.
 */
class ModelStore {
    private features = new Map<string, StoredFeature>();
    private order: string[] = [];               // sibling order — what `rank` keys give the real store
    private applied = new Set<string>();
    private writers = new Map<string, string>();
    private clock = 1;
    conflicts = 0;
    merges = 0;
    /** N2's oracle: a foreign write the EDITOR has not adopted yet, per feature. Cleared
     *  by `observe` when a projection carrying it is adopted. While an entry stands, any
     *  command that overwrites that exact text on the CLEAN path (base == current, no
     *  merge, no arbitration, no record) is a silent revert — the author is being credited
     *  with having seen text they never saw. */
    private foreignUnseen = new Map<string, string>();
    silentReverts = 0;

    /** Simulate a write by somebody other than the editing session under test. */
    foreignWrite(fid: string, description: string): void {
        const f = this.features.get(fid);
        if (!f || f.retired) return;
        f.description = description;
        f.version = this.clock++;
        this.writers.set(fid, 'agent');
        this.foreignUnseen.set(fid, description);
    }

    /** The editor adopted these units — anything they show is now text the author saw. */
    observe(units: readonly FeatureUnit[]): void {
        for (const u of units) {
            if (u.fid && this.foreignUnseen.get(u.fid) === u.description) this.foreignUnseen.delete(u.fid);
        }
    }

    seed(fid: string, title: string, description: string): void {
        this.features.set(fid, { title, description, version: this.clock++, retired: false });
        this.order.push(fid);
    }
    get(fid: string): StoredFeature | undefined { return this.features.get(fid); }
    live(): Array<[string, StoredFeature]> {
        return this.order.flatMap(fid => {
            const f = this.features.get(fid);
            return f && !f.retired ? [[fid, f] as [string, StoredFeature]] : [];
        });
    }

    /** A freshly minted feature enters the order where its anchors say, not at the end:
     *  a heading typed BETWEEN two features must land there (the `add` anchors). */
    private reposition_new(fid: string, afterId: string, beforeId: string): void {
        this.order.push(fid);
        this.reposition(fid, afterId, beforeId);
    }

    /** Reposition a feature between the siblings a move command names — the model of
     *  `store.rank_between`. Anchors are identities, never indices, so a concurrent
     *  add/retire cannot turn "after A" into a different place. */
    private reposition(fid: string, afterId: string, beforeId: string): void {
        const at = this.order.indexOf(fid);
        if (at < 0) return;
        this.order.splice(at, 1);
        const anchor = afterId ? this.order.indexOf(afterId) : -1;
        if (anchor >= 0) { this.order.splice(anchor + 1, 0, fid); return; }
        const before = beforeId ? this.order.indexOf(beforeId) : -1;
        if (before >= 0) { this.order.splice(before, 0, fid); return; }
        this.order.push(fid);   // no opinion about order → append (the pre-ordering behaviour)
    }

    apply(commands: readonly CommandEntry[]): void {
        for (const c of commands) {
            if (this.applied.has(c.id)) continue;      // the ledger
            this.applied.add(c.id);
            if (c.kind === 'add') {
                // Mint under the localId so the projection can echo an fid back.
                this.features.set(`f-${c.local_id}`, {
                    title: c.payload?.title ?? '', description: c.payload?.description ?? '',
                    version: this.clock++, retired: false,
                });
                this.reposition_new(`f-${c.local_id}`, c.payload?.after_id ?? '', c.payload?.before_id ?? '');
                continue;
            }
            const f = c.feature_id ? this.features.get(c.feature_id) : undefined;
            if (!f || f.retired) continue;             // vanished / tombstone guard
            if (c.base_text !== undefined) {
                const current = c.kind === 'set_title' ? f.title : f.description;
                const same = c.kind === 'set_title'
                    ? c.base_text.trim() === current.trim()
                    : normalizeDescription(c.base_text) === normalizeDescription(current);
                // A base that trails is only a disagreement when somebody ELSE wrote
                // the current text: an author outrunning the projection round-trip is
                // continuing their own work.
                const mine = !!c.session && this.writers.get(c.feature_id!) === c.session;
                if (same) {
                    // The CLEAN path: apply verbatim, no merge, no record anywhere. Sound
                    // only if base_text really is what the author last knew — so if it is
                    // text an unadopted foreign write put there, this is the silent revert.
                    if (this.foreignUnseen.get(c.feature_id!) === current) this.silentReverts++;
                } else if (mine) {
                    this.merges += 1;                  // continuation: merge, don't refuse
                } else {
                    this.conflicts++; continue;
                }
            }
            if (c.kind === 'set_title') f.title = c.payload?.title ?? f.title;
            if (c.kind === 'set_description') f.description = c.payload?.description ?? f.description;
            if (c.kind === 'retire') f.retired = true;
            if (c.kind === 'move') {
                this.reposition(c.feature_id!, c.payload?.after_id ?? '', c.payload?.before_id ?? '');
                f.version = this.clock++;
                continue;   // structural: no text, and NO writer stamp (review finding #15)
            }
            f.version = this.clock++;
            if (c.session) this.writers.set(c.feature_id!, c.session);
        }
    }

    /** The store→doc projection, in the shape `build_doc_from_store` emits. */
    project(): PMNode {
        const content: unknown[] = [];
        for (const [fid, f] of this.live()) {
            content.push({
                type: 'featureHeading',
                attrs: { fid, localId: null, level: 0, retired: false, realized: true,
                         version: String(f.version).padStart(12, '0') },
                content: f.title ? [{ type: 'text', text: f.title }] : [],
            });
            for (const para of f.description.split('\n\n')) {
                content.push({
                    type: 'paragraph', attrs: { ownerId: fid },
                    content: para ? [{ type: 'text', text: para }] : [],
                });
            }
        }
        return { type: 'doc', content } as unknown as PMNode;
    }
}

// ── the virtual user ─────────────────────────────────────────────────────────

function makeState(doc: PMNode): EditorState {
    return EditorState.create({
        schema,
        doc: PMNodeType.fromJSON(schema, doc as never),
        plugins: [
            history(),
            uniqueLocalIdPlugin(schema.nodes.featureHeading),
            keepParagraphOwnerPlugin(),
            markHygienePlugin(),
        ],
    });
}

/** Put the caret somewhere plausible — inside a block, not always at its start. */
function randomCaret(state: EditorState, rng: () => number): EditorState {
    const blocks: number[] = [];
    state.doc.forEach((node, pos) => { if (node.isTextblock) blocks.push(pos); });
    if (!blocks.length) return state;
    const pos = pick(rng, blocks);
    const node = state.doc.nodeAt(pos);
    const offset = node ? int(rng, 0, node.content.size) : 0;
    const target = Math.min(pos + 1 + offset, state.doc.content.size - 1);
    return state.apply(state.tr.setSelection(TextSelection.near(state.doc.resolve(Math.max(1, target)))));
}

type Op = 'type' | 'backspace' | 'split' | 'selectDelete' | 'undo' | 'redo'
        | 'paste' | 'ime' | 'drag';
const OPS: readonly Op[] = ['type', 'type', 'type', 'backspace', 'split', 'selectDelete',
                            'undo', 'redo', 'paste', 'ime', 'drag'];

/** The block run a feature owns — heading plus prose up to the next heading. What a drag
 *  moves (feature-drag.ts): the heading alone would leave its prose under a stranger. */
function featureSliceAt(state: EditorState, headingPos: number): { from: number; to: number } {
    let from = -1, to = state.doc.content.size, seen = false;
    state.doc.forEach((node, pos) => {
        if (pos === headingPos) { from = pos; to = pos + node.nodeSize; seen = true; return; }
        if (!seen) return;
        if (node.type.name === 'featureHeading') { seen = false; return; }
        to = pos + node.nodeSize;
    });
    return { from, to };
}

function headingPositions(state: EditorState): number[] {
    const out: number[] = [];
    state.doc.forEach((node, pos) => { if (node.type.name === 'featureHeading') out.push(pos); });
    return out;
}

/** One keystroke-scale action. Returns the state after it (unchanged if inapplicable). */
function act(state: EditorState, op: Op, rng: () => number): EditorState {
    const { $from, from } = state.selection;
    switch (op) {
        case 'type':
            return state.apply(state.tr.insertText(pick(rng, ['a', 'b', ' ', 'x', 'z']), from));
        case 'backspace': {
            // Through the real guard, exactly as the keymap routes it.
            const tr = verdictTransaction(state, backspaceVerdict(state));
            if (tr) return state.apply(tr);
            if (from <= 1) return state;
            return state.apply(state.tr.delete(from - 1, from));
        }
        case 'split':
            return $from.parent.type.name === 'featureHeading'
                ? state   // Enter in a heading is intercepted by the editor, not a split
                : state.apply(state.tr.split(from));
        case 'selectDelete': {
            const to = Math.min(from + int(rng, 1, 6), state.doc.content.size - 1);
            if (to <= from) return state;
            const sel = TextSelection.between(state.doc.resolve(from), state.doc.resolve(to));
            if (sel.from === sel.to) return state;
            return state.apply(state.tr.delete(sel.from, sel.to));
        }
        case 'paste': {
            // A slice, not a keystroke: whole blocks land at once and a pasted heading has
            // no localId of its own — the mint plugin has to key it, or the settle emits an
            // `add` nothing can correlate a minted fid back to.
            const heads = headingPositions(state);
            const at = heads.length ? pick(rng, heads) : 0;
            return state.apply(state.tr.insert(at, [
                schema.nodes.paragraph.create({ ownerId: null }, schema.text('pasted prose')),
                schema.nodes.featureHeading.create(
                    { fid: null, localId: null, level: 0, retired: false, realized: true },
                    schema.text('Pasted')),
            ]));
        }
        case 'ime':
            // Provisional composition text. The caller sets `composing`, which suppresses
            // the settle and defers any arriving projection until the commit — the window
            // where the editor's content and the newest baseline disagree the longest.
            return state.apply(state.tr.insertText('にぽ', from));
        case 'drag': {
            const heads = headingPositions(state);
            if (heads.length < 2) return state;
            const { from: f, to: t } = featureSliceAt(state, pick(rng, heads));
            if (f < 0 || t <= f) return state;
            const slice = state.doc.slice(f, t).content;
            const cut = state.apply(state.tr.delete(f, t));
            const targets = [0, ...headingPositions(cut), cut.doc.content.size];
            const at = Math.min(pick(rng, targets), cut.doc.content.size);
            return cut.apply(cut.tr.insert(at, slice));
        }
        case 'undo': {
            let out = state;
            undo(state, (tr: Transaction) => { out = state.apply(tr); });
            return out;
        }
        case 'redo': {
            let out = state;
            redo(state, (tr: Transaction) => { out = state.apply(tr); });
            return out;
        }
    }
}

// ── the two client halves ────────────────────────────────────────────────────

/**
 * The HOST (tree-editor.ts): reads projections and turns a settled doc into commands.
 * Everything load-bearing is delegated to the production `EditProvenance`; what is
 * modelled here is only the ORDER the host does things in.
 */
class ModelHost {
    private readonly provenance: EditProvenance;
    private seq = 0;
    private emissions = 0;

    constructor(private readonly store: ModelStore, session: string) {
        this.provenance = new EditProvenance(session);
    }

    /** buildPayload: read the projection, stamp a monotonic baselineId, and let the
     *  provenance book record it as citable + retire the overlay entries it confirms. */
    projection(): { doc: PMNode; baselineId: number } {
        const doc = this.store.project();
        const baselineId = ++this.seq;
        this.provenance.observe(featureUnits(doc), baselineId);
        return { doc, baselineId };
    }

    /** settleDoc: diff against the baseline the settle CITES, apply, then record what was
     *  sent (the host's own order — a command that never reached the log never happened). */
    settle(doc: PMNode, cited: number | undefined): CommandEntry[] {
        const cmds = this.provenance.settle(featureUnits(doc), cited, `t${++this.emissions}`);
        this.store.apply(cmds);
        this.provenance.record(cmds);
        return cmds;
    }
}

/**
 * The EDITOR (whole-doc-editor.ts): holds the live doc, the per-feature version gate, and
 * — the part that was missing — the citation. `cited` advances at the END of an adopt, so
 * a settle flushed BY an arriving projection still names the baseline its text was typed
 * against. Naming the arriving one instead was review finding #2.
 */
class ModelEditor {
    state: EditorState;
    dirty = false;
    composing = false;
    cited: number | undefined;
    private deferred: { doc: PMNode; baselineId: number } | null = null;
    private readonly localVersions = new Map<string, string>();
    private readonly pendingFids = new Set<string>();

    constructor(doc: PMNode, private readonly host: ModelHost, private readonly store: ModelStore) {
        this.state = makeState(doc);
    }

    /** The debounce firing (or any flush): nothing while composing — settling provisional
     *  text would mint a permanent, id-stamped edit from a half-typed word. */
    settle(): CommandEntry[] {
        if (!this.dirty || this.composing) return [];
        this.dirty = false;
        return this.host.settle(this.state.doc.toJSON() as PMNode, this.cited);
    }

    /** A projection lands. Flush FIRST (the unsent text belongs to the old baseline), then
     *  gate per feature, then adopt the citation. While composing, defer the whole payload
     *  — keeping only the latest — exactly as the doc gate does. */
    arrive(payload: { doc: PMNode; baselineId: number }): CommandEntry[] {
        if (this.composing) { this.deferred = payload; return []; }
        const flushed = this.settle();
        const gate = gateProjection({
            incoming: payload.doc, local: this.state.doc.toJSON() as PMNode,
            localVersions: this.localVersions, pendingFids: this.pendingFids,
        });
        for (const [fid, v] of gate.adopted) { this.localVersions.set(fid, v); this.pendingFids.delete(fid); }
        this.state = makeState(gate.doc);
        // What the author has now SEEN: the PROJECTED text of every feature the gate
        // adopted. Not the gated doc's text — unowned prose (a paste, a split) attributes
        // positionally, so the rendered slice can differ from the projection by a
        // paragraph the author moved there themselves. And not the kept-local features:
        // those still show the author's own text, so a write inside them is still unseen.
        const adopted = new Set(gate.adopted.keys());
        this.store.observe(featureUnits(payload.doc).filter(u => u.fid && adopted.has(u.fid)));
        this.cited = payload.baselineId;
        return flushed;
    }

    /** Composition committed: the deferred projection (if any) lands now. */
    endComposing(): CommandEntry[] {
        this.composing = false;
        const p = this.deferred;
        this.deferred = null;
        return p ? this.arrive(p) : [];
    }
}

interface RunStats {
    settles: number; commands: number; retires: number; conflicts: number;
    noopSettles: number; edited: number; silentReverts: number;
    byKind: Record<string, number>;
}

/**
 * Drive one script. `settleEvery` keystrokes produce ONE settle (the debounce), the
 * daemon's projection lands `projectionLag` settles later (the round trip), and — when
 * `agentEvery` is set — somebody else writes into the tree while the author types.
 */
function runScript(seed: number, opts: {
    steps: number; settleEvery: number; projectionLag: number; agentEvery?: number;
}): RunStats {
    const rng = mulberry32(seed);
    const store = new ModelStore();
    store.seed('f-1', 'Auth', 'Validates the session token.');
    store.seed('f-2', 'Theme', 'Light and dark.');

    const SESSION = `sess-${seed}`;
    const host = new ModelHost(store, SESSION);
    const first = host.projection();
    const editor = new ModelEditor(first.doc, host, store);
    editor.cited = first.baselineId;
    const inFlight: Array<{ at: number; payload: { doc: PMNode; baselineId: number } }> = [];
    const stats: RunStats = { settles: 0, commands: 0, retires: 0, conflicts: 0,
                              noopSettles: 0, edited: 0, silentReverts: 0, byKind: {} };
    const record = (cmds: CommandEntry[]): void => {
        stats.commands += cmds.length;
        stats.retires += cmds.filter(c => c.kind === 'retire').length;
        if (!cmds.length) stats.noopSettles++;
    };

    for (let step = 0; step < opts.steps; step++) {
        if (editor.composing) {
            // The commit half of the composition: the provisional text becomes the word.
            const { from } = editor.state.selection;
            const at = Math.max(1, from - 2);
            editor.state = editor.state.apply(
                editor.state.tr.replaceWith(at, from, schema.text('日本')));
            editor.dirty = true;
            record(editor.endComposing());
        } else {
            editor.state = randomCaret(editor.state, rng);
            const before = editor.state.doc;
            const op = pick(rng, OPS);
            editor.state = act(editor.state, op, rng);
            if (editor.state.doc !== before) {
                stats.edited++;
                stats.byKind[op] = (stats.byKind[op] ?? 0) + 1;
                editor.dirty = true;
                if (op === 'ime') editor.composing = true;
            }
        }

        // Somebody else writes — an agent amend landing while the author is mid-word.
        if (opts.agentEvery && step % opts.agentEvery === opts.agentEvery - 1) {
            store.foreignWrite(pick(rng, ['f-1', 'f-2']), `agent text @${step}`);
            inFlight.push({ at: stats.settles + opts.projectionLag, payload: host.projection() });
        }

        if (step % opts.settleEvery !== opts.settleEvery - 1) continue;

        // ── the debounce fires ──
        stats.settles++;
        const cmds = editor.settle();          // I-total
        record(cmds);
        if (cmds.length) inFlight.push({ at: stats.settles + opts.projectionLag, payload: host.projection() });

        // ── projections land on their own schedule, flushing whatever is unsent ──
        while (inFlight.length && inFlight[0].at <= stats.settles) {
            record(editor.arrive(inFlight.shift()!.payload));
        }
    }
    stats.conflicts = store.conflicts;
    stats.silentReverts = store.silentReverts;
    return stats;
}

// ── the properties ───────────────────────────────────────────────────────────

const SEEDS = Array.from({ length: 120 }, (_u, i) => i * 7919 + 13);

describe('virtual user — keystrokes and settles are different events', () => {
    it('I-total: no script throws, at any debounce or projection lag', () => {
        for (const seed of SEEDS) {
            for (const settleEvery of [1, 3, 5]) {
                expect(() => runScript(seed, { steps: 24, settleEvery, projectionLag: 1 })).not.toThrow();
            }
        }
    });

    it('I1: messy editing never emits a retire, however the clocks interleave', () => {
        // The crown jewel, now under a debounce and a lagging projection rather than
        // the instant-acceptance model. Deleting a heading, joining blocks, undoing
        // past a projection — none of it may destroy a feature.
        let edited = 0;
        for (const seed of SEEDS) {
            const s = runScript(seed, { steps: 30, settleEvery: 3, projectionLag: 2 });
            expect(s.retires).toBe(0);
            edited += s.edited;
        }
        expect(edited).toBeGreaterThan(500);   // anti-vacuity: the scripts really edited
    });

    it('N3: a settle that changed nothing emits nothing', () => {
        // Many keystrokes per settle means most settles carry real change; the ones
        // that do not must be silent. A settle emitting a command for text identical
        // to what the store holds is the churn that mints directives for non-edits.
        let noop = 0;
        for (const seed of SEEDS) {
            const s = runScript(seed, { steps: 24, settleEvery: 4, projectionLag: 1 });
            noop += s.noopSettles;
        }
        expect(noop).toBeGreaterThan(0);   // they occur, and cost nothing when they do
    });

    it('N5: editor and store converge once the projections have caught up', () => {
        // Quiescence: no more typing, one last settle, every projection delivered.
        for (const seed of SEEDS.slice(0, 40)) {
            const rng = mulberry32(seed);
            const store = new ModelStore();
            store.seed('f-1', 'Auth', 'Validates the session token.');
            let state = makeState(store.project());
            let baseline = featureUnits(store.project());
            const known = new Map(baseline.filter(u => u.fid).map(u => [u.fid!, u]));

            for (let i = 0; i < 12; i++) {
                state = randomCaret(state, rng);
                state = act(state, pick(rng, ['type', 'type', 'backspace', 'split'] as const), rng);
            }
            const cmds = commandsForSettle(baseline, featureUnits(state.doc.toJSON() as PMNode), 'final', known);
            store.apply(cmds);

            // The projection now IS the store; adopting it (nothing pending) must
            // reproduce exactly what the store holds.
            const projected = featureUnits(store.project());
            baseline = featureUnits(gateProjection({
                incoming: store.project(), local: state.doc.toJSON() as PMNode,
                localVersions: new Map(), pendingFids: new Set(),
            }).doc);
            for (const u of projected) {
                const mine = baseline.find(b => b.fid === u.fid);
                expect(mine?.title).toBe(u.title);
                expect(mine?.description).toBe(u.description);
            }
        }
    });

    it('#18: paste, IME and drag actually occur — the alphabet is not aspirational', () => {
        const byKind: Record<string, number> = {};
        for (const seed of SEEDS) {
            const s = runScript(seed, { steps: 30, settleEvery: 3, projectionLag: 2 });
            for (const [k, n] of Object.entries(s.byKind)) byKind[k] = (byKind[k] ?? 0) + n;
        }
        for (const op of ['paste', 'ime', 'drag']) expect(byKind[op] ?? 0).toBeGreaterThan(0);
    });

    it('N2: a concurrent agent write is never silently reverted, whatever the interleaving', () => {
        // The branch's flagship scenario, fuzzed: the author keeps typing while an agent
        // amends features under them, and projections arrive mid-word. Every arrival
        // FLUSHES unsent text, and the flush must cite the baseline that text was typed
        // against — otherwise the agent's amend reads as a user edit that reverts it and
        // applies on the CLEAN path, with no merge and no record (findings #2 + #7).
        let reverts = 0, contended = 0;
        for (const seed of SEEDS) {
            for (const lag of [1, 2, 3]) {
                const s = runScript(seed, { steps: 30, settleEvery: 3, projectionLag: lag, agentEvery: 4 });
                reverts += s.silentReverts;
                contended += s.conflicts;
            }
        }
        expect(reverts).toBe(0);
        // Anti-vacuity: the agent and the author really did collide sometimes. If they
        // never contended, "no silent revert" would be true of an empty set.
        expect(contended).toBeGreaterThan(0);
    });

    it('N1: a settled edit is never silently dropped by the base check', () => {
        // The base check must refuse ONLY genuine disagreement. With a single author
        // and no agent writing in parallel, every settled command has to land — a
        // conflict here would mean ordinary typing stalls behind a review prompt.
        for (const seed of SEEDS) {
            const s = runScript(seed, { steps: 24, settleEvery: 3, projectionLag: 2 });
            expect(s.conflicts).toBe(0);
        }
    });
});

/**
 * The other half of N1: the base check must still CATCH a genuine disagreement.
 * A rule loose enough never to fire is the silent-overwrite behaviour it replaced.
 */
describe('base enforcement — catches a foreign write, ignores its own trail', () => {
    const setup = () => {
        const store = new ModelStore();
        store.seed('f-1', 'Auth', 'original');
        const units = featureUnits(store.project());
        return { store, units, known: new Map(units.filter(u => u.fid).map(u => [u.fid!, u])) };
    };
    const edited = (text: string): FeatureUnit[] => [{
        fid: 'f-1', localId: null, title: 'Auth', description: text, parentId: null, retired: false, realized: true,
    }];

    it('refuses to overwrite text an agent wrote after the author last looked', () => {
        const { store, units, known } = setup();
        store.foreignWrite('f-1', 'the agent rewrote this');

        store.apply(commandsForSettle(units, edited('mine, based on the old text'), 't1', known, 'sess-a'));

        expect(store.conflicts).toBe(1);
        expect(store.get('f-1')!.description).toBe('the agent rewrote this');
    });

    it('lets the same session keep typing past its own earlier commands', () => {
        const { store, units, known } = setup();
        // Two settles in a row, the second citing a base the store has already moved
        // past — because the FIRST settle moved it. Ordinary typing.
        store.apply(commandsForSettle(units, edited('one'), 't1', known, 'sess-a'));
        store.apply(commandsForSettle(units, edited('one two'), 't2', known, 'sess-a'));

        expect(store.conflicts).toBe(0);
        expect(store.get('f-1')!.description).toBe('one two');
    });

    it('treats a second window as somebody else, because it is', () => {
        const { store, units, known } = setup();
        store.apply(commandsForSettle(units, edited('window A wrote this'), 't1', known, 'sess-a'));
        store.apply(commandsForSettle(units, edited('window B wrote this'), 't2', known, 'sess-b'));

        expect(store.conflicts).toBe(1);
        expect(store.get('f-1')!.description).toBe('window A wrote this');
    });
});


/**
 * The projection-arrival flush, as one deterministic sequence rather than a fuzz seed —
 * so the mechanism of findings #2/#7 is legible and its fix is pinned by name.
 */
describe('a projection arriving mid-word flushes against the baseline it was typed on', () => {
    /** Type into a feature's first description paragraph. */
    const typeInto = (state: EditorState, fid: string, text: string): EditorState => {
        let at = -1;
        state.doc.forEach((node, pos) => {
            if (at < 0 && node.type.name === 'paragraph' && node.attrs.ownerId === fid) at = pos + 1;
        });
        return state.apply(state.tr.insertText(text, at));
    };

    const setup = () => {
        const store = new ModelStore();
        store.seed('f-1', 'Auth', 'Validates the session token.');
        store.seed('f-2', 'Theme', 'Light and dark.');
        const host = new ModelHost(store, 'sess-a');
        const first = host.projection();
        const editor = new ModelEditor(first.doc, host, store);
        editor.cited = first.baselineId;
        return { store, host, editor };
    };

    it('leaves the feature the author never touched alone', () => {
        const { store, host, editor } = setup();
        editor.state = typeInto(editor.state, 'f-1', 'mine: ');
        editor.dirty = true;

        store.foreignWrite('f-2', 'the agent rewrote this');
        const flushed = editor.arrive(host.projection());   // the arrival flushes the typing

        expect(flushed.map(c => c.feature_id)).toEqual(['f-1']);
        expect(store.get('f-2')!.description).toBe('the agent rewrote this');
        expect(store.silentReverts).toBe(0);
        expect(store.get('f-1')!.description).toContain('mine: ');
    });

    it('and the oracle is real: citing the ARRIVING baseline reverts the agent silently', () => {
        // The pre-fix behaviour, reproduced deliberately — doc-view read the module-level
        // `payload.baselineId`, which the message handler had already advanced. Without this
        // the N2 property could pass because nothing was being detected.
        const { store, host, editor } = setup();
        editor.state = typeInto(editor.state, 'f-1', 'mine: ');
        store.foreignWrite('f-2', 'the agent rewrote this');
        const arriving = host.projection();

        host.settle(editor.state.doc.toJSON() as PMNode, arriving.baselineId);

        expect(store.silentReverts).toBe(1);
        expect(store.get('f-2')!.description).toBe('Light and dark.');   // the amend is gone
    });

    it('a composition holds the projection AND the settle until the word is committed', () => {
        const { store, host, editor } = setup();
        editor.state = typeInto(editor.state, 'f-1', 'にぽ');
        editor.dirty = true;
        editor.composing = true;

        expect(editor.settle()).toEqual([]);                 // never ship a half-composed word
        expect(editor.arrive(host.projection())).toEqual([]); // and never replace the doc under it
        const cited = editor.cited;

        store.foreignWrite('f-2', 'agent wrote during the composition');
        const flushed = editor.endComposing();

        // The deferred projection lands now; the flush it triggers still cites the baseline
        // the author was typing against, so the agent's text survives.
        expect(editor.cited).not.toBe(cited);
        expect(flushed.every(c => c.feature_id === 'f-1')).toBe(true);
        expect(store.silentReverts).toBe(0);
    });
});
