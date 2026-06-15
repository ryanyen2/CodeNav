/**
 * doc-roundtrip.test.ts — the gate for U2 (the highest-risk contract).
 *
 * Proves `renderTreeFromDoc(parseTreeToDoc(text))` reproduces canonical `tree.codoc`
 * bytes — i.e. the TS projection of the rich doc matches the Python `render_tree`,
 * so an unchanged doc yields zero `diff_codoc` ops and never wakes Loop B.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { parseTreeToDoc } from '../state/doc-deserialize';
import { renderTreeFromDoc } from '../state/doc-serialize';
import {
    makeDoc,
    featureHeadingNode,
    paragraphNode,
    textNode,
    codeRefNode,
    textToInlineRuns,
    inlineRunsToText,
    descriptionToBlocks,
    blocksToDescriptionText,
    descriptionBlocksForFid,
    PMNode,
} from '../state/pm-doc';
import { PENDING_SENTINEL } from '../state/tree-model';

/** Strip the stale leading `#` header + the pending-changes block + trailing blanks,
 *  yielding the headerless, ghost-less form today's `render_tree` emits. */
function canonicalLive(raw: string): string {
    const body = raw.split(PENDING_SENTINEL)[0];
    const lines = body.split('\n');
    let start = 0;
    while (start < lines.length && (lines[start].trim() === '' || lines[start].trimStart().startsWith('#'))) start++;
    return lines.slice(start).join('\n').replace(/\s+$/, '') + '\n';
}

function rt(text: string): string {
    return renderTreeFromDoc(parseTreeToDoc(text));
}

describe('U2: doc ↔ tree.codoc round-trip (real data)', () => {
    const realTree = resolve(process.cwd(), '../test/requests/.codoc/tree.codoc');

    it('reproduces the real requests tree byte-for-byte (28 features, 3 levels)', () => {
        if (!existsSync(realTree)) {
            console.warn(`skipping real-tree test: ${realTree} not found`);
            return;
        }
        const gold = canonicalLive(readFileSync(realTree, 'utf-8'));
        expect(gold.length).toBeGreaterThan(100);
        expect(rt(gold)).toBe(gold);
    });

    it('is idempotent on a second pass (fixpoint)', () => {
        if (!existsSync(realTree)) return;
        const gold = canonicalLive(readFileSync(realTree, 'utf-8'));
        expect(rt(rt(gold))).toBe(rt(gold));
    });
});

describe('U2: round-trip (synthetic — exact bytes)', () => {
    it('renders a two-level tree with the exact contract', () => {
        const text =
            '- Auth  ⟨f-cccc3333⟩\n' +
            '    Login + sessions.\n' +
            '\n' +
            '  - OAuth  ⟨f-dddd4444⟩\n' +
            '      Google / GitHub flow.\n';
        // two spaces before ⟨id⟩; child indented 2; child desc indented 6.
        expect(rt(text)).toBe(text);
        expect(rt(text)).toContain('- Auth  ⟨f-cccc3333⟩');
        expect(rt(text)).toContain('  - OAuth  ⟨f-dddd4444⟩');
        expect(rt(text)).toContain('      Google / GitHub flow.');
    });

    it('preserves the retired marker (~)', () => {
        const text = '~ Deprecated thing  ⟨f-aaaa0001⟩\n    Old prose.\n';
        expect(rt(text)).toBe(text);
    });

    it('preserves multi-paragraph descriptions (blank line)', () => {
        const text =
            '- Feature  ⟨f-bbbb0002⟩\n' +
            '    First paragraph.\n' +
            '\n' +
            '    Second paragraph.\n';
        expect(rt(text)).toBe(text);
    });

    it('preserves a deep three-level chain', () => {
        const text =
            '- A  ⟨f-1111aaaa⟩\n' +
            '\n' +
            '  - B  ⟨f-2222bbbb⟩\n' +
            '\n' +
            '    - C  ⟨f-3333cccc⟩\n' +
            '        Leaf prose.\n';
        expect(rt(text)).toBe(text);
    });

    it('handles a feature with no description', () => {
        const text = '- Bare  ⟨f-4444dddd⟩\n';
        expect(rt(text)).toBe(text);
    });
});

describe('U2: inline code references round-trip exactly', () => {
    it('round-trips a ref with a symbol', () => {
        const text = '- F  ⟨f-5555eeee⟩\n    See [the parser](codoc:parse.py#parse_text) for detail.\n';
        expect(rt(text)).toBe(text);
    });

    it('round-trips a ref without a symbol', () => {
        const text = '- F  ⟨f-6666ffff⟩\n    See [the module](codoc:render.py) here.\n';
        expect(rt(text)).toBe(text);
    });

    it('round-trips an empty label []', () => {
        const text = '- F  ⟨f-7777aaaa⟩\n    Bare [](codoc:db.py#Store) link.\n';
        expect(rt(text)).toBe(text);
    });

    it('weaves multiple refs in one paragraph', () => {
        const text =
            '- F  ⟨f-8888bbbb⟩\n' +
            '    Calls [a](codoc:a.py#fn_a) then [b](codoc:b.py#fn_b) at the end.\n';
        expect(rt(text)).toBe(text);
        const doc = parseTreeToDoc(text);
        const para = (doc.content ?? [])[1];
        const refs = (para.content ?? []).filter(n => n.type === 'codeRef');
        expect(refs).toHaveLength(2);
    });
});

