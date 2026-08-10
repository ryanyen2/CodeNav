/**
 * suggestion-anchoring.test.ts — where a proposal's decorations land, against a real
 * ProseMirror doc.
 *
 * Two things a screenshot caught and a unit test can pin:
 *   • a proposed RETIRE covers the node's whole body, not just its title;
 *   • an ADD lands at the END of its destination parent's subtree (after the parent's
 *     own prose and its existing children), not wedged between the parent's heading
 *     and the parent's first paragraph, where it read as an edit to the parent.
 *
 * Plus the boundary the two share: a feature's OWN blocks stop at the next heading of
 * any level, while its SUBTREE runs to the next heading at its level or shallower.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import { locateFeatures } from '../webview/tiptap/suggestion-decorations';
import {
    makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, PMNode,
} from '../state/pm-doc';

const schema = codocSchema();

const head = (fid: string, level: number, title: string): PMNode =>
    featureHeadingNode({ fid, localId: null, level, retired: false, realized: true },
                       textToInlineRuns(title));
const para = (owner: string, text: string): PMNode => paragraphNode(textToInlineRuns(text), owner);

/** parent(L0) → [prose, child(L1) → prose, second child(L1) → prose] → sibling(L0). */
function tree(): PMNodeType {
    return PMNodeType.fromJSON(schema, makeDoc([
        head('f-parent', 0, 'Session request lifecycle'),
        para('f-parent', 'Manages a reusable HTTP session.'),
        head('f-kid1', 1, 'Redirect handling mixin'),
        para('f-kid1', 'Follows redirect responses.'),
        head('f-kid2', 1, 'Session QUERY helper'),
        para('f-kid2', 'Adds Session.query().'),
        para('f-kid2', 'A second paragraph of the same node.'),
        head('f-other', 0, 'Transport adapter base interface'),
        para('f-other', 'Carries a prepared request.'),
    ]) as never);
}

describe('locateFeatures — the two boundaries a proposal needs', () => {
    const loc = locateFeatures(tree());

    it("a feature's own body stops at the next heading of ANY level", () => {
        // The parent owns exactly one paragraph; its children are not its body.
        expect(loc.get('f-parent')!.body.map(b => b.node.textContent))
            .toEqual(['Manages a reusable HTTP session.']);
        // A multi-paragraph node keeps all of its own paragraphs.
        expect(loc.get('f-kid2')!.body).toHaveLength(2);
    });

    it("a feature's SUBTREE runs to the next heading at its level or shallower", () => {
        const parent = loc.get('f-parent')!;
        const other = loc.get('f-other')!;
        // The parent's subtree swallows both children and ends where the next L0 starts.
        expect(parent.subtreeEnd).toBe(other.headingPos);
        // Its own body ends much earlier — at the first child.
        expect(parent.bodyEnd).toBe(loc.get('f-kid1')!.headingPos);
        expect(parent.bodyEnd).toBeLessThan(parent.subtreeEnd);
    });

    it('a leaf feature has body === subtree', () => {
        const kid = loc.get('f-kid1')!;
        expect(kid.bodyEnd).toBe(kid.subtreeEnd);
    });

    it('the last feature in the document runs to the end of the doc', () => {
        const doc = tree();
        expect(loc.get('f-other')!.subtreeEnd).toBe(doc.content.size);
    });

    it('an ADD under the parent lands AFTER its existing children, not under its title', () => {
        const parent = loc.get('f-parent')!;
        const insertAt = parent.subtreeEnd;         // where buildDecorations puts the ghost
        const wedgedUnderTitle = parent.headingPos + parent.heading.nodeSize;  // the old spot
        expect(insertAt).toBeGreaterThan(wedgedUnderTitle);
        // and specifically past the last existing child's prose
        expect(insertAt).toBeGreaterThan(loc.get('f-kid2')!.body[1].pos);
    });

    it('a retire covers every block of the node, so nothing of it is left at full ink', () => {
        const kid = loc.get('f-kid2')!;
        const covered = [kid.headingPos, ...kid.body.map(b => b.pos)];
        expect(covered).toHaveLength(3);            // heading + both paragraphs
        // and stops before the next feature — a retire promotes children, it doesn't take them
        expect(Math.max(...covered)).toBeLessThan(loc.get('f-other')!.headingPos);
    });
});

describe('locateFeatures — survives the shapes a user actually leaves behind', () => {
    it('skips headings with no fid (a node the user just typed, pre-mint)', () => {
        const doc = PMNodeType.fromJSON(schema, makeDoc([
            head('f-a', 0, 'Real'),
            featureHeadingNode({ fid: null, localId: 'loc-1', level: 1, retired: false, realized: true },
                               textToInlineRuns('Just typed')),
            para('f-a', 'prose'),
        ]) as never);
        const loc = locateFeatures(doc);
        expect([...loc.keys()]).toEqual(['f-a']);
        // the un-minted heading still ENDS f-a's body — it is a real block boundary
        expect(loc.get('f-a')!.body).toHaveLength(0);
    });

    it('a heading the user added inside a node truncates that node, leaving the rest owned', () => {
        // The user split a feature in two while a proposal was pending on the first.
        const doc = PMNodeType.fromJSON(schema, makeDoc([
            head('f-a', 0, 'A'),
            para('f-a', 'first'),
            head('f-new', 1, 'New node the user typed'),
            para('f-new', 'second'),
        ]) as never);
        const loc = locateFeatures(doc);
        expect(loc.get('f-a')!.body.map(b => b.node.textContent)).toEqual(['first']);
        // a retire on f-a can no longer reach across into the user's new node
        expect(loc.get('f-a')!.bodyEnd).toBe(loc.get('f-new')!.headingPos);
    });

    it('an empty document locates nothing rather than throwing', () => {
        const doc = PMNodeType.fromJSON(schema, makeDoc([paragraphNode(textToInlineRuns(''), null)]) as never);
        expect(locateFeatures(doc).size).toBe(0);
    });
});
