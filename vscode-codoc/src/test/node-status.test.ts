import { describe, it, expect } from 'vitest';
import {
    nodeStatus, statusGlyphs, statusTitle, isSettled, expire, SETTLED,
    FULFILMENT_TTL_MS, type Fulfilment,
} from '../state/node-status';
import type { Claim } from '../state/settlement';

const NOW = 1_700_000_000_000;

const claim = (channel: Claim['channel'], stage: Claim['stage'], edit: Claim['edit'] = 'add'): Claim => ({
    channel, stage, edit, block: { kind: 'para', index: 0 }, start: 0, end: 4, layerId: 'l',
});

const slots = (s: ReturnType<typeof nodeStatus>) => statusGlyphs(s).map(g => g.slot);

describe('nodeStatus — one slot per question', () => {
    it('draws nothing for a settled feature', () => {
        expect(nodeStatus([], null, NOW)).toEqual(SETTLED);
        expect(statusGlyphs(SETTLED)).toEqual([]);
        expect(isSettled(SETTLED)).toBe(true);
    });

    it('separates your edit waiting from your edit sent', () => {
        expect(nodeStatus([claim('human', 'open')], null, NOW).human).toBe('open');
        expect(nodeStatus([claim('human', 'committed')], null, NOW).human).toBe('committed');
    });

    it('still says "yours to send" when a feature holds both — one unsent span is enough', () => {
        const s = nodeStatus([claim('human', 'committed'), claim('human', 'open')], null, NOW);
        expect(s.human).toBe('open');
        // …and the other way round, so the answer does not depend on claim order.
        const t = nodeStatus([claim('human', 'open'), claim('human', 'committed')], null, NOW);
        expect(t.human).toBe('open');
    });

    it('separates a plan awaiting a verdict from one already accepted', () => {
        expect(nodeStatus([claim('plan', 'proposed')], null, NOW).plan).toBe('proposed');
        expect(nodeStatus([claim('plan', 'accepted')], null, NOW).plan).toBe('accepted');
    });

    it('signs the code diff by direction', () => {
        expect(nodeStatus([claim('code', 'landed', 'add')], null, NOW).diff).toBe('add');
        expect(nodeStatus([claim('code', 'landed', 'del')], null, NOW).diff).toBe('del');
        expect(nodeStatus([claim('code', 'landed', 'add'), claim('code', 'landed', 'del')], null, NOW).diff)
            .toBe('both');
    });
});

describe('nodeStatus — accumulation, which is the point', () => {
    it('wears all three at once: your edit, over a plan, over what the code says', () => {
        const s = nodeStatus(
            [claim('human', 'open'), claim('plan', 'accepted'), claim('code', 'landed', 'del')],
            null, NOW,
        );
        expect(s).toEqual({ human: 'open', plan: 'accepted', diff: 'del' });
        expect(slots(s)).toEqual(['human', 'plan', 'diff']);
    });

    it('says a plan was built AND that the build changed it — the case ranking could not say', () => {
        const f: Fulfilment = { channel: 'plan', at: NOW - 1000, diverged: 'both' };
        const s = nodeStatus([], f, NOW);
        expect(s.plan).toBe('fulfilled');
        expect(s.diff).toBe('both');
        expect(statusGlyphs(s).find(g => g.slot === 'diff')?.text).toBe('±');
        expect(statusTitle(s)).toContain('differs from what was asked for');
    });

    it('fills your dot when the code catches up, with no sign if it landed as written', () => {
        const s = nodeStatus([], { channel: 'human', at: NOW, diverged: 'none' }, NOW);
        expect(s).toEqual({ human: 'fulfilled', plan: 'none', diff: 'none' });
        expect(statusGlyphs(s)[0].cls).toContain('fulfilled');
    });

    it('lets a live claim outrank a stale fulfilment on the same slot', () => {
        // You edited again after it was built — that is the fact that still needs acting on.
        const s = nodeStatus([claim('human', 'open')], { channel: 'human', at: NOW, diverged: 'none' }, NOW);
        expect(s.human).toBe('open');
    });
});

describe('when a marker is dropped', () => {
    it('keeps a fulfilment only for its window', () => {
        const f: Fulfilment = { channel: 'plan', at: NOW - FULFILMENT_TTL_MS - 1, diverged: 'add' };
        expect(expire(f, NOW)).toBe(true);
        expect(nodeStatus([], f, NOW)).toEqual(SETTLED);
    });

    it('never expires a live claim — a condition that fades out is a lie', () => {
        const claims = [claim('human', 'open'), claim('plan', 'proposed')];
        const later = NOW + FULFILMENT_TTL_MS * 100;
        expect(nodeStatus(claims, null, later)).toEqual(nodeStatus(claims, null, NOW));
    });

    it('drops the whole marker once every claim is gone and the ack has been shown', () => {
        expect(isSettled(nodeStatus([], { channel: 'human', at: 0, diverged: 'none' }, NOW))).toBe(true);
    });
});

describe('the glyphs', () => {
    it('reads left to right: whose, planned, drifted', () => {
        const s = nodeStatus(
            [claim('human', 'committed'), claim('plan', 'proposed'), claim('code', 'landed', 'add')],
            null, NOW,
        );
        expect(statusGlyphs(s).map(g => g.text ?? '●')).toEqual(['●', '●', '+']);
        expect(slots(s)).toEqual(['human', 'plan', 'diff']);
    });

    it('gives every glyph a sentence saying what to do about it', () => {
        for (const g of statusGlyphs(nodeStatus([claim('human', 'open'), claim('plan', 'proposed')], null, NOW))) {
            expect(g.title.length).toBeGreaterThan(10);
        }
        expect(statusTitle(nodeStatus([claim('human', 'open')], null, NOW))).toContain('⌘S');
    });
});
