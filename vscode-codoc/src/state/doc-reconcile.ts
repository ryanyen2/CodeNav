/**
 * doc-reconcile.ts — keep the authoritative rich doc (`tree.doc.json`) in sync with
 * the structural truth in `tree.codoc`, WITHOUT losing authorship marks (U4/U6).
 *
 * The structure (which features exist, their order, level, title, retired) always
 * comes fresh from the parsed `tree.codoc`. The rich description blocks (carrying
 * author/bold/etc. marks) are borrowed from the saved doc by fid — but only when
 * the saved blocks still project to the SAME description text. If a loop or an
 * external text edit changed a description underneath, its saved marks are stale,
 * so we fall back to the from-text blocks (marks reset). This is the narrow,
 * honest version of the Phase-3 role-merge: structure wins from text, marks are
 * re-anchored where they still apply.
 */
import {
    PMNode,
    NODE_FEATURE_HEADING,
    NODE_PARAGRAPH,
    FeatureHeadingAttrs,
    blocksToDescriptionText,
    makeDoc,
} from './pm-doc';
import { parseTreeToDoc, RealizedLookup } from './doc-deserialize';

interface HeadingGroup {
    heading: PMNode;
    blocks: PMNode[];
}

/** Split a whole-tree doc into [heading, its-description-paragraphs] groups. */
export function groupByHeading(doc: PMNode): HeadingGroup[] {
    const out: HeadingGroup[] = [];
    let cur: HeadingGroup | null = null;
    for (const b of doc.content ?? []) {
        if (b.type === NODE_FEATURE_HEADING) {
            cur = { heading: b, blocks: [] };
            out.push(cur);
        } else if (cur && b.type === NODE_PARAGRAPH) {
            cur.blocks.push(b);
        }
    }
    return out;
}

function fidOf(heading: PMNode): string | null {
    return (heading.attrs as FeatureHeadingAttrs | undefined)?.fid ?? null;
}

/**
 * Build the doc for the webview: structure from `treeText`, description marks
 * borrowed from `savedDoc` by fid where the description text is unchanged.
 */
export function reconcileDoc(
    treeText: string,
    savedDoc: PMNode | null,
    realized?: RealizedLookup,
): PMNode {
    const fresh = parseTreeToDoc(treeText, realized);
    if (!savedDoc) return fresh;

    const savedByFid = new Map<string, PMNode[]>();
    for (const g of groupByHeading(savedDoc)) {
        const fid = fidOf(g.heading);
        if (fid) savedByFid.set(fid, g.blocks);
    }

    const content: PMNode[] = [];
    for (const g of groupByHeading(fresh)) {
        content.push(g.heading);
        const fid = fidOf(g.heading);
        const saved = fid ? savedByFid.get(fid) : undefined;
        if (saved && blocksToDescriptionText(saved) === blocksToDescriptionText(g.blocks)) {
            content.push(...saved); // text unchanged → keep marks
        } else {
            content.push(...g.blocks); // changed (or new) → from text
        }
    }
    return makeDoc(content);
}

/**
 * Replace one feature's description paragraphs in a whole-tree doc (the persisted
 * effect of a `doc-commit`). Structure and every other feature are untouched.
 */
export function replaceFeatureBlocks(doc: PMNode, fid: string, blocks: PMNode[]): PMNode {
    const content: PMNode[] = [];
    for (const g of groupByHeading(doc)) {
        content.push(g.heading);
        content.push(...(fidOf(g.heading) === fid ? blocks : g.blocks));
    }
    return makeDoc(content);
}
