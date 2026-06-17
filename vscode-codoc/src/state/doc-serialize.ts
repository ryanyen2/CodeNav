/**
 * doc-serialize.ts — project the authoritative rich doc (`tree.doc.json`) down to
 * canonical `tree.codoc` text.
 *
 * `renderTreeFromDoc` MUST be byte-identical to the Python `render_tree`
 * (codoc/codoc_file/render.py) for the live-feature tree, so the existing
 * `parse.py → diff_codoc → apply_op` pipeline and the watch self-write guard keep
 * working: a faithful projection means an unchanged doc yields zero `diff_codoc`
 * ops. This is the single highest-risk contract in the feature — guarded by the
 * round-trip property tests in `src/test/doc-roundtrip.test.ts`.
 *
 * The Python contract being reproduced (render.py:91-110, _description_lines:59-67):
 *   title line   `{"  "*level}{marker} {title}  ⟨{fid}⟩`   (marker '-'/'~', TWO
 *                spaces before the id; no `  ⟨…⟩` suffix when fid is null/new)
 *   description  each line of `desc.split("\n")` → `{indent}    {line}` when the
 *                line is non-blank, else "" (blank lines stay truly blank)
 *   spacing      one blank line after every feature block
 *   trailing     `"\n".join(lines).rstrip() + "\n"`
 *
 * Authorship marks, bold/italic/highlight/comment, and pending ADD/MOVE ghosts are
 * NOT emitted — marks live only in the doc; ghosts are sidecar overlays, never
 * part of the authored doc.  No `_HEADER` is emitted (render_tree's header line is
 * commented out, so today's canonical output is headerless).
 */
import {
    NODE_FEATURE_HEADING,
    NODE_PARAGRAPH,
    PMNode,
    inlineRunsToText,
    blocksToDescriptionText,
    FeatureHeadingAttrs,
} from './pm-doc';

function headingAttrs(node: PMNode): FeatureHeadingAttrs {
    const a = (node.attrs ?? {}) as Partial<FeatureHeadingAttrs>;
    return {
        fid: a.fid ?? null,
        level: typeof a.level === 'number' ? a.level : 0,
        retired: a.retired === true,
        realized: a.realized !== false,
    };
}

/** Render one feature's title line at the given depth, matching render.py:95. */
function titleLine(attrs: FeatureHeadingAttrs, title: string, depth: number): string {
    const indent = '  '.repeat(depth);
    const marker = attrs.retired ? '~' : '-';
    const head = `${indent}${marker} ${title}`;
    return attrs.fid ? `${head}  ⟨${attrs.fid}⟩` : head;
}

/** Indent description lines by `indent + 4`; keep blank lines truly blank (render.py:59-67). */
function descriptionLines(description: string, indent: string): string[] {
    const out: string[] = [];
    for (const dl of description.split('\n')) {
        out.push(dl.trim().length > 0 ? `${indent}    ${dl}` : '');
    }
    return out;
}

/**
 * Serialize the rich doc to canonical `tree.codoc` text. Walks the flat block
 * sequence, grouping the paragraphs that follow each heading into its description.
 */
export function renderTreeFromDoc(doc: PMNode): string {
    const blocks = doc.content ?? [];
    const lines: string[] = [];

    // Depth can step DOWN any amount but never UP by more than one — matching how
    // parse.py derives depth from the parent chain. Clamping here keeps the
    // text↔tree round-trip idempotent even if an editor edit left a level skip
    // (which would otherwise snap to parent+1 on the next reconcile).
    let prevDepth = -1;
    let i = 0;
    while (i < blocks.length) {
        const b = blocks[i];
        if (b.type !== NODE_FEATURE_HEADING) {
            // A stray paragraph before any heading has no owner — skip (defensive).
            i++;
            continue;
        }
        const attrs = headingAttrs(b);
        const depth = Math.max(0, Math.min(attrs.level, prevDepth + 1));
        prevDepth = depth;
        const indent = '  '.repeat(depth);
        const title = inlineRunsToText(b.content).trim();
        lines.push(titleLine(attrs, title, depth));

        // Gather the description paragraph NODES belonging to this heading, then
        // normalize via blocksToDescriptionText (U7): empty paragraphs are dropped
        // and the rest joined with a single blank line. This is the SAME normalization
        // the settle path stores (so the live serialization == the round-tripped one —
        // no reflow/caret-jump when you leave blank lines between or after features),
        // and it matches parse.py's collapsed description string (TS↔Python parity).
        const descBlocks: PMNode[] = [];
        i++;
        while (i < blocks.length && blocks[i].type !== NODE_FEATURE_HEADING) {
            if (blocks[i].type === NODE_PARAGRAPH) descBlocks.push(blocks[i]);
            i++;
        }
        const description = blocksToDescriptionText(descBlocks);
        if (description) lines.push(...descriptionLines(description, indent));
        lines.push('');
    }

    // `"\n".join(lines).rstrip() + "\n"` — strip the trailing blank(s), one final newline.
    return lines.join('\n').replace(/\s+$/, '') + '\n';
}
