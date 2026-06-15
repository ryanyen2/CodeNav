/**
 * comment-model.test.ts — the inline-comment lifecycle, the half the user can't see.
 *
 * A comment serializes to a `> …` steering line; Loop B drains it. These guard the
 * properties that keep the server-side loop robust:
 *   • injectComments is idempotent and round-trips through parse → render → inject
 *     (a no-op settle stays a no-op — no write loop);
 *   • reconcileComments folds the text back: harvest raw notes, flip drained → sent,
 *     drop settled/feature-gone, and converge (a second pass reports no change);
 *   • stripOrphanComments GCs dead anchor marks.
 */
import { describe, it, expect } from 'vitest';
import {
    CommentThread, commentNoteText, commentsByFid, injectComments,
    reconcileComments, reanchorComments, stripOrphanComments,
} from '../state/comment-model';
import { parseTreeCodoc } from '../state/tree-model';
import { parseTreeToDoc } from '../state/doc-deserialize';
import { renderTreeFromDoc } from '../state/doc-serialize';
import { makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns } from '../state/pm-doc';

const BASE = '- Auth  ⟨f-aaaa0001⟩\n    Validates user input.\n';

function thread(over: Partial<CommentThread> = {}): CommentThread {
    return {
        id: 'cm-1', featureId: 'f-aaaa0001', anchorText: 'input', body: 'handle unicode too',
        status: 'open', author: 'human', createdAt: 0, ...over,
    };
}

describe('commentNoteText', () => {
    it('rides the anchored snippet in as a `re "…":` lead-in', () => {
        expect(commentNoteText(thread())).toBe('re "input": handle unicode too');
    });
    it('falls back to the bare body with no anchor (a harvested raw note)', () => {
        expect(commentNoteText(thread({ anchorText: '' }))).toBe('handle unicode too');
    });
    it('collapses a multi-line anchor and truncates it', () => {
        const note = commentNoteText(thread({ anchorText: 'a'.repeat(80) }));
        expect(note.startsWith('re "' + 'a'.repeat(59) + '…"')).toBe(true);
    });
});

describe('injectComments', () => {
    it('returns the text unchanged with no comments', () => {
        expect(injectComments(BASE, new Map())).toBe(BASE);
    });

    it('splices the note line at the end of its feature block', () => {
        const out = injectComments(BASE, commentsByFid([thread()]));
        expect(out).toBe('- Auth  ⟨f-aaaa0001⟩\n    Validates user input.\n    > re "input": handle unicode too\n');
    });

    it('is idempotent', () => {
        const m = commentsByFid([thread()]);
        const once = injectComments(BASE, m);
        expect(injectComments(once, m)).toBe(once);
    });

    it('the parsed note is a comment, NOT part of the prose', () => {
        const out = injectComments(BASE, commentsByFid([thread()]));
        const f = parseTreeCodoc(out).features[0];
        expect(f.description).toBe('Validates user input.');
        expect(f.comments.map(c => c.text)).toEqual(['re "input": handle unicode too']);
    });

    it('survives the host settle round-trip (render drops it, inject re-adds it)', () => {
        // The no-op-settle property: inject(render(parse(injected))) === injected.
        const m = commentsByFid([thread()]);
        const injected = injectComments(BASE, m);
        const reSettled = injectComments(renderTreeFromDoc(parseTreeToDoc(injected)), m);
        expect(reSettled).toBe(injected);
    });

    it('emits a multi-line body as a contiguous `>` run (parse rejoins it)', () => {
        const m = commentsByFid([thread({ anchorText: '', body: 'line one\nline two' })]);
        const out = injectComments(BASE, m);
        expect(out).toContain('    > line one\n    > line two\n');
        expect(parseTreeCodoc(out).features[0].comments[0].text).toBe('line one\nline two');
    });

    it('indents the note under a nested feature', () => {
        const nested = '- Root  ⟨f-aaaa0001⟩\n  - Sub  ⟨f-bbbb0002⟩\n    Does a thing.\n';
        const m = commentsByFid([thread({ featureId: 'f-bbbb0002', anchorText: '', body: 'tweak it' })]);
        expect(injectComments(nested, m)).toContain('\n      > tweak it\n'); // depth-1 indent (2) + 4
    });

    it('skips sent threads and null-fid (unminted) threads', () => {
        const m = commentsByFid([
            thread({ id: 'cm-s', status: 'sent' }),
            thread({ id: 'cm-n', featureId: null }),
        ]);
        expect(m.size).toBe(0);
        expect(injectComments(BASE, m)).toBe(BASE);
    });

    it('injects two sibling features\' notes each under its own block', () => {
        const two = '- A  ⟨f-aaaa0001⟩\n    alpha\n\n- B  ⟨f-bbbb0002⟩\n    beta\n';
        const m = commentsByFid([
            thread({ id: 'c1', featureId: 'f-aaaa0001', anchorText: '', body: 'note A' }),
            thread({ id: 'c2', featureId: 'f-bbbb0002', anchorText: '', body: 'note B' }),
        ]);
        const feats = parseTreeCodoc(injectComments(two, m)).features;
        expect(feats.find(f => f.id === 'f-aaaa0001')!.comments.map(c => c.text)).toEqual(['note A']);
        expect(feats.find(f => f.id === 'f-bbbb0002')!.comments.map(c => c.text)).toEqual(['note B']);
    });
});

