/**
 * threads.test.ts — guards the unified Connections merge (U4 → U5): dedup within a
 * strand, self-edge + title-less drop, empty → omitted, PLUS the U5 deltas — the
 * Consult strand, weight ranking (title tie-break; code rows by file/symbol), and
 * the per-strand collapse flag.
 */
import { describe, it, expect } from 'vitest';
import { assembleThreads } from '../state/threads';
import { THREADS_COLLAPSE_AT } from '../webview/protocol';

const titles: Record<string, string> = {
    'f-a': 'Alpha', 'f-b': 'Beta', 'f-c': 'Gamma', 'f-self': 'Self',
};
const titleOf = (fid: string): string => titles[fid] ?? '';

describe('U4 — assembleThreads (merge / dedup / empty)', () => {
    it('merges reads + used-by + refs into the strands (now ranked + with consult/collapsed)', () => {
        const t = assembleThreads({
            out: [{ to: 'f-a' }, { to: 'f-b' }],
            in: [{ to: 'f-c' }],
            bindings: [{ file: 'x.py', symbol: 'x::a' }, { file: 'x.py', symbol: 'x::b' }, { file: 'y.py', symbol: 'y::c' }],
            titleOf, selfId: 'f-self',
        });
        expect(t).not.toBeNull();
        // equal weight (all 0) → ranked by title: Alpha before Beta
        expect(t!.reads.map(r => r.toTitle)).toEqual(['Alpha', 'Beta']);
        expect(t!.usedBy.map(r => r.toTitle)).toEqual(['Gamma']);
        expect(t!.refs).toHaveLength(3);
        expect(t!.consult).toEqual([]);
        expect(t!.collapsed).toEqual({ reads: false, usedBy: false, refs: false, consult: false });
    });

    it('dedups within a strand but allows a mutual dependency in both strands', () => {
        const t = assembleThreads({
            out: [{ to: 'f-a' }, { to: 'f-a' }],   // duplicate read
            in: [{ to: 'f-a' }],                    // same feature also uses this one (mutual)
            bindings: [], titleOf, selfId: 'f-self',
        });
        expect(t!.reads.map(r => r.toId)).toEqual(['f-a']);      // deduped within reads
        expect(t!.usedBy.map(r => r.toId)).toEqual(['f-a']);     // still present in usedBy
    });

    it('drops self-edges and title-less edges', () => {
        const t = assembleThreads({
            out: [{ to: 'f-self' }, { to: 'f-unknown' }, { to: 'f-a' }],
            in: [], bindings: [], titleOf, selfId: 'f-self',
        });
        expect(t!.reads.map(r => r.toId)).toEqual(['f-a']); // self + unknown(no title) dropped
    });

    it('returns null when ALL strands (incl. consult) are empty (panel omitted)', () => {
        expect(assembleThreads({ out: [], in: [], bindings: [], links: [], titleOf, selfId: 'f-self' })).toBeNull();
    });
});

describe('U5 — Consult strand', () => {
    it('surfaces external https:// links and never codoc: refs (caller-supplied, parse-free)', () => {
        // The assembler is parse-free; the caller (buildPayload via extractLinks) hands it
        // ONLY external links — codoc: refs are excluded upstream by the https? scheme guard.
        const t = assembleThreads({
            out: [], in: [], bindings: [],
            links: [
                { label: 'RFC 7231', url: 'https://www.rfc-editor.org/rfc/rfc7231' },
                { label: 'PEP 8', url: 'http://peps.python.org/pep-0008/' },
            ],
            titleOf, selfId: 'f-self',
        });
        expect(t).not.toBeNull();
        expect(t!.consult.map(c => c.url)).toEqual([
            'http://peps.python.org/pep-0008/',       // ranked by label: "PEP 8" < "RFC 7231"
            'https://www.rfc-editor.org/rfc/rfc7231',
        ]);
        // a consult-only feature is non-null (a panel renders just the Consult strand)
        expect(t!.reads).toEqual([]);
        expect(t!.usedBy).toEqual([]);
        expect(t!.refs).toEqual([]);
    });

    it('dedups consult links by url and falls back to the url when label is blank', () => {
        const t = assembleThreads({
            out: [], in: [], bindings: [],
            links: [
                { label: '', url: 'https://example.com/a' },
                { label: 'dup', url: 'https://example.com/a' }, // same url → dropped
            ],
            titleOf, selfId: 'f-self',
        });
        expect(t!.consult).toHaveLength(1);
        expect(t!.consult[0]).toEqual({ label: 'https://example.com/a', url: 'https://example.com/a' });
    });
});

