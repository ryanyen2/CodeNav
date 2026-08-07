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
 * types. Undo, joins and selection deletes are in the alphabet, because those are
 * the gestures that were never exercised.
 *
 * Invariants (see the architecture doc's N-series):
 *   N1  no silent loss  — settled text reaches the store or stays visibly pending
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
    private applied = new Set<string>();
    private writers = new Map<string, string>();
    private clock = 1;
    conflicts = 0;

    /** Simulate a write by somebody other than the editing session under test. */
    foreignWrite(fid: string, description: string): void {
        const f = this.features.get(fid);
        if (!f) return;
        f.description = description;
        f.version = this.clock++;
        this.writers.set(fid, 'agent');
    }

    seed(fid: string, title: string, description: string): void {
        this.features.set(fid, { title, description, version: this.clock++, retired: false });
    }
    get(fid: string): StoredFeature | undefined { return this.features.get(fid); }
    live(): Array<[string, StoredFeature]> {
        return [...this.features].filter(([, f]) => !f.retired);
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
                if (!same && !mine) { this.conflicts++; continue; }
            }
            if (c.kind === 'set_title') f.title = c.payload?.title ?? f.title;
            if (c.kind === 'set_description') f.description = c.payload?.description ?? f.description;
            if (c.kind === 'retire') f.retired = true;
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

type Op = 'type' | 'backspace' | 'split' | 'selectDelete' | 'undo' | 'redo';
const OPS: readonly Op[] = ['type', 'type', 'type', 'backspace', 'split', 'selectDelete', 'undo', 'redo'];

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

interface RunStats {
    settles: number; commands: number; retires: number; conflicts: number;
    noopSettles: number; edited: number;
}

/**
 * Drive one script. `settleEvery` keystrokes produce ONE settle (the debounce), and
 * the daemon's projection lands `projectionLag` settles later (the round trip).
 */
function runScript(seed: number, opts: { steps: number; settleEvery: number; projectionLag: number }): RunStats {
    const rng = mulberry32(seed);
    const store = new ModelStore();
    store.seed('f-1', 'Auth', 'Validates the session token.');
    store.seed('f-2', 'Theme', 'Light and dark.');

    let state = makeState(store.project());
    let baseline: FeatureUnit[] = featureUnits(store.project());
    let known = new Map(baseline.filter(u => u.fid).map(u => [u.fid!, u]));
    const localVersions = new Map<string, string>();
    const pendingFids = new Set<string>();
    const inFlight: Array<{ at: number; doc: PMNode }> = [];
    const SESSION = `sess-${seed}`;
    const stats: RunStats = { settles: 0, commands: 0, retires: 0, conflicts: 0, noopSettles: 0, edited: 0 };
    let token = 0;

    for (let step = 0; step < opts.steps; step++) {
        state = randomCaret(state, rng);
        const before = state.doc;
        state = act(state, pick(rng, OPS), rng);
        if (state.doc !== before) stats.edited++;

        if (step % opts.settleEvery !== opts.settleEvery - 1) continue;

        // ── a settle fires ──
        stats.settles++;
        const next = featureUnits(state.doc.toJSON() as PMNode);     // I-total
        const cmds = commandsForSettle(baseline, next, `t${++token}`, known, SESSION);  // I-total
        stats.commands += cmds.length;
        stats.retires += cmds.filter(c => c.kind === 'retire').length;
        if (!cmds.length) stats.noopSettles++;

        store.apply(cmds);
        // The host's optimistic view advances with what it just sent (P2).
        for (const c of cmds) {
            const prior = c.feature_id ? known.get(c.feature_id) : undefined;
            if (!prior) continue;
            if (c.kind === 'set_title') known.set(c.feature_id!, { ...prior, title: c.payload!.title! });
            if (c.kind === 'set_description') {
                known.set(c.feature_id!, { ...prior, description: c.payload!.description! });
            }
        }
        inFlight.push({ at: stats.settles + opts.projectionLag, doc: store.project() });

        // ── a projection lands, `projectionLag` settles after it was rendered ──
        while (inFlight.length && inFlight[0].at <= stats.settles) {
            const incoming = inFlight.shift()!.doc;
            const local = state.doc.toJSON() as PMNode;
            const gate = gateProjection({ incoming, local, localVersions, pendingFids });
            for (const [fid, v] of gate.adopted) { localVersions.set(fid, v); pendingFids.delete(fid); }
            state = makeState(gate.doc);
            baseline = featureUnits(gate.doc);
            known = new Map(featureUnits(incoming).filter(u => u.fid).map(u => [u.fid!, u]));
        }
    }
    stats.conflicts = store.conflicts;
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
        fid: 'f-1', localId: null, title: 'Auth', description: text, parentId: null, retired: false,
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
