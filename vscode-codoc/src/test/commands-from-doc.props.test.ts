/**
 * commands-from-doc.props.test.ts — PROPERTY / FUZZ harness for the settle diff.
 *
 * The example tests in commands-from-doc.test.ts pin hand-picked scenarios. They cannot
 * catch the failure class that matters here: bugs that emerge from *sequences* of messy,
 * char-by-char edits (backspace-merge a heading, select-all delete, type above a heading,
 * a heading that lost its identity). This file instead asserts INVARIANTS over a large
 * space of randomly generated docs — well-formed AND deliberately malformed — so the
 * contract is proven "for all inputs", not spot-checked.
 *
 * Determinism: a seeded PRNG (mulberry32) drives every generator, so a failing case is
 * reproducible from its seed and can be shrunk into a permanent regression test. No
 * external fuzz dependency (fast-check) is required.
 *
 * Invariants (see docs/plans/2026-08-01-002-doc-attribution-robustness-plan.md):
 *   I-idempotence  — a doc diffed against itself yields ZERO commands (no churn).
 *   I1 no-destroy  — a heading that merely VANISHED never yields a retire; only the
 *                    explicit `retired` flag does.
 *   I-total        — featureUnits / commandsForSettle never throw on any doc, malformed
 *                    included, and are deterministic (same inputs → same output).
 *   I-content-safe — content-only edits (title/description text) emit only
 *                    set_title/set_description — never add / move / retire.
 */
import { describe, it, expect } from 'vitest';
import { commandsForSettle, featureUnits } from '../state/commands-from-doc';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns } from '../state/pm-doc';
import type { PMNode } from '../state/pm-doc';

// ── Seeded PRNG (mulberry32) — reproducible fuzzing without Math.random ────────
function mulberry32(seed: number): () => number {
    let a = seed >>> 0;
    return () => {
        a |= 0; a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
const pick = <T>(rng: () => number, xs: readonly T[]): T => xs[Math.floor(rng() * xs.length)];
const int = (rng: () => number, lo: number, hi: number): number => lo + Math.floor(rng() * (hi - lo + 1));

// ── A feature spec → its projection nodes (heading + N paragraphs) ─────────────
interface Spec {
    fid: string | null;
    localId: string | null;
    level: number;
    title: string;
    paras: string[];
    retired: boolean;
}

function specNodes(s: Spec): PMNode[] {
    const heading = featureHeadingNode(
        { fid: s.fid, level: s.level, retired: s.retired, realized: true, localId: s.localId },
        textToInlineRuns(s.title),
    );
    const paras = s.paras.map(p => paragraphNode(textToInlineRuns(p)));
    return [heading, ...paras];
}
const specsToDoc = (specs: Spec[]): PMNode => makeDoc(specs.flatMap(specNodes));

const WORDS = ['auth', 'theme', 'sync', 'parse', 'render', 'store', 'loop', '', 'edge case', 'a b c'];

/** A random spec. `malformed` widens the space: null identity, empty titles, level jumps,
 *  zero paragraphs — the shapes a live editor can transiently produce mid-edit. */
function randomSpec(rng: () => number, i: number, malformed: boolean): Spec {
    const wildIdentity = malformed && rng() < 0.25;
    return {
        fid: rng() < 0.6 ? `f-${i}` : null,
        localId: wildIdentity ? null : `lid-${i}`,
        level: malformed ? int(rng, 0, 4) : Math.min(i, int(rng, 0, 3)),
        title: pick(rng, WORDS),
        paras: Array.from({ length: malformed ? int(rng, 0, 3) : int(rng, 0, 2) }, () => pick(rng, WORDS)),
        retired: rng() < 0.15,
    };
}
function randomSpecs(rng: () => number, malformed = false): Spec[] {
    return Array.from({ length: int(rng, 0, 8) }, (_u, i) => randomSpec(rng, i, malformed));
}

// A generous corpus per property — cheap (pure functions) so we run thousands.
const SEEDS = Array.from({ length: 400 }, (_u, i) => i * 2654435761);

describe('property: I-total — never throws, deterministic (well-formed + malformed)', () => {
    it('featureUnits + commandsForSettle survive any doc and are deterministic', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            const a = specsToDoc(randomSpecs(rng, true));
            const b = specsToDoc(randomSpecs(rng, true));
            let ua: ReturnType<typeof featureUnits>, ub: typeof ua;
            expect(() => { ua = featureUnits(a); ub = featureUnits(b); }).not.toThrow();
            const c1 = commandsForSettle(ua!, ub!, 1);
            const c2 = commandsForSettle(ua!, ub!, 1);
            expect(c2).toEqual(c1);              // deterministic
        }
    });
});