describe('U5 — weight ranking', () => {
    it('ranks reads by coupling weight (heaviest first), tie-break by title', () => {
        const t = assembleThreads({
            out: [
                { to: 'f-a', weight: 1 },   // Alpha, light
                { to: 'f-b', weight: 5 },   // Beta, heavy
                { to: 'f-c', weight: 5 },   // Gamma, heavy — tie with Beta → title decides
            ],
            in: [], bindings: [], titleOf, selfId: 'f-self',
        });
        // weight desc → {Beta,Gamma}@5 before Alpha@1; tie at 5 → Beta before Gamma by title
        expect(t!.reads.map(r => r.toTitle)).toEqual(['Beta', 'Gamma', 'Alpha']);
        expect(t!.reads.map(r => r.weight)).toEqual([5, 5, 1]);
    });

    it('ranks bound code by file then symbol_path', () => {
        const t = assembleThreads({
            out: [], in: [],
            bindings: [
                { file: 'b.py', symbol: 'b::z' },
                { file: 'a.py', symbol: 'a::y' },
                { file: 'a.py', symbol: 'a::x' },
            ],
            titleOf, selfId: 'f-self',
        });
        expect(t!.refs).toEqual([
            { file: 'a.py', symbol: 'a::x' },
            { file: 'a.py', symbol: 'a::y' },
            { file: 'b.py', symbol: 'b::z' },
        ]);
    });

    it('carries the edge kinds through for shape = kind rendering', () => {
        const t = assembleThreads({
            out: [{ to: 'f-a', weight: 2, kinds: ['call'] }],
            in: [], bindings: [], titleOf, selfId: 'f-self',
        });
        expect(t!.reads[0].kinds).toEqual(['call']);
    });
});

describe('U5 — collapse flag', () => {
    it('reports collapsed:true for a strand exceeding THREADS_COLLAPSE_AT rows', () => {
        const many = Array.from({ length: THREADS_COLLAPSE_AT + 1 }, (_v, i) => `f-${i}`);
        const t = assembleThreads({
            out: many.map(to => ({ to })),
            in: [], bindings: [],
            titleOf: fid => fid,   // every id has a title so none are dropped
            selfId: 'f-self',
        });
        expect(t!.reads.length).toBe(THREADS_COLLAPSE_AT + 1);
        expect(t!.collapsed.reads).toBe(true);
        expect(t!.collapsed.usedBy).toBe(false);
    });

    it('reports collapsed:false at exactly THREADS_COLLAPSE_AT rows (boundary)', () => {
        const exact = Array.from({ length: THREADS_COLLAPSE_AT }, (_v, i) => `f-${i}`);
        const t = assembleThreads({
            out: exact.map(to => ({ to })),
            in: [], bindings: [],
            titleOf: fid => fid, selfId: 'f-self',
        });
        expect(t!.collapsed.reads).toBe(false);
    });
});

describe('U5 — used-by inverse (regression)', () => {
    it('A depends on B ⇒ B.usedBy includes A (the inverse strand is preserved)', () => {
        // assembleThreads is per-feature; the inversion happens in directedEdges (host).
        // Here we model B's inputs: B's in-edge is the dependant A.
        const bThreads = assembleThreads({
            out: [],                         // B depends on nothing
            in: [{ to: 'f-a', weight: 3 }],  // A depends on B → A is in B.usedBy
            bindings: [], titleOf, selfId: 'f-b',
        });
        expect(bThreads!.usedBy.map(u => u.toId)).toEqual(['f-a']);
        expect(bThreads!.reads).toEqual([]);
    });
});
