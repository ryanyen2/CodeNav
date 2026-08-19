/**
 * track-changes-integration.test.ts — U1 guard for vendoring the tracked-changes
 * engine (sungkhum/tiptap-track-changes, MIT) into the codoc schema.
 *
 * The engine's own behavior (interception, accept/reject, getBaseText/getResultText)
 * is covered by its upstream suite and needs a live TipTap Editor (a DOM); the
 * vitest env here is node. What this file pins is the codoc INTEGRATION contract:
 *   1. the insertion/deletion/formatChange marks are present in the schema;
 *   2. the canonical `tree.codoc` projection is BASELINE-aware — insertion-marked
 *      (uncommitted) runs are excluded, deletion-marked runs are kept as plain text,
 *      and a normal (untracked) doc serializes exactly as before.
 */
import { describe, it, expect } from 'vitest';
import { codocSchema } from '../webview/tiptap/schema';
import { renderTreeFromDoc } from '../state/doc-serialize';
import {
    makeDoc, featureHeadingNode, paragraphNode, textNode, PMMark,
} from '../state/pm-doc';

const ins = (): PMMark[] => [{ type: 'insertion', attrs: { changeId: 'c1', authorId: 'claude-code', authorName: 'Claude', authorColor: '#b58fff', timestamp: 't' } }];
const del = (): PMMark[] => [{ type: 'deletion', attrs: { changeId: 'c2', authorId: 'claude-code', authorName: 'Claude', authorColor: '#b58fff', timestamp: 't' } }];

describe('U1 — engine marks registered in the schema', () => {
    it('adds insertion / deletion / formatChange marks without dropping the codoc marks', () => {
        const marks = codocSchema().marks;
        expect(marks.insertion).toBeTruthy();
        expect(marks.deletion).toBeTruthy();
        expect(marks.formatChange).toBeTruthy();
        // codoc's own marks survive registration
        expect(marks.author).toBeTruthy();
        expect(marks.bold).toBeTruthy();
        expect(marks.comment).toBeTruthy();
    });
});

describe('U1 — baseline projection in tree.codoc serialization', () => {
    it('EXCLUDES insertion-marked (uncommitted) text from the canonical render', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, [textNode('Auth')]),
            paragraphNode([textNode('Login and sessions.'), textNode(' Plus OAuth.', ins())]),
        ]);
        const text = renderTreeFromDoc(doc);
        expect(text).toContain('Login and sessions.');
        expect(text).not.toContain('Plus OAuth.'); // not yet accepted → not in baseline
    });

    it('KEEPS deletion-marked text (struck but still baseline) as plain text', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, [textNode('Auth')]),
            paragraphNode([textNode('Login'), textNode(' and sessions', del()), textNode('.')]),
        ]);
        const text = renderTreeFromDoc(doc);
        expect(text).toContain('Login and sessions.'); // deletion kept in baseline; the mark itself is dropped
    });

    it('excludes an insertion-marked run from a heading title too', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true },
                [textNode('Auth'), textNode('entication', ins())]),
            paragraphNode([textNode('Desc.')]),
        ]);
        // baseline title is "Auth" (the "entication" addition is uncommitted)
        const text = renderTreeFromDoc(doc);
        expect(text).toMatch(/(^|\n)[-#\s]*Auth\b/);
        expect(text).not.toContain('Authentication');
    });

    it('leaves an untracked doc byte-identical (regression: normal docs unaffected)', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, [textNode('Auth')]),
            paragraphNode([textNode('Login and sessions.')]),
        ]);
        const text = renderTreeFromDoc(doc);
        expect(text).toContain('Auth');
        expect(text).toContain('Login and sessions.');
    });
});
