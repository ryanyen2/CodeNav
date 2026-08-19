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
    normalizeDescription,
    PMNode,
} from '../state/pm-doc';
import { PENDING_SENTINEL } from '../state/tree-model';

// R19 — the TS canonical normalization must match codoc/codoc_file/parse.py
// (normalize_description). These cases mirror the Python test
// tests/codoc_file/test_roundtrip_idempotency.py; both sides must agree byte-for-byte
// or a description round-trips to a phantom diff between host and daemon.
describe('normalizeDescription — canonical form, parity with parse.normalize_description', () => {
    const cases: Array<[string, string]> = [
        ['Holds brand colors. ', 'Holds brand colors.'],          // trailing space
        ['Holds brand colors.\n', 'Holds brand colors.'],         // trailing newline
        ['  leading and trailing  ', 'leading and trailing'],
        ['a.\n\n\n\nb.', 'a.\n\nb.'],                              // collapse interior blank run
        ['\n\nonly.\n\n', 'only.'],                               // drop edge blanks
        ['First paragraph.\n\nSecond paragraph.', 'First paragraph.\n\nSecond paragraph.'], // fixed point
    ];
    it.each(cases)('normalizes %j', (input, expected) => {
        expect(normalizeDescription(input)).toBe(expected);
    });
    it('blocksToDescriptionText emits canonical text (trailing whitespace stripped)', () => {
        const blocks = [paragraphNode([textNode('Holds brand colors. ')])];
        expect(blocksToDescriptionText(blocks)).toBe('Holds brand colors.');
    });
});

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

describe('U7: blank-line normalization (no reflow / phantom paragraphs)', () => {
    const A = { fid: 'f-aaaa0001', level: 0, retired: false, realized: true };
    const B = { fid: 'f-bbbb0002', level: 0, retired: false, realized: true };

    it('drops a trailing empty paragraph (Enter after the text → no trailing-blank churn)', () => {
        const doc = makeDoc([
            featureHeadingNode(A, textToInlineRuns('A')),
            paragraphNode(textToInlineRuns('Desc.')),
            paragraphNode([]),  // user pressed Enter at the end
        ]);
        expect(renderTreeFromDoc(doc)).toBe('- A  ⟨f-aaaa0001⟩\n    Desc.\n');
    });

    it('collapses multiple empty paragraphs between two text paragraphs to one blank', () => {
        const doc = makeDoc([
            featureHeadingNode(A, textToInlineRuns('A')),
            paragraphNode(textToInlineRuns('P1.')),
            paragraphNode([]), paragraphNode([]),
            paragraphNode(textToInlineRuns('P2.')),
        ]);
        expect(renderTreeFromDoc(doc)).toBe('- A  ⟨f-aaaa0001⟩\n    P1.\n\n    P2.\n');
    });

    it('treats empty paragraphs between two features as cosmetic (no phantom feature/line)', () => {
        const doc = makeDoc([
            featureHeadingNode(A, textToInlineRuns('A')),
            paragraphNode(textToInlineRuns('Desc A.')),
            paragraphNode([]),  // cosmetic spacing before the next heading
            featureHeadingNode(B, textToInlineRuns('B')),
            paragraphNode(textToInlineRuns('Desc B.')),
        ]);
        expect(renderTreeFromDoc(doc)).toBe(
            '- A  ⟨f-aaaa0001⟩\n    Desc A.\n\n- B  ⟨f-bbbb0002⟩\n    Desc B.\n');
    });

    it('a freshly minted ## heading with an empty description renders clean (no id, no trailing blank)', () => {
        const doc = makeDoc([
            featureHeadingNode(A, textToInlineRuns('Existing')),
            paragraphNode(textToInlineRuns('Desc.')),
            featureHeadingNode({ fid: null, level: 0, retired: false, realized: true }, textToInlineRuns('New feature')),
            paragraphNode([]),  // the empty paragraph that follows a just-typed heading
        ]);
        expect(renderTreeFromDoc(doc)).toBe('- Existing  ⟨f-aaaa0001⟩\n    Desc.\n\n- New feature\n');
    });

    it('is a fixpoint: serializing the normalized output again is unchanged', () => {
        const doc = makeDoc([
            featureHeadingNode(A, textToInlineRuns('A')),
            paragraphNode(textToInlineRuns('P1.')),
            paragraphNode([]), paragraphNode([]),
            paragraphNode(textToInlineRuns('P2.')),
        ]);
        const once = renderTreeFromDoc(doc);
        expect(rt(once)).toBe(once);  // re-parse + re-render is stable
    });
});

describe('U2: inline text projection helpers', () => {
    it('inlineRunsToText drops presentation marks but keeps codeRef markdown', () => {
        const runs: PMNode[] = [
            textNode('noted ', [{ type: 'comment', attrs: { threadId: 't1' } }]),
            codeRefNode({ label: 'x', file: 'a.py', symbol: 'fn' }),
            textNode(' end'),
        ];
        expect(inlineRunsToText(runs)).toBe('noted [x](codoc:a.py#fn) end');
    });
});

