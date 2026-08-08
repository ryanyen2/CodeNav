/**
 * reorder-commands.test.ts — a drag emits ONE move, naming where it landed.
 *
 * Reorder used to emit nothing at all: the only move trigger was a changed
 * parent, so dragging a node among its siblings produced a command whose
 * parent_id had not changed, and the next projection put it back. These pin the
 * detection, and pin the property that makes it safe under concurrency — a
 * sibling arriving from the agent must not read as everybody moving.
 */
import { describe, it, expect } from 'vitest';
import { reorderTargets, moveCommand, type FeatureUnit } from '../state/commands-from-doc';

const u = (fid: string, parentId: string | null = null): FeatureUnit => ({
    fid, localId: null, title: fid, description: '', parentId, retired: false,
});

const seq = (...fids: string[]) => fids.map(f => u(f));

describe('detecting a reorder', () => {
    it('finds nothing when the order is unchanged', () => {
        expect(reorderTargets(seq('a', 'b', 'c'), seq('a', 'b', 'c')).size).toBe(0);
    });

    it('reports exactly ONE node for a single drag', () => {
        // Every write stamps feature_writers, so a reorder that touched all N
        // siblings would mark them all as freshly written and the author's next
        // edit to any of them would read as a conflict with a stranger.
        expect([...reorderTargets(seq('a', 'b', 'c'), seq('c', 'a', 'b')).keys()]).toEqual(['c']);
        expect([...reorderTargets(seq('a', 'b', 'c', 'd'), seq('a', 'c', 'b', 'd')).keys()])
            .toEqual(['c']);
    });

    it('bounds a drag on both sides when both neighbours stayed put', () => {
        const moved = reorderTargets(seq('a', 'b', 'c', 'd'), seq('a', 'c', 'b', 'd'));
        expect(moved.get('c')).toEqual({ afterId: 'a', beforeId: 'b' });
    });

    it('names the sibling a node was dropped after', () => {
        const moved = reorderTargets(seq('a', 'b', 'c'), seq('a', 'c', 'b'));
        expect(moved.get('c')?.afterId).toBe('a');
    });

    it('names the successor when a node lands first', () => {
        expect(reorderTargets(seq('a', 'b'), seq('b', 'a')).get('b'))
            .toEqual({ afterId: '', beforeId: 'a' });
    });

    it('never anchors ahead to a node that is itself still moving', () => {
        // A following mover has not been placed yet, so naming it would anchor
        // against a position that is about to change.
        const moved = reorderTargets(seq('a', 'b', 'c', 'd'), seq('c', 'd', 'a', 'b'));
        for (const [, { beforeId }] of moved) {
            if (beforeId) expect(moved.has(beforeId)).toBe(false);
        }
    });

    it('keeps siblings of different parents independent', () => {
        const before = [u('p'), u('a', 'p'), u('b', 'p'), u('q'), u('x', 'q'), u('y', 'q')];
        const after = [u('p'), u('b', 'p'), u('a', 'p'), u('q'), u('x', 'q'), u('y', 'q')];
        const moved = reorderTargets(before, after);
        expect([...moved.keys()]).toEqual(['b']);
        expect(moved.has('x')).toBe(false);
        expect(moved.has('y')).toBe(false);
    });
});

describe('what must NOT read as a reorder', () => {
    it('an agent inserting a sibling moves nobody', () => {
        // The load-bearing negative. Restricted to the ids both sides know, every
        // surviving predecessor is unchanged — so a projection that adds a feature
        // between two others does not make the editor claim the user dragged them.
        const moved = reorderTargets(seq('a', 'c'), seq('a', 'b', 'c'));
        expect(moved.size).toBe(0);
    });

    it('a retired sibling disappearing moves nobody', () => {
        const moved = reorderTargets(seq('a', 'b', 'c'), seq('a', 'c'));
        expect(moved.size).toBe(0);
    });

    it('a brand-new node with no fid is never a move target', () => {
        const fresh: FeatureUnit = {
            fid: null, localId: 'l-1', title: 'new', description: '', parentId: null, retired: false,
        };
        const moved = reorderTargets(seq('a'), [u('a'), fresh]);
        expect(moved.size).toBe(0);
    });

    it('editing prose moves nobody', () => {
        const before = seq('a', 'b');
        const after = [{ ...u('a'), description: 'rewritten' }, u('b')];
        expect(reorderTargets(before, after).size).toBe(0);
    });
});