describe('reanchorComments', () => {
    const cmMark = { type: 'comment', attrs: { threadId: 'cm-z' } };
    const docWithMark = makeDoc([
        featureHeadingNode({ fid: 'f-x', level: 0, retired: false, realized: true }, textToInlineRuns('Feat')),
        paragraphNode([textNode('validate', [cmMark]), textNode(' input')]),
    ]);

    it('re-anchors a null-fid thread to the now-minted feature carrying its mark', () => {
        const r = reanchorComments(docWithMark, [thread({ id: 'cm-z', featureId: null })]);
        expect(r.changed).toBe(true);
        expect(r.threads[0].featureId).toBe('f-x');
    });

    it('is a no-op when there is no open null-fid thread', () => {
        const threads = [thread({ featureId: 'f-x' })];
        expect(reanchorComments(docWithMark, threads)).toEqual({ threads, changed: false });
    });

    it('leaves a null-fid thread alone if its mark is not in the doc', () => {
        const bare = makeDoc([featureHeadingNode({ fid: 'f-x', level: 0, retired: false, realized: true }, textToInlineRuns('Feat'))]);
        const r = reanchorComments(bare, [thread({ id: 'cm-z', featureId: null })]);
        expect(r.changed).toBe(false);
        expect(r.threads[0].featureId).toBeNull();
    });
});