describe('property: I-idempotence — a projection vs itself yields zero commands', () => {
    it('a fully-minted doc (every heading has an fid, as a store projection always is) self-diffs to []', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            // A projection from the store always carries fids; that is the steady state
            // the editor renders and re-settles against. It must never self-churn.
            const minted = randomSpecs(rng, true).map((s, i) => ({ ...s, fid: `f-${i}` }));
            const units = featureUnits(specsToDoc(minted));
            expect(commandsForSettle(units, units, seed & 0xffff)).toEqual([]);
        }
    });

    it('an un-minted node self-diffs to ONLY its deterministic-id add (never a mutate/destroy)', () => {
        // A node still awaiting its fid legitimately re-emits its `add` each settle until
        // the mint echoes back — but the id is deterministic (c-add-<localId>), so the
        // daemon's ledger folds the replay (FIX B). Crucially, self-diff must emit nothing
        // BUT adds: no phantom set_title/set_description/move/retire on a stable doc.
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            const units = featureUnits(specsToDoc(randomSpecs(rng, true)));
            const cmds = commandsForSettle(units, units, seed & 0xffff);
            for (const c of cmds) {
                expect(c.kind).toBe('add');
                expect(c.id).toBe(`c-add-${c.local_id}`);   // deterministic → ledger-folded
            }
        }
    });
});

describe('property: I1 — a vanished heading is NEVER a retire', () => {
    it('deleting an arbitrary subset of headings emits no retire command', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            // Baseline: live features (nothing flagged retired).
            const base = randomSpecs(rng).map(s => ({ ...s, retired: false, fid: s.fid ?? `f-${rng()}` }));
            const prev = featureUnits(specsToDoc(base));
            // Next: keep a random subset (simulate backspace-merge / select-all delete /
            // a mid-edit transient) — still nothing flagged retired.
            const next = featureUnits(specsToDoc(base.filter(() => rng() < 0.5)));
            const cmds = commandsForSettle(prev, next, 1);
            expect(cmds.filter(c => c.kind === 'retire')).toEqual([]);
        }
    });
});

describe('property: retire is emitted iff the retired flag transitions false→true', () => {
    it('retire count equals the number of fids flipped to retired', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            // Baseline: features with stable fids, none retired.
            const base = randomSpecs(rng).map((s, i) => ({ ...s, retired: false, fid: `f-${i}` }));
            if (base.length === 0) continue;
            const prev = featureUnits(specsToDoc(base));
            // Flip a random subset to retired (the explicit ~ retire gesture); nodes STAY.
            const flipped = new Set<string>();
            const next = featureUnits(specsToDoc(base.map(s => {
                const retire = rng() < 0.4;
                if (retire) flipped.add(s.fid);
                return { ...s, retired: retire };
            })));
            const retires = commandsForSettle(prev, next, 1).filter(c => c.kind === 'retire');
            expect(new Set(retires.map(c => c.feature_id))).toEqual(flipped);
            expect(retires).toHaveLength(flipped.size);
        }
    });
});

describe('property: I2 — prose attribution follows ownerId, not position', () => {
    it('every owned paragraph lands in its owner feature regardless of where it sits', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            // n headings with stable fids f-0..f-{n-1}. At least one so paras have an owner.
            const n = int(rng, 1, 5);
            const fids = Array.from({ length: n }, (_u, i) => `f-${i}`);
            const headings = fids.map(fid => featureHeadingNode(
                { fid, level: 0, retired: false, realized: true, localId: null }, textToInlineRuns(fid)));
            // m paragraphs, each with UNIQUE text and an ownerId pointing at a random fid,
            // placed at random positions among the headings — deliberately NOT under their
            // owner, so position and identity disagree.
            const m = int(rng, 0, 10);
            const paras = Array.from({ length: m }, (_u, i) => ({
                text: `p${seed & 0xff}-${i}`, owner: pick(rng, fids),
            }));
            // Interleave: start with all headings, then splice each paragraph at a random slot.
            const blocks: PMNode[] = [...headings];
            for (const p of paras) {
                const at = int(rng, 1, blocks.length);   // never before the first heading
                blocks.splice(at, 0, paragraphNode(textToInlineRuns(p.text), p.owner));
            }
            const units = featureUnits(makeDoc(blocks));
            // Expected: each feature's description = its owned paragraphs' text in doc order.
            const expected = new Map<string, string[]>(fids.map(f => [f, []]));
            for (const b of blocks) {
                if (b.type !== 'paragraph') continue;
                const owner = (b.attrs as { ownerId?: string }).ownerId!;
                const text = (b.content?.[0] as { text?: string })?.text ?? '';
                expected.get(owner)!.push(text);
            }
            for (const u of units) {
                expect(u.description).toBe(expected.get(u.fid!)!.join('\n\n'));
            }
        }
    });
});

describe('property: I-content-safe — text-only edits never restructure', () => {
    it('mutating titles/descriptions emits only set_title / set_description', () => {
        for (const seed of SEEDS) {
            const rng = mulberry32(seed);
            // Stable structure: same fids, same levels, none retired — only text changes.
            const base = randomSpecs(rng).map((s, i) => ({
                ...s, fid: `f-${i}`, localId: `lid-${i}`, retired: false,
            }));
            if (base.length === 0) continue;
            const prev = featureUnits(specsToDoc(base));
            const next = featureUnits(specsToDoc(base.map(s => ({
                ...s,
                title: rng() < 0.5 ? s.title + ' x' : s.title,
                paras: s.paras.map(p => (rng() < 0.5 ? p + ' y' : p)),
            }))));
            const kinds = new Set(commandsForSettle(prev, next, 1).map(c => c.kind));
            for (const k of kinds) expect(['set_title', 'set_description']).toContain(k);
        }
    });
});
