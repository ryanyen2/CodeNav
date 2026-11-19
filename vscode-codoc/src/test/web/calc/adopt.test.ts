import { describe, expect, it } from 'vitest';

import { adoptionPlan, blockNode } from '../../../web/calc/adopt';
import type { Projection, ProjectedBlock, Span } from '../../../web/calc/projection';
import { codocSchema } from '../../../web/editor/schema';

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, runs: Array<[string, number, number, string?, string?]>,
               spans: Span[] = [], level = 1, head = 1): ProjectedBlock {
    return {
        id, kind, level, rank: id, head,
        fields: { text: { text, runs: runs.map(([op, s, e, actor, plan]) => ({ op, k0: 0, start: s, end: e, actor: actor ?? 'human:a', at: '1', plan: plan ?? null })),
                          spans } },
    };
}

function proj(blocks: ProjectedBlock[]): Projection {
    return { head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {} }, claims: {} };
}

describe('blockNode', () => {
    it('turns spans into marks, and leaves plan runs to the paints', () => {
        const b = block('p', 'paragraph', 'keep this maybe', [['s:1', 0, 10], ['a:1', 10, 15, 'agent:x', 'pl']],
            [{ kind: 'bold', attrs: {}, start: 0, end: 4 }]);
        const { node, shadow } = blockNode(schema, b);
        const marksAt = (i: number) => node.resolve(i + 1).marks().map(m => m.type.name).sort();
        expect(node.textContent).toBe('keep this maybe');
        expect(marksAt(0)).toEqual(['bold']);
        expect(marksAt(12)).toEqual([]);
        expect(shadow!.ref(11)).toEqual(['a:1', 1]);
    });
});

describe('adoptionPlan', () => {
    it('replaces only blocks whose head moved and are not held', () => {
        const p = proj([block('h', 'heading', 'T', [['s:1', 0, 1]], [], 1, 5), block('p', 'paragraph', 'x', [['s:2', 0, 1]], [], 0, 7),
                        block('q', 'paragraph', 'y', [['s:3', 0, 1]], [], 0, 8)]);
        const steps = adoptionPlan([{ bid: 'h', head: 5 }, { bid: 'p', head: 6 }, { bid: 'z', head: 1 }], p, new Set(['p']));
        expect(steps).toEqual([
            { kind: 'remove', bid: 'z', index: 2 },
            { kind: 'insert', bid: 'q', index: 2 },
        ]);
        const steps2 = adoptionPlan([{ bid: 'h', head: 5 }, { bid: 'p', head: 6 }], p, new Set());
        expect(steps2).toEqual([{ kind: 'replace', bid: 'p', index: 1 }, { kind: 'insert', bid: 'q', index: 2 }]);
    });
});
