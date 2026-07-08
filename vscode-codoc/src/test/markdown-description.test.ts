/**
 * #P0-1 — markdown syntax in a description (TS parser parity with
 * codoc/codoc_file/parse.py; see tests/codoc_file/test_roundtrip_markdown.py).
 *
 * A `-`/`~`/`#`/`+`/`*` line indented at (or past) the description indent
 * (owner_indent + 4, deeper than a direct child's owner_indent + 2) is prose the
 * author wrote — NOT a phantom child feature. render.py's 2-space-per-level scheme
 * makes this unambiguous; the parser must not mint ghost nodes or truncate the
 * description. A line carrying a ⟨f-id⟩ stays a feature (render never emits an id
 * inside a description).
 */
import { describe, it, expect } from 'vitest';
import { parseTreeCodoc } from '../state/tree-model';

describe('markdown in descriptions round-trips as prose', () => {
    it('a bullet list under a feature is one node, not a phantom child', () => {
        const { features } = parseTreeCodoc([
            '- Token auth  ⟨f-0000aaaa⟩',
            '    Handles:',
            '    - validates tokens',
            '    - checks expiry',
        ].join('\n'));
        expect(features).toHaveLength(1);
        expect(features[0].title).toBe('Token auth');
        expect(features[0].description).toBe('Handles:\n- validates tokens\n- checks expiry');
    });

    it('a heading / tilde / plus in a description stays prose', () => {
        const { features } = parseTreeCodoc([
            '- Feat  ⟨f-0000aaaa⟩',
            '    # Overview',
            '    ~ tilde line',
            '    + plus line',
        ].join('\n'));
        expect(features).toHaveLength(1);
        expect(features[0].description).toBe('# Overview\n~ tilde line\n+ plus line');
    });

    it('a real 2-space child is still a feature (canonical render)', () => {
        const { features } = parseTreeCodoc([
            '- Parent  ⟨f-0000aaaa⟩',
            '    Handles:',
            '    - a bullet',
            '  - Child  ⟨f-0000bbbb⟩',
            '      child prose',
        ].join('\n'));
        expect(features.map(f => f.title)).toEqual(['Parent', 'Child']);
        expect(features[1].parent_id).toBe('f-0000aaaa');
        expect(features[0].description).toBe('Handles:\n- a bullet');
    });

    it('a mis-indented but id-bearing child is still a feature (escape hatch)', () => {
        const { features } = parseTreeCodoc('- Parent  ⟨f-1⟩\n    - Child  ⟨f-2⟩\n');
        expect(features.map(f => f.title)).toEqual(['Parent', 'Child']);
        expect(features[1].parent_id).toBe('f-1');
    });
});