describe('anchors are usable in emission order', () => {
    it('every anchor is a node that already exists in the store', () => {
        // An anchor must be a fid the daemon can resolve. A freshly typed heading
        // has only a localId, so anchoring to it would name nothing.
        const fresh: FeatureUnit = {
            fid: null, localId: 'l-1', title: 'new', description: '', parentId: null, retired: false,
        };
        const moved = reorderTargets(seq('a', 'b', 'c'), [u('c'), fresh, u('a'), u('b')]);
        const known = new Set(['a', 'b', 'c']);
        for (const { afterId, beforeId } of moved.values()) {
            if (afterId) expect(known.has(afterId)).toBe(true);
            if (beforeId) expect(known.has(beforeId)).toBe(true);
        }
    });

    it('a node never anchors to itself', () => {
        const moved = reorderTargets(seq('a', 'b', 'c', 'd'), seq('d', 'c', 'b', 'a'));
        for (const [fid, { afterId, beforeId }] of moved) {
            expect(afterId).not.toBe(fid);
            expect(beforeId).not.toBe(fid);
        }
    });

    it('anchors reconstruct the settled order when applied in sequence', () => {
        // The convergence argument, executed: replay the emitted moves against a
        // model list, in document order, and the result must be what the user saw.
        const before = seq('a', 'b', 'c', 'd', 'e');
        const target = ['d', 'b', 'e', 'a', 'c'];
        const moved = reorderTargets(before, seq(...target));

        let list = ['a', 'b', 'c', 'd', 'e'];
        for (const fid of target) {                       // emission is document order
            const spot = moved.get(fid);
            if (!spot) continue;
            list = list.filter(x => x !== fid);
            if (spot.afterId) list.splice(list.indexOf(spot.afterId) + 1, 0, fid);
            else if (spot.beforeId) list.splice(list.indexOf(spot.beforeId), 0, fid);
            else list.push(fid);
        }
        expect(list).toEqual(target);
    });
});

describe('the move command itself', () => {
    it('carries the neighbours it was given', () => {
        const cmd = moveCommand('f-1', 'p', 'tok', 'f-0', 'f-2');
        expect(cmd.payload).toEqual({ parent_id: 'p', after_id: 'f-0', before_id: 'f-2' });
    });

    it('omits neighbours entirely when there is no opinion about order', () => {
        // Absent means "append", which is what every caller did before ordering
        // existed — sending empty strings instead would claim a position.
        expect(moveCommand('f-1', 'p', 'tok').payload).toEqual({ parent_id: 'p' });
    });
});

describe('convergence, fuzzed', () => {
    /** Replay the emitted moves the way the daemon does: in document order, each
     *  placed relative to the anchors it named. */
    function replay(start: string[], target: string[],
                    moved: Map<string, { afterId: string; beforeId: string }>): string[] {
        let list = [...start];
        for (const fid of target) {
            const spot = moved.get(fid);
            if (!spot) continue;
            list = list.filter(x => x !== fid);
            if (spot.afterId) list.splice(list.indexOf(spot.afterId) + 1, 0, fid);
            else if (spot.beforeId) list.splice(list.indexOf(spot.beforeId), 0, fid);
            else list.push(fid);
        }
        return list;
    }

    function mulberry32(seed: number): () => number {
        let a = seed >>> 0;
        return () => {
            a = (a + 0x6d2b79f5) >>> 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    it('any permutation replays to exactly the order the user saw', () => {
        // The load-bearing property. The emitted commands are not merely plausible
        // — applied in order against the real list they must reproduce the settled
        // document, or the tree silently disagrees with the screen.
        let maxMoves = 0;
        for (let seed = 0; seed < 500; seed++) {
            const rng = mulberry32(seed);
            const n = 2 + Math.floor(rng() * 9);
            const start = Array.from({ length: n }, (_, i) => `f-${i}`);
            const target = [...start];
            for (let i = target.length - 1; i > 0; i--) {          // Fisher-Yates
                const j = Math.floor(rng() * (i + 1));
                [target[i], target[j]] = [target[j], target[i]];
            }
            const moved = reorderTargets(seq(...start), seq(...target));
            expect(replay(start, target, moved)).toEqual(target);
            maxMoves = Math.max(maxMoves, moved.size);
        }
        // Anti-vacuity: the corpus must actually contain real reorders, or the
        // assertion above is being proved against a stream of identity shuffles.
        expect(maxMoves).toBeGreaterThan(2);
    });

    it('moving one element of a long list emits one command, whatever the list', () => {
        for (let seed = 0; seed < 200; seed++) {
            const rng = mulberry32(seed);
            const n = 4 + Math.floor(rng() * 8);
            const start = Array.from({ length: n }, (_, i) => `f-${i}`);
            const from = Math.floor(rng() * n);
            let to = Math.floor(rng() * n);
            if (to === from) to = (to + 1) % n;
            const target = [...start];
            target.splice(to, 0, ...target.splice(from, 1));

            const moved = reorderTargets(seq(...start), seq(...target));
            expect(moved.size).toBe(1);
            expect(replay(start, target, moved)).toEqual(target);
        }
    });
});
