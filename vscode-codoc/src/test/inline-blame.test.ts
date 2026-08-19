/**
 * inline-blame.test.ts — who wrote THIS sentence (W9).
 *
 * The per-node blame it replaces credited a whole feature to whoever touched it last.
 * These tests pin the two properties that make the finer answer worth trusting: words
 * carry their author forward across later edits, and a span the ledger cannot account
 * for stays UNATTRIBUTED rather than being credited to the nearest editor.
 */
import { describe, it, expect } from 'vitest';
import { BLAME_MIN_SPAN, blameDescription, significantSpans } from '../state/inline-blame';
import { buildTimeline } from '../state/revision-model';
import type { RevisionEntry, RevisionsFile, Timeline } from '../state/revision-model';

const T0 = 1_700_000_000_000;
const hlc = (msAfter: number): string =>
    `${String(T0 + msAfter).padStart(20, '0')}-${'0'.repeat(20)}-n`;

/** Events are given oldest-first here and reversed, the way the daemon writes them. */
function timelineOf(events: Partial<RevisionEntry>[]): Timeline {
    const revisions = events.map((e, i) => ({
        event_id: `e-${i}`, at: hlc(i * 300_000), kind: 'amend', feature_id: 'f-1',
        actor: 'human', mode: 'pen', ...e,
    })).reverse() as RevisionEntry[];
    return buildTimeline({ version: 1, revisions, directives: {} } as RevisionsFile);
}

const textOf = (s: string, span: { from: number; to: number }): string =>
    s.slice(span.from, span.to);

describe('blameDescription', () => {
    it('credits the words an event introduced, not the whole feature', () => {
        const current = 'Handles login. Retries three times.';
        const spans = blameDescription(current, timelineOf([
            { prev_description: '', description: 'Handles login.', actor: 'human' },
            { prev_description: 'Handles login.', description: current, actor: 'claude-code' },
        ]), 'f-1');

        const claude = spans.filter(s => s.actor === 'claude-code');
        expect(claude.length).toBeGreaterThan(0);
        expect(claude.map(s => textOf(current, s)).join('')).toContain('Retries three times.');
        // …and the human's original sentence is still the human's.
        const human = spans.filter(s => s.actor === 'human');
        expect(human.map(s => textOf(current, s)).join('')).toContain('Handles login.');
    });

    it('carries authorship forward across an edit that kept the words', () => {
        // The point of replaying rather than reading the newest event: a later editor
        // must not inherit the sentences they merely left alone.
        const current = 'Handles login. Caches the token.';
        const spans = blameDescription(current, timelineOf([
            { prev_description: '', description: 'Handles login.', actor: 'human' },
            { prev_description: 'Handles login.', description: 'Handles login. Caches the token.', actor: 'loop' },
        ]), 'f-1');
        const owner = spans.find(s => textOf(current, s).includes('Handles login'));
        expect(owner?.actor).toBe('human');
    });

    it('drops attribution for text it never saw written', () => {
        // Prose older than the window, or typed since the last recorded event, has no
        // honest owner — crediting it to the nearest editor is exactly the error the
        // per-node version made.
        const current = 'Ancient prose that predates the window.';
        expect(blameDescription(current, timelineOf([
            { prev_description: current, description: current, actor: 'claude-code' },
        ]), 'f-1')).toEqual([]);
    });

    it('gives up attribution rather than sliding it when the chain breaks', () => {
        // A recorded `prev` that disagrees with the replay means a write is missing from
        // the window. Trusting the replay would offset every later span by the difference
        // and confidently blame the wrong words.
        const current = 'One. Two. Three.';
        const spans = blameDescription(current, timelineOf([
            { prev_description: '', description: 'One.', actor: 'human' },
            { prev_description: 'COMPLETELY DIFFERENT.', description: current, actor: 'codex' },
        ]), 'f-1');
        expect(spans.every(s => s.actor === 'codex')).toBe(true);
    });

    it('ignores events on other features', () => {
        const current = 'Mine.';
        const spans = blameDescription(current, timelineOf([
            { prev_description: '', description: 'Theirs.', feature_id: 'f-other', actor: 'codex' },
            { prev_description: '', description: 'Mine.', actor: 'human' },
        ]), 'f-1');
        expect(spans.every(s => s.actor === 'human')).toBe(true);
    });

    it('is empty with no history and with no text', () => {
        expect(blameDescription('anything', buildTimeline(null), 'f-1')).toEqual([]);
        expect(blameDescription('', timelineOf([{ description: 'x' }]), 'f-1')).toEqual([]);
    });

    it('never reports a span past the end of the current text', () => {
        const current = 'Short.';
        for (const s of blameDescription(current, timelineOf([
            { prev_description: '', description: 'A much longer original sentence.', actor: 'human' },
            { prev_description: 'A much longer original sentence.', description: current, actor: 'loop' },
        ]), 'f-1')) {
            expect(s.to).toBeLessThanOrEqual(current.length);
            expect(s.from).toBeLessThan(s.to);
        }
    });
});