describe('reconcileComments', () => {
    const featuresWith = (note?: string): ReturnType<typeof parseTreeCodoc>['features'] =>
        parseTreeCodoc(note ? injectComments(BASE, new Map([['f-aaaa0001', [note]]])) : BASE).features;

    it('marks a freshly-written thread serialized once its line is in the text', () => {
        const note = commentNoteText(thread());
        const rc = reconcileComments(featuresWith(note), [thread({ serialized: false })], { inSync: false });
        expect(rc.changed).toBe(true);
        expect(rc.threads[0]).toMatchObject({ id: 'cm-1', status: 'open', serialized: true });
    });

    it('flips a serialized-then-vanished thread to sent (Loop B drained it)', () => {
        const rc = reconcileComments(featuresWith(), [thread({ serialized: true })], { inSync: false });
        expect(rc.changed).toBe(true);
        expect(rc.threads[0].status).toBe('sent');
    });

    it('keeps a not-yet-written thread (will be emitted), no false drain', () => {
        const rc = reconcileComments(featuresWith(), [thread({ serialized: false })], { inSync: false });
        expect(rc.threads[0]).toMatchObject({ status: 'open', serialized: false });
        expect(rc.changed).toBe(false);
    });

    it('drops a sent thread once the realize cycle settles (in_sync)', () => {
        const rc = reconcileComments(featuresWith(), [thread({ status: 'sent' })], { inSync: true });
        expect(rc.threads).toHaveLength(0);
        expect(rc.changed).toBe(true);
    });

    it('keeps a sent thread while the cycle is still running', () => {
        const rc = reconcileComments(featuresWith(), [thread({ status: 'sent' })], { inSync: false });
        expect(rc.threads).toHaveLength(1);
    });

    it('drops an open thread whose feature is gone', () => {
        const rc = reconcileComments(featuresWith(), [thread({ featureId: 'f-ZZZZ' })], { inSync: false });
        expect(rc.threads).toHaveLength(0);
        expect(rc.changed).toBe(true);
    });

    it('holds a null-fid (unminted) thread untouched', () => {
        const t = thread({ featureId: null, serialized: false });
        const rc = reconcileComments(featuresWith(), [t], { inSync: false });
        expect(rc.threads[0]).toEqual(t);
    });

    it('harvests a raw-editor `> …` note no thread accounts for, with a stable id', () => {
        const rc = reconcileComments(featuresWith('do it differently'), [], { inSync: false });
        expect(rc.changed).toBe(true);
        expect(rc.threads[0]).toMatchObject({
            featureId: 'f-aaaa0001', body: 'do it differently', status: 'open', serialized: true,
        });
        // deterministic — re-harvesting the same note yields the same id (no churn)
        const again = reconcileComments(featuresWith('do it differently'), [], { inSync: false });
        expect(again.threads[0].id).toBe(rc.threads[0].id);
    });

    it('converges: a second pass over its own output reports no change', () => {
        const note = commentNoteText(thread());
        const first = reconcileComments(featuresWith(note), [thread({ serialized: false })], { inSync: false });
        const second = reconcileComments(featuresWith(note), first.threads, { inSync: false });
        expect(second.changed).toBe(false);
        expect(second.threads).toEqual(first.threads);
    });

    // Two distinct threads on ONE feature must round-trip as TWO comments (regression
    // for the adjacent-`>`-lines merge that collapsed them, false-flipped both to sent,
    // and never converged).
    it('keeps two threads on one feature distinct through inject → parse → reconcile', () => {
        const t1 = thread({ id: 'cm-1', anchorText: 'a', body: 'first note' });
        const t2 = thread({ id: 'cm-2', anchorText: 'b', body: 'second note' });
        const stored = [{ ...t1, serialized: false }, { ...t2, serialized: false }];
        const text = injectComments(BASE, commentsByFid(stored));
        const feats = parseTreeCodoc(text).features;
        expect(feats[0].comments.map(c => c.text)).toEqual(['re "a": first note', 're "b": second note']);
        const rc = reconcileComments(feats, stored, { inSync: false });
        expect(rc.threads.map(t => t.id).sort()).toEqual(['cm-1', 'cm-2']);
        expect(rc.threads.every(t => t.status === 'open' && t.serialized)).toBe(true);
        // and it converges
        const second = reconcileComments(parseTreeCodoc(injectComments(BASE, commentsByFid(rc.threads))).features, rc.threads, { inSync: false });
        expect(second.changed).toBe(false);
    });

    // Editing a body keeps serialized:true; if the daemon then drains, the thread
    // goes sent (NOT resurrected) — guards the double-queue regression.
    it('an edited (serialized) thread whose new line then vanishes flips to sent, not re-emit', () => {
        const edited = thread({ body: 'edited body', serialized: true });
        // new line present → stays open serialized
        const present = reconcileComments(featuresWith(commentNoteText(edited)), [edited], { inSync: false });
        expect(present.threads[0]).toMatchObject({ status: 'open', serialized: true });
        // daemon drained it → sent (no false "created, not yet written")
        const drained = reconcileComments(featuresWith(), [edited], { inSync: false });
        expect(drained.threads[0].status).toBe('sent');
    });
});

describe('stripOrphanComments', () => {
    const cmMark = (id: string) => ({ type: 'comment', attrs: { threadId: id } });
    const doc = makeDoc([
        featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('T')),
        paragraphNode([textNode('alive', [cmMark('cm-live')]), textNode(' '), textNode('dead', [cmMark('cm-dead')])]),
    ]);

    it('removes comment marks with no live thread, keeps the live one', () => {
        const out = stripOrphanComments(doc, new Set(['cm-live']));
        const runs = out.content![1].content!;
        expect(runs[0].marks).toEqual([cmMark('cm-live')]);
        expect(runs[2].marks ?? []).toEqual([]); // cm-dead stripped
    });

    it('returns the same object when nothing is orphaned (cheap identity)', () => {
        expect(stripOrphanComments(doc, new Set(['cm-live', 'cm-dead']))).toBe(doc);
    });
});
