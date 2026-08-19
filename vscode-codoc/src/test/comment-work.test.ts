/**
 * comment-work.test.ts — a comment as a unit of requested work (W8).
 *
 * The pure half: which code a note scopes itself to, and how the store's durable copy of
 * a thread merges with this host's un-drained one. The composer and the margin card are
 * EDH-verified like every other visual surface.
 */
import { describe, it, expect } from 'vitest';
import { codeRefsIn, mergeThreads, storedThreads } from '../state/comment-model';
import type { CommentThread } from '../state/comment-model';

const hlc = (ms: number): string => `${String(ms).padStart(20, '0')}-${'0'.repeat(20)}-n`;

function stored(over: Record<string, unknown> = {}): Record<string, unknown> {
    return {
        id: 'cm-1', feature_id: 'f-1', body: 'rate-limit this', author: 'human',
        status: 'sent', anchor_start: 0, anchor_end: 4,
        created_at: hlc(1_700_000_000_000), updated_at: hlc(1_700_000_000_000),
        ...over,
    };
}

function local(over: Partial<CommentThread> = {}): CommentThread {
    return {
        id: 'cm-1', featureId: 'f-1', anchorText: 'Accepts files.', body: 'rate-limit this',
        status: 'sent', author: 'human', createdAt: 1_700_000_000_000, ...over,
    };
}

describe('codeRefsIn', () => {
    it('reads the code a commented sentence already cites', () => {
        // The whole "which code?" mechanism, and it needed no picker: a description
        // cites its code inline, so the sentence you select usually already names it.
        expect(codeRefsIn('Retries via [handle](codoc:upload.py#handle) three times.'))
            .toEqual(['upload.py::handle']);
    });

    it('takes a file-only citation as the file', () => {
        expect(codeRefsIn('See [the module](codoc:upload.py).')).toEqual(['upload.py']);
    });

    it('dedupes and keeps document order', () => {
        expect(codeRefsIn('[a](codoc:b.py#x) then [c](codoc:a.py#y) then [d](codoc:b.py#x)'))
            .toEqual(['b.py::x', 'a.py::y']);
    });

    it('is empty for prose that cites nothing — which is what "no particular code" means', () => {
        expect(codeRefsIn('This should be faster.')).toEqual([]);
        expect(codeRefsIn('')).toEqual([]);
    });

    it('ignores an external link — a Consult URL is not a code target', () => {
        expect(codeRefsIn('per [the RFC](https://example.com/rfc)')).toEqual([]);
    });
});

describe('storedThreads', () => {
    it('reads the durable thread off the sidecar', () => {
        const [t] = storedThreads({ comments: { 'f-1': [stored({
            anchor_text: 'Accepts files.', code_refs: ['upload.py::handle'],
            scope: 'both', directive_id: 'd-abc',
        })] } });
        expect(t).toMatchObject({
            id: 'cm-1', featureId: 'f-1', body: 'rate-limit this', status: 'sent',
            anchorText: 'Accepts files.', codeRefs: ['upload.py::handle'],
            scope: 'both', directiveId: 'd-abc',
        });
        expect(t.createdAt).toBe(1_700_000_000_000);
    });

    it('defaults an unknown status to open rather than trusting the wire', () => {
        expect(storedThreads({ comments: { 'f-1': [stored({ status: 'nonsense' })] } })[0].status)
            .toBe('open');
    });

    it('skips a row with no id or no feature', () => {
        expect(storedThreads({ comments: { 'f-1': [stored({ id: '' }), stored({ feature_id: '' })] } }))
            .toEqual([]);
    });

    it('is empty for a sidecar with no comments slice', () => {
        expect(storedThreads({})).toEqual([]);
    });
});

describe('mergeThreads', () => {
    it('lets the local copy win, so a fresh note does not blink out of the margin', () => {
        // Between authoring a note and the daemon's next pass the store's copy is absent
        // or one revision behind; preferring it would erase what was just typed.
        const merged = mergeThreads(
            [storedThreads({ comments: { 'f-1': [stored({ body: 'old wording' })] } })[0]],
            [local({ body: 'new wording' })],
        );
        expect(merged).toHaveLength(1);
        expect(merged[0].body).toBe('new wording');
    });

    it('carries across what only the store knows', () => {
        const merged = mergeThreads(
            storedThreads({ comments: { 'f-1': [stored({ directive_id: 'd-abc' })] } }),
            [local({ body: 'edited' })],
        );
        expect(merged[0].directiveId).toBe('d-abc');
    });

    it('keeps a resolved thread resolved', () => {
        // The local copy is a stale optimistic 'sent' from before the close; un-resolving
        // would put a closed conversation back in the margin.
        const merged = mergeThreads(
            storedThreads({ comments: { 'f-1': [stored({ status: 'resolved' })] } }),
            [local({ status: 'sent' })],
        );
        expect(merged[0].status).toBe('resolved');
    });

    it('keeps threads that exist on only one side', () => {
        const merged = mergeThreads(
            storedThreads({ comments: { 'f-1': [stored({ id: 'cm-old' })] } }),
            [local({ id: 'cm-new', createdAt: 1_700_000_001_000 })],
        );
        expect(merged.map(t => t.id)).toEqual(['cm-old', 'cm-new']);
    });

    it('orders by age, so the margin reads oldest-first however the two sides arrived', () => {
        const merged = mergeThreads(
            storedThreads({ comments: { 'f-1': [stored({ id: 'cm-b', created_at: hlc(2000) })] } }),
            [local({ id: 'cm-a', createdAt: 1000 })],
        );
        expect(merged.map(t => t.id)).toEqual(['cm-a', 'cm-b']);
    });
});