describe('U2: structural editing semantics', () => {
    it('a newly authored heading (null fid) renders with NO id suffix → ADD_NODE shape', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: null, level: 0, retired: false, realized: true }, textToInlineRuns('Brand new')),
            paragraphNode(textToInlineRuns('A fresh idea.')),
        ]);
        const out = renderTreeFromDoc(doc);
        expect(out).toBe('- Brand new\n    A fresh idea.\n');
        expect(out).not.toContain('⟨');
    });

    it('editing a title changes exactly one line', () => {
        const text =
            '- One  ⟨f-aaa11111⟩\n    P1.\n\n- Two  ⟨f-bbb22222⟩\n    P2.\n\n- Three  ⟨f-ccc33333⟩\n    P3.\n';
        const doc = parseTreeToDoc(text);
        // mutate the middle heading's title in place.
        const heading = (doc.content ?? []).find(
            n => n.type === 'featureHeading' && (n.attrs as { fid?: string }).fid === 'f-bbb22222',
        )!;
        heading.content = textToInlineRuns('Two (edited)');
        const before = text.split('\n');
        const after = renderTreeFromDoc(doc).split('\n');
        const changed = before.filter((l, i) => l !== after[i]);
        expect(changed).toEqual(['- Two  ⟨f-bbb22222⟩']);
    });

    it('clamps a level skip (0 → 2) to skip-free indentation (0 → 1) — idempotent round-trip', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-1a2b3c40', level: 0, retired: false, realized: true }, textToInlineRuns('Parent')),
            featureHeadingNode({ fid: 'f-5d6e7f80', level: 2, retired: false, realized: true }, textToInlineRuns('Child')),
        ]);
        const out = renderTreeFromDoc(doc);
        // level 2 with no intervening level-1 renders at depth 1 (parent + 1), not 2.
        expect(out).toContain('  - Child  ⟨f-5d6e7f80⟩');
        expect(out).not.toContain('    - Child');
        expect(rt(out)).toBe(out); // and the result is a fixpoint
    });

    it('indenting a heading (level change) re-projects its depth → MOVE shape', () => {
        const text = '- Parent  ⟨f-1a2b3c40⟩\n\n- Sibling  ⟨f-5d6e7f80⟩\n';
        const doc = parseTreeToDoc(text);
        const sib = (doc.content ?? []).find(
            n => (n.attrs as { fid?: string })?.fid === 'f-5d6e7f80',
        )!;
        (sib.attrs as { level: number }).level = 1; // nest under Parent
        const out = renderTreeFromDoc(doc);
        expect(out).toContain('  - Sibling  ⟨f-5d6e7f80⟩'); // now indented 2
    });
});

describe('U2: inline text projection helpers', () => {
    it('inlineRunsToText drops marks but keeps codeRef markdown', () => {
        const runs: PMNode[] = [
            textNode('bold ', [{ type: 'strong' }]),
            codeRefNode({ label: 'x', file: 'a.py', symbol: 'fn' }),
            textNode(' end'),
        ];
        expect(inlineRunsToText(runs)).toBe('bold [x](codoc:a.py#fn) end');
    });
});

describe('U3: description ↔ paragraph blocks (per-section editor seam)', () => {
    it('round-trips a single-paragraph description', () => {
        const desc = 'Login and sessions, see [auth](codoc:auth.py#login).';
        expect(blocksToDescriptionText(descriptionToBlocks(desc))).toBe(desc);
    });

    it('round-trips a multi-paragraph description', () => {
        const desc = 'First paragraph.\n\nSecond paragraph with [x](codoc:a.py#y).';
        expect(blocksToDescriptionText(descriptionToBlocks(desc))).toBe(desc);
    });

    it('an empty description yields one empty paragraph and projects back to ""', () => {
        const blocks = descriptionToBlocks('');
        expect(blocks).toHaveLength(1);
        expect(blocks[0].type).toBe('paragraph');
        expect(blocksToDescriptionText(blocks)).toBe('');
    });

    it('drops marks (bold/author) when projecting blocks back to text', () => {
        const blocks: PMNode[] = [
            paragraphNode([
                textNode('solid ', [{ type: 'author', attrs: { role: 'human', mode: 'pen' } }]),
                textNode('and bold', [{ type: 'bold' }]),
            ]),
        ];
        expect(blocksToDescriptionText(blocks)).toBe('solid and bold');
    });

    it('descriptionBlocksForFid extracts the right feature description from a whole doc', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-aaaa0001', level: 0, retired: false, realized: true }, textToInlineRuns('A')),
            paragraphNode(textToInlineRuns('Desc for A.')),
            featureHeadingNode({ fid: 'f-bbbb0002', level: 0, retired: false, realized: true }, textToInlineRuns('B')),
            paragraphNode(textToInlineRuns('First B.')),
            paragraphNode(textToInlineRuns('Second B.')),
        ]);
        expect(blocksToDescriptionText(descriptionBlocksForFid(doc, 'f-bbbb0002'))).toBe('First B.\n\nSecond B.');
        expect(descriptionBlocksForFid(doc, 'f-missing')).toEqual([]);
    });
});
