import { describe, it, expect } from 'vitest';
import { directedEdges, SidecarData, emptySidecar } from '../state/bindings-model';

function sidecar(edges: SidecarData['feature_edges']): SidecarData {
    return { ...emptySidecar(), feature_edges: edges };
}

describe('directedEdges', () => {
    it('out = depends-on, in = used-by, kinds carried through', () => {
        const dir = directedEdges(sidecar({ 'f-a': [{ to: 'f-b', weight: 3, kinds: ['call', 'import'] }] }));
        expect(dir.out.get('f-a')).toEqual([{ to: 'f-b', weight: 3, kinds: ['call', 'import'] }]);
        expect(dir.in.get('f-b')).toEqual([{ to: 'f-a', weight: 3, kinds: ['call', 'import'] }]);
        expect(dir.out.get('f-b')).toBeUndefined();
    });

    it('drops self-loops in both directions', () => {
        const dir = directedEdges(sidecar({ 'f-a': [{ to: 'f-a', weight: 1, kinds: ['call'] }] }));
        expect(dir.out.get('f-a')).toBeUndefined();
        expect(dir.in.get('f-a')).toBeUndefined();
    });

    it('accumulates multiple dependants on one target', () => {
        const dir = directedEdges(sidecar({
            'f-a': [{ to: 'f-c', weight: 1, kinds: ['call'] }],
            'f-b': [{ to: 'f-c', weight: 2, kinds: ['import'] }],
        }));
        expect(dir.in.get('f-c')!.map(e => e.to).sort()).toEqual(['f-a', 'f-b']);
    });

    it('empty when there are no edges', () => {
        const dir = directedEdges(emptySidecar());
        expect(dir.out.size).toBe(0);
        expect(dir.in.size).toBe(0);
    });
});
