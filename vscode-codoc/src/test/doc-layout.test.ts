import { describe, it, expect } from 'vitest';
import { layoutDoc, groupBindings } from '../state/doc-layout';
import { ParsedFeature, extractRefs } from '../state/tree-model';
import { SidecarData, emptySidecar } from '../state/bindings-model';

function feat(p: Partial<ParsedFeature> & { id: string; title: string }): ParsedFeature {
    const description = p.description ?? '';
    return {
        id: p.id,
        title: p.title,
        description,
        parent_id: p.parent_id ?? null,
        retired: p.retired ?? false,
        line: p.line ?? 0,
        refs: p.refs ?? extractRefs(description),
    };
}

function sidecar(over: Partial<SidecarData> = {}): SidecarData {
    return { ...emptySidecar(), ...over };
}

describe('layoutDoc — ordering', () => {
    it("'tree' order preserves file order of siblings", () => {
        const feats = [
            feat({ id: 'f-a', title: 'A' }),
            feat({ id: 'f-b', title: 'B' }),
            feat({ id: 'f-c', title: 'C' }),
        ];
        const secs = layoutDoc(feats, sidecar(), { siblingOrder: 'tree' });
        expect(secs.map(s => s.id)).toEqual(['f-a', 'f-b', 'f-c']);
    });

    it("'dependency' order places prerequisites before dependants", () => {
        // A depends on C (A → C). C is the prerequisite, so C must come before A.
        const feats = [
            feat({ id: 'f-a', title: 'A' }),
            feat({ id: 'f-b', title: 'B' }),
            feat({ id: 'f-c', title: 'C' }),
        ];
        const sc = sidecar({ feature_edges: { 'f-a': [{ to: 'f-c', weight: 3, kinds: ['call'] }] } });
        const secs = layoutDoc(feats, sc, { siblingOrder: 'dependency' });
        const ids = secs.map(s => s.id);
        expect(ids.indexOf('f-c')).toBeLessThan(ids.indexOf('f-a'));
        // B has no edges → keeps relative file order among the un-constrained.
        expect(ids).toContain('f-b');
    });

    it('falls back to file order on a dependency cycle', () => {
        const feats = [feat({ id: 'f-a', title: 'A' }), feat({ id: 'f-b', title: 'B' })];
        const sc = sidecar({
            feature_edges: {
                'f-a': [{ to: 'f-b', weight: 1, kinds: ['call'] }],
                'f-b': [{ to: 'f-a', weight: 1, kinds: ['call'] }],
            },
        });
        const secs = layoutDoc(feats, sc, { siblingOrder: 'dependency' });
        expect(secs.map(s => s.id)).toEqual(['f-a', 'f-b']);
    });

    it('only reorders within a sibling group, never across parents', () => {
        const feats = [
            feat({ id: 'f-root', title: 'Root' }),
            feat({ id: 'f-x', title: 'X', parent_id: 'f-root' }),
            feat({ id: 'f-y', title: 'Y', parent_id: 'f-root' }),
        ];
        const sc = sidecar({ feature_edges: { 'f-x': [{ to: 'f-y', weight: 2, kinds: ['import'] }] } });
        const secs = layoutDoc(feats, sc, { siblingOrder: 'dependency' });
        // Root stays first; Y (prereq) before X within the children.
        expect(secs[0].id).toBe('f-root');
        const ids = secs.map(s => s.id);
        expect(ids.indexOf('f-y')).toBeLessThan(ids.indexOf('f-x'));
        expect(secs.find(s => s.id === 'f-x')!.level).toBe(1);
    });
});

describe('layoutDoc — ghosts', () => {
    it('inserts ADD ghost at its destination parent, trailing live siblings', () => {
        const feats = [
            feat({ id: 'f-root', title: 'Root' }),
            feat({ id: 'f-live', title: 'Live', parent_id: 'f-root' }),
        ];
        const sc = sidecar({
            proposals: {
                by_feature: {},
                by_event: { 'e-1': { op: 'add', tag: 'agent reflection', parent_id: 'f-root', title: 'New thing' } },
            },
        });
        const secs = layoutDoc(feats, sc, { siblingOrder: 'dependency' });
        const ids = secs.map(s => s.id);
        expect(ids).toContain('e-1');
        const ghost = secs.find(s => s.id === 'e-1')!;
        expect(ghost.flags.isGhost).toBe(true);
        expect(ghost.flags.proposalOp).toBe('add');
        expect(ghost.parentId).toBe('f-root');
        // ghost trails the live sibling
        expect(ids.indexOf('f-live')).toBeLessThan(ids.indexOf('e-1'));
    });
});

