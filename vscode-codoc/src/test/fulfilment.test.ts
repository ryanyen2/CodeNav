import { describe, it, expect } from 'vitest';
import {
    fulfilments, mergeFulfilments, divergenceOf, emptySnapshot, type Snapshot,
} from '../state/fulfilment';
import { nodeStatus, statusGlyphs, FULFILMENT_TTL_MS, type Fulfilment } from '../state/node-status';
import type { Claim } from '../state/settlement';

const NOW = 1_700_000_000_000;

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({ ...emptySnapshot(), ...over });

const codeClaim = (edit: 'add' | 'del'): Claim => ({
    channel: 'code', stage: 'landed', edit, block: { kind: 'para', index: 0 },
    start: 0, end: 4, layerId: 'e-1',
});

const none = () => [];

describe('noticing that an edit of yours was built', () => {
    it('fires when the feature leaves the hold set', () => {
        const got = fulfilments(
            snap({ held: new Set(['f-1']) }),
            snap({ present: new Set(['f-1']) }),
            none, NOW,
        );
        expect(got.get('f-1')).toEqual([{ channel: 'human', at: NOW, diverged: 'none' }]);
    });

    it('does not fire while it is still held', () => {
        const held = snap({ held: new Set(['f-1']), present: new Set(['f-1']) });
        expect(fulfilments(held, held, none, NOW).size).toBe(0);
    });

    it('does not fire for a feature that left the document — that is not "built"', () => {
        const got = fulfilments(snap({ held: new Set(['f-1']) }), snap(), none, NOW);
        expect(got.size).toBe(0);
    });

    it('records HOW the build differed, from the code claims standing at that moment', () => {
        const got = fulfilments(
            snap({ held: new Set(['f-1']) }),
            snap({ present: new Set(['f-1']) }),
            () => [codeClaim('add'), codeClaim('del')], NOW,
        );
        expect(got.get('f-1')![0].diverged).toBe('both');
    });
});

describe('noticing that a plan was built', () => {
    const offered = snap({ planLayers: new Map([['f-1', 'e-9']]), present: new Set(['f-1']) });

    it('fires when the layer stops being offered but its node stands', () => {
        const got = fulfilments(offered, snap({ present: new Set(['f-1']) }), none, NOW);
        expect(got.get('f-1')![0].channel).toBe('plan');
    });

    it('does NOT fire when the node left with it — that is a rejection', () => {
        // Saying "built" about a proposal the reader declined is a lie about their own
        // decision, and the ring filling is the surface making it.
        expect(fulfilments(offered, snap(), none, NOW).size).toBe(0);
    });

    it('does not fire while the same layer is still on offer', () => {
        expect(fulfilments(offered, offered, none, NOW).size).toBe(0);
    });

    it('fires when a DIFFERENT layer replaces it — the first one resolved', () => {
        const next = snap({ planLayers: new Map([['f-1', 'e-10']]), present: new Set(['f-1']) });
        expect(fulfilments(offered, next, none, NOW).get('f-1')![0].channel).toBe('plan');
    });
});

describe('both slots can land at once', () => {
    it('records the plan AND the edit that rode with it', () => {
        const got = fulfilments(
            snap({ held: new Set(['f-1']), planLayers: new Map([['f-1', 'e-9']]) }),
            snap({ present: new Set(['f-1']) }),
            none, NOW,
        );
        expect(got.get('f-1')!.map(f => f.channel).sort()).toEqual(['human', 'plan']);
        // …and the marker draws both rings, filled.
        const s = nodeStatus([], got.get('f-1')!, NOW);
        expect(s.human).toBe('fulfilled');
        expect(s.plan).toBe('fulfilled');
        expect(statusGlyphs(s).map(g => g.slot)).toEqual(['human', 'plan']);
    });
});

describe('divergenceOf', () => {
    it('reads only the code channel — the other two are not the build', () => {
        const human: Claim = { ...codeClaim('add'), channel: 'human', stage: 'open' };
        expect(divergenceOf([human])).toBe('none');
        expect(divergenceOf([codeClaim('add')])).toBe('add');
        expect(divergenceOf([codeClaim('del')])).toBe('del');
    });
});

describe('mergeFulfilments — remember briefly, then let go', () => {
    const f = (channel: 'human' | 'plan', at: number): Fulfilment => ({ channel, at, diverged: 'none' });

    it('drops what has aged out', () => {
        const known = new Map([['f-1', [f('human', NOW - FULFILMENT_TTL_MS - 1)]]]);
        expect(mergeFulfilments(known, new Map(), NOW, FULFILMENT_TTL_MS).size).toBe(0);
    });

    it('keeps what is still owed a showing', () => {
        const known = new Map([['f-1', [f('human', NOW - 1000)]]]);
        expect(mergeFulfilments(known, new Map(), NOW, FULFILMENT_TTL_MS).get('f-1')).toHaveLength(1);
    });

    it('replaces a remembered landing on the same channel rather than stacking them', () => {
        const known = new Map([['f-1', [f('human', NOW - 1000)]]]);
        const arriving = new Map([['f-1', [f('human', NOW)]]]);
        const out = mergeFulfilments(known, arriving, NOW, FULFILMENT_TTL_MS).get('f-1')!;
        expect(out).toHaveLength(1);
        expect(out[0].at).toBe(NOW);
    });

    it('keeps a landing on the OTHER channel alongside it', () => {
        const known = new Map([['f-1', [f('plan', NOW - 1000)]]]);
        const arriving = new Map([['f-1', [f('human', NOW)]]]);
        const out = mergeFulfilments(known, arriving, NOW, FULFILMENT_TTL_MS).get('f-1')!;
        expect(out.map(x => x.channel).sort()).toEqual(['human', 'plan']);
    });
});

describe('what it deliberately does not guess', () => {
    it('emits no marker for a resolved ADD, rather than a wrong one', () => {
        // An add proposal is filed under its own EVENT id — it has no feature yet — so
        // once it resolves that id is gone from the document either way: accepted, it
        // returns as a real node under a freshly minted fid; rejected, it is simply gone.
        // Silence is the honest answer until the ledger's `caused_by` reaches the webview.
        const offered = snap({ planLayers: new Map([['e-add', 'e-add']]), present: new Set(['e-add']) });
        const accepted = snap({ present: new Set(['f-new']) });   // came back under a new id
        const rejected = snap({ present: new Set() });
        expect(fulfilments(offered, accepted, none, NOW).size).toBe(0);
        expect(fulfilments(offered, rejected, none, NOW).size).toBe(0);
    });
});
