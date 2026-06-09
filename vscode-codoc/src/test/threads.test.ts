/**
 * threads.test.ts — guards U4/H6: the unified dependency-threads merge (dedup within
 * a strand, self-edge + title-less drop, empty → omitted).
 */
import { describe, it, expect } from 'vitest';
import { assembleThreads } from '../state/threads';

const titles: Record<string, string> = { 'f-a': 'Alpha', 'f-b': 'Beta', 'f-c': 'Gamma', 'f-self': 'Self' };
const titleOf = (fid: string): string => titles[fid] ?? '';

describe('U4 — assembleThreads', () => {
    it('merges 2 reads + 1 used-by + 3 refs into the three strands', () => {
        const t = assembleThreads({
            out: [{ to: 'f-a' }, { to: 'f-b' }],
            in: [{ to: 'f-c' }],
            bindings: [{ file: 'x.py', symbol: 'x::a' }, { file: 'x.py', symbol: 'x::b' }, { file: 'y.py', symbol: 'y::c' }],
            titleOf, selfId: 'f-self',
        });
        expect(t).not.toBeNull();
        expect(t!.reads.map(r => r.toTitle)).toEqual(['Alpha', 'Beta']);
        expect(t!.usedBy.map(r => r.toTitle)).toEqual(['Gamma']);
        expect(t!.refs).toHaveLength(3);
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

    it('returns null when all three strands are empty (line omitted)', () => {
        expect(assembleThreads({ out: [], in: [], bindings: [], titleOf, selfId: 'f-self' })).toBeNull();
    });
});