describe('layoutDoc — citations', () => {
    it('splits prose into text + cite runs at authored ref positions', () => {
        const f = feat({
            id: 'f-a',
            title: 'A',
            description: 'See [helper](codoc:util.py#helper) for details.',
        });
        const [sec] = layoutDoc([f], sidecar(), { siblingOrder: 'tree' });
        expect(sec.blocks).toHaveLength(1);
        const runs = sec.blocks[0];
        expect(runs[0]).toEqual({ t: 'text', s: 'See ' });
        expect(runs[1]).toEqual({ t: 'cite', label: 'helper', file: 'util.py', symbol: 'helper' });
        expect(runs[2]).toEqual({ t: 'text', s: ' for details.' });
    });

    it('splits description into paragraphs on blank lines', () => {
        const f = feat({ id: 'f-a', title: 'A', description: 'First para.\n\nSecond para.' });
        const [sec] = layoutDoc([f], sidecar(), { siblingOrder: 'tree' });
        expect(sec.blocks).toHaveLength(2);
    });

    it('binding rail drops bindings already cited inline (file + symbol)', () => {
        const f = feat({ id: 'f-a', title: 'A', description: 'uses [h](codoc:util.py#helper)' });
        const sc = sidecar({
            by_feature: {
                'f-a': [
                    { file: 'util.py', symbol: 'util.py::helper' },  // cited inline → suppressed
                    { file: 'util.py', symbol: 'util.py::other' },   // kept in rail
                ],
            },
        });
        const [sec] = layoutDoc([f], sc, { siblingOrder: 'tree' });
        expect(sec.bindings.map(b => b.symbol)).toEqual(['util.py::other']);
    });

    it('cross-refs are sorted by weight desc, deduped by target, self/missing dropped', () => {
        const feats = [
            feat({ id: 'f-a', title: 'A' }),
            feat({ id: 'f-b', title: 'B' }),
            feat({ id: 'f-c', title: 'C' }),
        ];
        const sc = sidecar({
            feature_edges: {
                'f-a': [
                    { to: 'f-b', weight: 1, kinds: ['call'] },
                    { to: 'f-c', weight: 5, kinds: ['import'] },
                    { to: 'f-ghost', weight: 9, kinds: ['call'] }, // missing target → dropped
                    { to: 'f-a', weight: 2, kinds: ['call'] },     // self → dropped
                ],
            },
        });
        const [secA] = layoutDoc(feats, sc, { siblingOrder: 'tree' });
        expect(secA.crossRefs.map(x => x.toId)).toEqual(['f-c', 'f-b']);
        expect(secA.crossRefs[0]).toMatchObject({ toId: 'f-c', rel: 'depends', weight: 5 });
    });

    it('drops cross-refs to retired features', () => {
        const feats = [
            feat({ id: 'f-a', title: 'A' }),
            feat({ id: 'f-dead', title: 'Dead', retired: true }),
        ];
        const sc = sidecar({ feature_edges: { 'f-a': [{ to: 'f-dead', weight: 4, kinds: ['call'] }] } });
        const [secA] = layoutDoc(feats, sc, { siblingOrder: 'tree' });
        expect(secA.crossRefs).toHaveLength(0);
    });
});

describe('groupBindings', () => {
    it('groups by file and clusters a class with its methods, file stripped', () => {
        const groups = groupBindings([
            { file: 'execution.py', symbol: 'execution.py::WriteOnlyStringIO::read' },
            { file: 'execution.py', symbol: 'execution.py::WriteOnlyStringIO' },
            { file: 'execution.py', symbol: 'execution.py::capture_io' },
        ]);
        expect(groups).toHaveLength(1);
        const g = groups[0];
        expect(g.file).toBe('execution.py');
        // sorted by symbol_path → class before its method; capture_io after.
        expect(g.items.map(i => i.label)).toEqual(['WriteOnlyStringIO', 'WriteOnlyStringIO.read', 'capture_io']);
        expect(g.items.map(i => i.depth)).toEqual([0, 1, 0]);
    });

    it('renders __module__ as ‹module›', () => {
        const [g] = groupBindings([{ file: 'execution.py', symbol: 'execution.py::__module__' }]);
        expect(g.items[0].label).toBe('‹module›');
        expect(g.items[0].depth).toBe(0);
    });

    it('orders the largest file group first', () => {
        const groups = groupBindings([
            { file: 'a.py', symbol: 'a.py::one' },
            { file: 'b.py', symbol: 'b.py::x' },
            { file: 'b.py', symbol: 'b.py::y' },
        ]);
        expect(groups.map(g => g.file)).toEqual(['b.py', 'a.py']);
    });
});

describe('layoutDoc — flags & contentHash', () => {
    it('overlays activeMode/phase without changing contentHash', () => {
        const f = feat({ id: 'f-a', title: 'A', description: 'body' });
        const base = layoutDoc([f], sidecar(), { siblingOrder: 'tree' })[0];
        const active = layoutDoc([f], sidecar(), {
            siblingOrder: 'tree',
            activeModes: new Map([['f-a', 'write']]),
            phases: new Map([['f-a', 'editing']]),
        })[0];
        expect(active.flags.activeMode).toBe('write');
        expect(active.flags.phase).toBe('editing');
        expect(active.contentHash).toBe(base.contentHash);
    });

    it('contentHash changes when description changes', () => {
        const a = layoutDoc([feat({ id: 'f-a', title: 'A', description: 'one' })], sidecar(), { siblingOrder: 'tree' })[0];
        const b = layoutDoc([feat({ id: 'f-a', title: 'A', description: 'two' })], sidecar(), { siblingOrder: 'tree' })[0];
        expect(a.contentHash).not.toBe(b.contentHash);
    });

    it('marks realized=false from the sidecar', () => {
        const sc = sidecar({ features: { 'f-a': { title: 'A', parent_id: null, realized: false } } });
        const [sec] = layoutDoc([feat({ id: 'f-a', title: 'A' })], sc, { siblingOrder: 'tree' });
        expect(sec.flags.realized).toBe(false);
    });

    it('carries a RETIRE/AMEND proposal onto the live section', () => {
        const sc = sidecar({
            proposals: {
                by_feature: { 'f-a': { op: 'amend', event_id: 'e-9', tag: 'code drift', title: 'A2', description: 'new' } },
                by_event: {},
            },
        });
        const [sec] = layoutDoc([feat({ id: 'f-a', title: 'A' })], sc, { siblingOrder: 'tree' });
        expect(sec.proposal).toMatchObject({ op: 'amend', eventId: 'e-9', title: 'A2' });
        expect(sec.flags.proposalOp).toBe('amend');
    });
});