describe('significantSpans', () => {
    const span = (from: number, to: number, actor = 'human') => ({ from, to, actor, at: hlc(0) });

    it('drops runs too short to mean anything', () => {
        // Usually one party's punctuation inside another's sentence.
        expect(significantSpans([span(0, 3)], 100)).toEqual([]);
    });

    it('draws nothing when one author owns the whole description', () => {
        // There is nothing to distinguish, and the heading's own label already says who.
        // Underlining every word to report "all of this is by one person" is the
        // node-level signal we removed, re-drawn per character.
        expect(significantSpans([span(0, 100)], 100)).toEqual([]);
    });

    it('keeps a lone span that covers only part of the text', () => {
        const only = span(0, BLAME_MIN_SPAN + 2);
        expect(significantSpans([only], 100)).toEqual([only]);
    });

    it('keeps every substantial span once authorship is actually mixed', () => {
        const a = span(0, 40, 'human');
        const b = span(40, 90, 'claude-code');
        expect(significantSpans([a, b], 90)).toEqual([a, b]);
    });
});

describe('the decoration layer draws authorship per span', () => {
    it('underlines only the words a different party wrote', async () => {
        const { buildBlameDecorations } = await import('../webview/tiptap/blame-decorations');
        const { codocSchema } = await import('../webview/tiptap/schema');
        const human = 'Handles login. ';
        const agent = 'Retries three times on failure.';

        const doc = codocSchema().nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: 'f-1', level: 0, retired: false, realized: true },
                  content: [{ type: 'text', text: 'Sessions' }] },
                { type: 'paragraph', content: [{ type: 'text', text: human + agent }] },
            ],
        });
        const timeline = timelineOf([
            { prev_description: '', description: human, actor: 'human' },
            { prev_description: human, description: human + agent, actor: 'claude-code' },
        ]);
        const set = buildBlameDecorations(doc, true, { 'f-1': [
            { at: hlc(300_000), kind: 'amend', actor: 'claude-code', mode: 'auto' },
        ] }, T0 + 600_000, { timeline, directives: {}, files: () => [] });

        const cls = (d: { type?: { attrs?: Record<string, string> } }): string =>
            d.type?.attrs?.class ?? '';
        const spans = set.find()
            .map(d => d as unknown as { from: number; to: number; type?: { attrs?: Record<string, string> } })
            .filter(d => cls(d).includes('ce-blame-span'));

        // Two authors, two spans, drawn where each of them actually wrote.
        expect(spans).toHaveLength(2);
        const [first, second] = spans.sort((a, b) => a.from - b.from);
        expect(cls(first)).toContain('ce-blame-human');
        expect(cls(second)).toContain('ce-blame-agent');
        // The boundary falls between the two sentences, not at the paragraph edge — which
        // is the whole point: the reader can see which claim the agent made. Offsets are
        // measured from the paragraph's own start (+1 for the display-space contract: a
        // textblock's content begins one position after the block).
        let paraPos = 0;
        doc.forEach((node, pos) => { if (node.type.name === 'paragraph') paraPos = pos; });
        expect(first.from).toBe(paraPos + 1);
        expect(second.from).toBe(paraPos + 1 + human.length);
        expect(second.to).toBe(paraPos + 1 + human.length + agent.length);
    });
});
