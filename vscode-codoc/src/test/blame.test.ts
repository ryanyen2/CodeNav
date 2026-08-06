/**
 * blame.test.ts — the History (blame) stance (W2): the pure model derivations and
 * the decoration builder. Visual rail/label placement is EDH; load-bearing rules:
 *   - HLC timestamp → readable relative time.
 *   - actor → role (human / agent / loop) drives the hue class.
 *   - stance off → zero decorations; on → one label + rail per feature-with-history.
 *   - the hover tooltip carries the full who·when·why trace.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import {
    actorRole, actorLabel, hlcWallMs, relativeTime, kindPhrase, blameSummaryFrom,
} from '../state/blame-model';
import { buildBlameDecorations, blameTooltip } from '../webview/tiptap/blame-decorations';
import type { HistoryEntry } from '../state/bindings-model';

const NOW = 1_000_000_000_000;
const hlc = (msAgo: number): string => `${String(NOW - msAgo).padStart(20, '0')}-${'0'.repeat(20)}-n`;

describe('blame-model: actor role + label', () => {
    it('classifies actors', () => {
        expect(actorRole('human')).toBe('human');
        expect(actorRole('')).toBe('human');
        expect(actorRole('loop')).toBe('loop');
        expect(actorRole('claude-code')).toBe('agent');
    });
    it('labels actors', () => {
        expect(actorLabel('human')).toBe('You');
        expect(actorLabel('loop')).toBe('codoc');
        expect(actorLabel('claude-code')).toBe('claude-code');
    });
});

describe('blame-model: time', () => {
    it('extracts wall ms from an HLC string', () => {
        expect(hlcWallMs(hlc(0))).toBe(NOW);
        expect(hlcWallMs('garbage')).toBeNaN();
    });
    it('renders relative time in buckets', () => {
        expect(relativeTime(hlc(10_000), NOW)).toBe('just now');
        expect(relativeTime(hlc(5 * 60_000), NOW)).toBe('5m ago');
        expect(relativeTime(hlc(3 * 3_600_000), NOW)).toBe('3h ago');
        expect(relativeTime(hlc(2 * 86_400_000), NOW)).toBe('2d ago');
        expect(relativeTime(hlc(30 * 86_400_000), NOW)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
});

describe('blame-model: kindPhrase + summary', () => {
    it('phrases op kinds', () => {
        expect(kindPhrase('add_node')).toBe('created');
        expect(kindPhrase('amend')).toBe('edited');
    });
    it('summarizes the latest change', () => {
        const hist: HistoryEntry[] = [
            { at: hlc(3 * 3_600_000), kind: 'amend', actor: 'human', mode: 'pen' },
            { at: hlc(86_400_000), kind: 'add_node', actor: 'claude-code', mode: 'auto' },
        ];
        const s = blameSummaryFrom(hist, NOW);
        expect(s.role).toBe('human');
        expect(s.line).toBe('You edited · 3h ago');
    });
});

// ── decoration builder ────────────────────────────────────────────────────────

function docWith(fids: string[]): PMModelNode {
    const content = fids.flatMap(fid => ([
        { type: 'featureHeading', attrs: { fid, level: 0, retired: false, realized: true }, content: [{ type: 'text', text: fid }] },
        { type: 'paragraph', content: [{ type: 'text', text: 'Body prose.' }] },
    ]));
    return codocSchema().nodeFromJSON({ type: 'doc', content });
}

function attrsOf(d: unknown): { class?: string; title?: string } | undefined {
    return (d as { type?: { attrs?: { class?: string; title?: string } } } | undefined)?.type?.attrs;
}

const history: Record<string, HistoryEntry[]> = {
    'f-a': [
        { at: hlc(3 * 3_600_000), kind: 'amend', actor: 'claude-code', mode: 'auto', rationale: 'clarified sessions' },
        { at: hlc(86_400_000), kind: 'add_node', actor: 'human', mode: 'pen', rationale: 'created for login' },
    ],
};

describe('buildBlameDecorations', () => {
    it('is empty when the stance is off', () => {
        expect(buildBlameDecorations(docWith(['f-a']), false, history, NOW).find().length).toBe(0);
    });

    it('decorates a feature-with-history: heading node + label widget + body rail', () => {
        const set = buildBlameDecorations(docWith(['f-a']), true, history, NOW);
        const all = set.find();
        const classes = all.map(d => attrsOf(d)?.class);
        expect(classes).toContain('ce-blame ce-blame-agent');       // heading node, last amender = agent
        expect(classes).toContain('ce-blame-rail ce-blame-agent');  // body rail in the same hue
        // node + widget label + rail = 3 (the widget's class lives on its DOM, not attrs).
        expect(all.length).toBe(3);
    });

    it('skips a feature with no recorded history', () => {
        const set = buildBlameDecorations(docWith(['f-a', 'f-b']), true, history, NOW);
        // only f-a has history → its 3 decorations, none for f-b
        expect(set.find().every(d => !attrsOf(d)?.class?.includes('f-b'))).toBe(true);
        expect(set.find().length).toBe(3);
    });

    it('builds the full who·when·why hover trace (attached to the label widget)', () => {
        const tip = blameTooltip(history['f-a'], NOW);
        expect(tip).toContain('claude-code edited · 3h ago — clarified sessions');
        expect(tip).toContain('You created · 1d ago — created for login');
    });

    it('blameTooltip omits the relative time when the HLC is unparseable', () => {
        const tip = blameTooltip([{ at: 'x', kind: 'amend', actor: 'human', mode: 'pen' }], NOW);
        expect(tip).toBe('You edited');
    });
});
