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

describe('the comment marker (the surface a sent note is actually found on)', () => {
    const docWith = async (text: string, threadId: string | null, from = 0, to = 0) => {
        const { codocSchema } = await import('../webview/tiptap/schema');
        const schema = codocSchema();
        const runs: Record<string, unknown>[] = threadId
            ? [
                ...(from > 0 ? [{ type: 'text', text: text.slice(0, from) }] : []),
                { type: 'text', text: text.slice(from, to),
                  marks: [{ type: 'comment', attrs: { threadId } }] },
                ...(to < text.length ? [{ type: 'text', text: text.slice(to) }] : []),
              ]
            : [{ type: 'text', text }];
        return schema.nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: 'f-1', level: 0, retired: false, realized: true },
                  content: [{ type: 'text', text: 'Sessions' }] },
                { type: 'paragraph', content: runs },
            ],
        });
    };
    const thread = (over: Partial<CommentThread> = {}): CommentThread => ({
        id: 'cm-1', featureId: 'f-1', anchorText: 'the words', body: 'cap it',
        status: 'sent', author: 'human', createdAt: 1, ...over,
    });

    it('marks the anchored span, so a sent note is visible at every window width', async () => {
        // Without this the only trace of a sent comment was a dotted underline, and the
        // card behind it rendered only where the margin happened to fit one.
        const { buildCommentAnchors } = await import('../webview/tiptap/comment-anchor');
        const doc = await docWith('Some words here.', 'cm-1', 5, 10);
        expect(buildCommentAnchors(doc, [thread()]).find()).toHaveLength(1);
    });

    it('places the marker after the span, like a footnote reference', async () => {
        const { buildCommentAnchors, commentAnchorEnds } = await import('../webview/tiptap/comment-anchor');
        const doc = await docWith('Some words here.', 'cm-1', 5, 10);
        const end = commentAnchorEnds(doc).get('cm-1');
        expect(buildCommentAnchors(doc, [thread()]).find()[0].from).toBe(end);
    });

    it('draws nothing for a thread whose span is gone', async () => {
        // The prose moved on; a marker with nothing under it would point at whatever
        // happened to be at that offset.
        const { buildCommentAnchors } = await import('../webview/tiptap/comment-anchor');
        const doc = await docWith('Some words here.', null);
        expect(buildCommentAnchors(doc, [thread()]).find()).toEqual([]);
    });

    it('counts the replies a thread has received', async () => {
        const { replyCount } = await import('../webview/tiptap/comment-anchor');
        expect(replyCount(thread())).toBe(0);
        expect(replyCount(thread({ replies: [{ author: 'claude-code', body: 'Done.', at: '1' }] }))).toBe(1);
    });

    it('is empty with no threads', async () => {
        const { buildCommentAnchors } = await import('../webview/tiptap/comment-anchor');
        const doc = await docWith('Some words here.', 'cm-1', 5, 10);
        expect(buildCommentAnchors(doc, []).find()).toEqual([]);
    });
});

describe('replies reach the webview', () => {
    it('parses them off the sidecar', () => {
        const [t] = storedThreads({ comments: { 'f-1': [stored({
            replies: [{ author: 'claude-code', body: 'Done — changed upload.py.', at: hlc(5) }],
        })] } });
        expect(t.replies).toEqual([
            { author: 'claude-code', body: 'Done — changed upload.py.', at: hlc(5) },
        ]);
    });

    it('keeps the stored replies when the local copy has none', () => {
        // Replies only ever come from the store; preferring the local thread would drop
        // the answers it has already received.
        const merged = mergeThreads(
            storedThreads({ comments: { 'f-1': [stored({
                replies: [{ author: 'loop', body: 'Done.', at: hlc(5) }],
            })] } }),
            [local({ body: 'edited' })],
        );
        expect(merged[0].replies).toHaveLength(1);
        expect(merged[0].body).toBe('edited');
    });

    it('ignores a malformed reply row rather than losing the thread', () => {
        const [t] = storedThreads({ comments: { 'f-1': [stored({
            replies: [{ author: 'x' }, { author: 'claude-code', body: 'ok', at: hlc(1) }],
        })] } });
        expect(t.replies).toHaveLength(1);
    });
});