// The bold mark is the ONE inline mark that is not presentation: `**…**` in the
// description is what `parse.extract_bold` lifts into a realize directive's `Focus:`
// line. Before this, the B button produced a mark `inlineRunsToText` silently threw
// away — the author saw bold, saved, and the next projection wiped it, and the signal
// the prompts document could not be produced from the only human surface at all.
//
// The load-bearing property is TEXT → doc → TEXT identity: the daemon projects the
// stored description into the doc, the webview serializes it back to compare against
// what it adopted, and any drift there is a phantom AMEND on every projection.
describe('bold: `**…**` ↔ the bold mark', () => {
    const bold = (t: string): PMNode => textNode(t, [{ type: 'bold' }]);

    it('serializes a bold run as **text**', () => {
        expect(inlineRunsToText([textNode('a '), bold('focus'), textNode(' b')]))
            .toBe('a **focus** b');
    });

    it('parses **text** back into a bold-marked run', () => {
        expect(textToInlineRuns('a **focus** b')).toEqual([
            textNode('a '), bold('focus'), textNode(' b'),
        ]);
    });

    it('wraps a maximal bold RUN once — never inside a link target', () => {
        const runs: PMNode[] = [
            bold('see '),
            codeRefNode({ label: 'x', file: 'a.py', symbol: 'fn' }, [{ type: 'bold' }]),
            bold(' now'),
            textNode(' tail'),
        ];
        expect(inlineRunsToText(runs)).toBe('**see [x](codoc:a.py#fn) now** tail');
    });

    it('merges adjacent bold runs into one span (not `**a****b**`)', () => {
        expect(inlineRunsToText([bold('a'), bold('b')])).toBe('**ab**');
    });

    it('emits nothing rather than `****` when the whole bold run is an insertion', () => {
        const ins = { type: 'insertion', attrs: { changeId: 'c1' } };
        expect(inlineRunsToText([textNode('kept '), textNode('gone', [{ type: 'bold' }, ins])]))
            .toBe('kept ');
    });

    it('leaves a bold run the parser could not read back UNWRAPPED', () => {
        // `**  **` strips to nothing in extract_bold, and `a*b` cannot sit inside
        // `[^*\n]+` — emitting either would change the stored text and signal nothing.
        expect(inlineRunsToText([bold('  ')])).toBe('  ');
        expect(inlineRunsToText([bold('a*b')])).toBe('a*b');
    });

    it('closes the span at a hard break instead of spanning a newline', () => {
        const runs: PMNode[] = [bold('a'), { type: 'hardBreak', marks: [{ type: 'bold' }] }, bold('b')];
        expect(inlineRunsToText(runs)).toBe('**a**\n**b**');
    });

    it('reads `**` inside a citation label as label text, not markup', () => {
        const runs = textToInlineRuns('[**x**](codoc:a.py#fn) after');
        expect(runs[0]).toEqual(codeRefNode({ label: '**x**', file: 'a.py', symbol: 'fn' }));
        expect(runs[0].marks).toBeUndefined();
    });

    it('marks a citation bold when the span encloses it', () => {
        const runs = textToInlineRuns('**see [x](codoc:a.py) now**');
        expect(runs.map(r => r.type)).toEqual(['text', 'codeRef', 'text']);
        expect(runs.every(r => r.marks?.some(m => m.type === 'bold'))).toBe(true);
    });

    // THE property. Every case here is a description the daemon could hand the editor;
    // serialize(parse(text)) must be the same bytes or the settle machinery mints an
    // edit nobody made. The list and the expected spans are mirrored VERBATIM in
    // tests/codoc_file/test_doc_render.py (`_BOLD_CASES`) — the daemon runs that side,
    // and if the two disagree they disagree about what the author wrote.
    const cases: Array<[string, string[]]> = [
        ['plain prose, no markers', []],
        ['a **focus** b', ['focus']],
        ['**leading** span', ['leading']],
        ['trailing **span**', ['span']],
        ['**two** spans **here**', ['two', 'here']],
        ['**see [x](codoc:a.py#fn) now** tail', ['see [x](codoc:a.py#fn) now']],
        ['[**x**](codoc:a.py#fn) label markers survive', []],
        ['**  ** whitespace-only marker pair', []],
        ['**a****b** touching pairs', ['a']],
        ['unmatched ** marker', []],
        ['***tripled***', ['tripled']],
        ['a **b*c** d', []],
        ['cite [y](codoc:g.py) then **focus**', ['focus']],
    ];

    /** The text each maximal run of bold-marked nodes covers — what the author sees
     *  emphasized, in the shape `extract_bold` returns. */
    function projectedBold(runs: PMNode[]): string[] {
        const out: string[] = [];
        let cur = '';
        for (const r of runs) {
            const t = inlineRunsToText([{ ...r, marks: undefined }]);
            if (r.marks?.some(m => m.type === 'bold')) cur += t;
            else if (cur) { out.push(cur); cur = ''; }
        }
        if (cur) out.push(cur);
        return out;
    }

    it.each(cases)('text → doc → text is the identity for %j', text => {
        expect(inlineRunsToText(textToInlineRuns(text))).toBe(text);
    });

    it.each(cases)('emphasizes exactly the Focus spans for %j', (text, spans) => {
        expect(projectedBold(textToInlineRuns(text))).toEqual(spans);
    });

    it('doc → text → doc is a fixpoint (no phantom edit on re-projection)', () => {
        for (const [text] of cases) {
            const doc = textToInlineRuns(text);
            expect(textToInlineRuns(inlineRunsToText(doc))).toEqual(doc);
        }
    });

    it('round-trips through a whole feature description', () => {
        const desc = 'Keep the **retry budget** bounded, see [backoff](codoc:net.py#backoff).';
        expect(blocksToDescriptionText(descriptionToBlocks(desc))).toBe(desc);
        expect(rt(`- F  ⟨f-9999cccc⟩\n    ${desc}\n`)).toBe(`- F  ⟨f-9999cccc⟩\n    ${desc}\n`);
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

    it('drops the author mark but writes bold back as **…**', () => {
        const blocks: PMNode[] = [
            paragraphNode([
                textNode('solid ', [{ type: 'author', attrs: { role: 'human', mode: 'pen' } }]),
                textNode('and bold', [{ type: 'bold' }]),
            ]),
        ];
        expect(blocksToDescriptionText(blocks)).toBe('solid **and bold**');
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
