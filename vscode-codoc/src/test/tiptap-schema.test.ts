/**
 * tiptap-schema.test.ts — U1 parity guard.
 *
 * Builds the ProseMirror schema from the TipTap extensions HEADLESSLY (getSchema,
 * no DOM) and proves it accepts and faithfully round-trips the exact JSON the pure
 * `pm-doc` constructors produce — so the live editor and the serializer share one
 * model. Also re-serializes a schema-validated doc through `renderTreeFromDoc` to
 * confirm the two halves compose.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import {
    makeDoc,
    featureHeadingNode,
    paragraphNode,
    textNode,
    codeRefNode,
    textToInlineRuns,
    PMNode,
} from '../state/pm-doc';
import { renderTreeFromDoc } from '../state/doc-serialize';

const schema = codocSchema();

/** Validate a pm-doc JSON against the schema and round-trip it through PM. */
function validate(json: PMNode): PMNode {
    const node = PMNodeType.fromJSON(schema, json as never);
    node.check(); // throws if the doc violates the schema
    return node.toJSON() as PMNode;
}

describe('U1: schema accepts the pm-doc vocabulary', () => {
    it('round-trips a featureHeading with all attrs', () => {
        const doc = makeDoc([
            featureHeadingNode(
                { fid: 'f-1a2b3c4d', level: 2, retired: true, realized: false },
                textToInlineRuns('Some title'),
            ),
        ]);
        const out = validate(doc);
        const h = out.content![0];
        expect(h.type).toBe('featureHeading');
        expect(h.attrs).toMatchObject({ fid: 'f-1a2b3c4d', level: 2, retired: true, realized: false });
    });

    it('round-trips a codeRef inline atom with its attrs', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-aaaa0001', level: 0, retired: false, realized: true }, [
                textNode('see '),
                codeRefNode({ label: 'parse', file: 'parse.py', symbol: 'parse_text' }),
            ]),
        ]);
        const out = validate(doc);
        const ref = out.content![0].content!.find(n => n.type === 'codeRef')!;
        expect(ref.attrs).toMatchObject({ label: 'parse', file: 'parse.py', symbol: 'parse_text' });
    });

    it('round-trips the author mark attrs on a text span', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-bbbb0002', level: 0, retired: false, realized: true }, [
                textNode('agent text', [
                    { type: 'author', attrs: { authorId: 'a1', role: 'claude-code', mode: 'pencil', ts: 42 } },
                ]),
            ]),
        ]);
        const out = validate(doc);
        const span = out.content![0].content![0];
        const authorMark = (span.marks ?? []).find(m => m.type === 'author')!;
        expect(authorMark.attrs).toMatchObject({ role: 'claude-code', mode: 'pencil', ts: 42 });
    });

    it('accepts paragraphs as description blocks and the bold mark', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-cccc0003', level: 0, retired: false, realized: true }, textToInlineRuns('T')),
            paragraphNode([textNode('bold', [{ type: 'bold' }]), textNode(' plain')]),
        ]);
        const out = validate(doc);
        expect(out.content!.map(n => n.type)).toEqual(['featureHeading', 'paragraph']);
        expect(out.content![1].content![0].marks).toEqual([{ type: 'bold' }]);
    });

    it('a schema-validated doc still serializes to tree.codoc', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-dddd0004', level: 0, retired: false, realized: true }, textToInlineRuns('Root')),
            paragraphNode(textToInlineRuns('Uses [x](codoc:a.py#fn).')),
        ]);
        validate(doc); // schema-valid
        expect(renderTreeFromDoc(doc)).toBe('- Root  ⟨f-dddd0004⟩\n    Uses [x](codoc:a.py#fn).\n');
    });
});

describe('U1: schema shape', () => {
    it('has the custom nodes and marks, and not the disabled ones', () => {
        expect(schema.nodes.featureHeading).toBeDefined();
        expect(schema.nodes.codeRef).toBeDefined();
        expect(schema.nodes.paragraph).toBeDefined();
        expect(schema.marks.author).toBeDefined();
        expect(schema.marks.comment).toBeDefined();
        expect(schema.marks.bold).toBeDefined();
        // disabled in StarterKit.configure
        expect(schema.nodes.heading).toBeUndefined();
        expect(schema.nodes.bulletList).toBeUndefined();
        expect(schema.nodes.codeBlock).toBeUndefined();
    });

    it('has NO italic or highlight mark — a mark tree.codoc cannot carry is a lie', () => {
        // Both were toolbar buttons whose marks the serializer discarded: the author saw
        // the styling, saved, and the next daemon projection wiped it. Removing the mark
        // from the schema is what makes the control impossible to re-add by accident.
        expect(schema.marks.italic).toBeUndefined();
        expect(schema.marks.highlight).toBeUndefined();
    });
});
