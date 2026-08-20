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
    const agreed = snap({
        held: new Set(['f-1']),
        agreedLayers: new Map([['f-1', 'hold:f-1']]),
        present: new Set(['f-1']),
    });

    it('does NOT fire when the reader ACCEPTS — accepting is not building', () => {
        // The whole point of the accepted stage. The proposal stops being offered and
        // becomes a queued directive; nothing has been written. Filling the ring here
        // told the reader their plan was done at the moment they agreed to it.
        expect(fulfilments(offered, agreed, none, NOW).size).toBe(0);
    });

    it('does NOT fire when the reader REJECTS — that is their decision, not a build', () => {
        // Every amend and retire leaves its node in the tree, so "the proposal is gone
        // and the node is still here" was true of a rejection too, and the ring filled
        // on one. Now a rejection goes nowhere: no agreed layer ever appears.
        expect(fulfilments(offered, snap({ present: new Set(['f-1']) }), none, NOW).size).toBe(0);
        expect(fulfilments(offered, snap(), none, NOW).size).toBe(0);
    });

    it('fires when the AGREED plan\'s directive closes — the code caught up', () => {
        const built = snap({ present: new Set(['f-1']) });
        expect(fulfilments(agreed, built, none, NOW).get('f-1')!.map(f => f.channel))
            .toEqual(['plan']);
    });

    it('credits the PLAN, not the reader, when an accepted plan leaves the hold set', () => {
        // Both transitions fire on the same payload — the feature leaves the hold set
        // AND the agreed layer goes — and only one of them is true. The words were the
        // agent's; saying "the code now says what YOU wrote" is the error the origin
        // distinction exists to prevent.
        const built = snap({ present: new Set(['f-1']) });
        expect(fulfilments(agreed, built, none, NOW).get('f-1')!.map(f => f.channel))
            .toEqual(['plan']);
    });

    it('does not fire while the plan is still agreed and unbuilt', () => {
        expect(fulfilments(agreed, agreed, none, NOW).size).toBe(0);
    });

    it('does not fire while the same layer is still on offer', () => {
        expect(fulfilments(offered, offered, none, NOW).size).toBe(0);
    });

    it('reports an accepted ADD once it is built, under the id the store minted', () => {
        // The gap that used to be pinned here as a deliberate silence. An add proposal is
        // filed under its own EVENT id and that id is gone the moment it resolves — so
        // watching the OFFER could never tell an accepted add from a rejected one. The
        // agreed set is keyed by the feature's real id, which only an ACCEPT produces, so
        // the question is answered by where the plan went instead of by what vanished.
        const addOffered = snap({ planLayers: new Map([['e-add', 'e-add']]), present: new Set(['e-add']) });
        const addAgreed = snap({
            held: new Set(['f-new']),
            agreedLayers: new Map([['f-new', 'hold:f-new']]),
            present: new Set(['f-new']),
        });
        expect(fulfilments(addOffered, addAgreed, none, NOW).size).toBe(0);   // accepted ≠ built
        expect(fulfilments(addAgreed, snap({ present: new Set(['f-new']) }), none, NOW)
            .get('f-new')!.map(f => f.channel)).toEqual(['plan']);
    });

    it('stays silent for a REJECTED add, which leaves with its node', () => {
        const addOffered = snap({ planLayers: new Map([['e-add', 'e-add']]), present: new Set(['e-add']) });
        expect(fulfilments(addOffered, snap(), none, NOW).size).toBe(0);
    });
});

describe('both slots can be lit at once', () => {
    it('the marker draws two filled rings when both channels have landed', () => {
        // Not from one payload: a feature's landing is credited to ONE channel per
        // transition, because crediting the reader with an agent's accepted words is
        // exactly the error `agreedLayers` exists to prevent. But a node can be built
        // twice over time — a plan, and later an edit of the reader's own — and the
        // marker has to hold both, because "this was planned and built" and "what you
        // wrote is now in the code" are two different things to be told.
        const both = mergeFulfilments(
            new Map([['f-1', [{ channel: 'plan' as const, at: NOW - 1000, diverged: 'none' as const }]]]),
            fulfilments(
                snap({ held: new Set(['f-1']) }),
                snap({ present: new Set(['f-1']) }),
                none, NOW,
            ),
            NOW, FULFILMENT_TTL_MS,
        );
        expect(both.get('f-1')!.map(f => f.channel).sort()).toEqual(['human', 'plan']);
        const s = nodeStatus([], both.get('f-1')!, NOW);
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

// (The "deliberately does not guess" block is gone with the gap it described. An
// accepted ADD used to produce no marker at all, because the only signal available was
// its proposal vanishing — which happens identically on accept and on reject. The
// agreed set is keyed by the id the STORE minted, which only an accept produces, so the
// two are now distinguishable and the accepted add gets its ring. Pinned above.)
