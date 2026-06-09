/**
 * doc-deserialize.ts — rebuild the rich doc (`tree.doc.json`) from canonical
 * `tree.codoc` text.
 *
 * Used for the store→doc rebuild: when Loop A changes the store and the daemon
 * re-renders `tree.codoc`, the host reconstructs a fresh doc from the text. This
 * is the structural skeleton; per-span authorship marks are re-anchored on top of
 * it by fid + offset (U6) so reflected agent text keeps its pencil provenance.
 *
 * It reuses `parseTreeCodoc` (the TS port of parse.py, parity-tested) so it sees
 * exactly the features the Python pipeline sees — pending ADD/MOVE ghosts are
 * skipped (they're overlays), and everything past the legacy pending sentinel is
 * ignored. Depth is recovered from the parent chain and becomes the heading
 * `level`, so `renderTreeFromDoc(parseTreeToDoc(text)) === canonical(text)`.
 *
 * NOTE on paragraph splitting: descriptions are split into paragraphs on blank
 * lines (`/\n{2,}/`), matching `doc-layout.weaveBlocks` — the production renderer's
 * contract. A single newline inside a paragraph (a soft wrap) is preserved inside
 * the paragraph's text and round-trips through `descriptionLines`. Runs of 3+
 * newlines collapse to one blank line on the next render, exactly as weaveBlocks
 * already treats them in the existing doc view.
 */
import { parseTreeCodoc, ParsedFeature } from './tree-model';
import {
    PMNode,
    featureHeadingNode,
    paragraphNode,
    makeDoc,
    textToInlineRuns,
} from './pm-doc';

/** Realized lookup keyed by feature id (from the sidecar `features` map); default true. */
export type RealizedLookup = (fid: string) => boolean;

function computeDepths(features: ParsedFeature[]): Map<ParsedFeature, number> {
    const byId = new Map<string, ParsedFeature>();
    for (const f of features) if (f.id) byId.set(f.id, f);

    const depthOf = new Map<ParsedFeature, number>();
    const resolve = (f: ParsedFeature, seen: Set<ParsedFeature>): number => {
        const cached = depthOf.get(f);
        if (cached !== undefined) return cached;
        if (f.parent_id == null || seen.has(f)) {
            depthOf.set(f, 0);
            return 0;
        }
        const parent = byId.get(f.parent_id);
        if (!parent) {
            depthOf.set(f, 0);
            return 0;
        }
        seen.add(f);
        const d = resolve(parent, seen) + 1;
        depthOf.set(f, d);
        return d;
    };
    for (const f of features) resolve(f, new Set());
    return depthOf;
}

/**
 * Rebuild the rich doc from `tree.codoc` text. Authored marks are not present in
 * the text, so every span comes back unmarked; callers re-anchor authorship.
 */
export function parseTreeToDoc(treeText: string, realized?: RealizedLookup): PMNode {
    const { features } = parseTreeCodoc(treeText);
    const depths = computeDepths(features);

    const content: PMNode[] = [];
    for (const f of features) {
        const level = depths.get(f) ?? 0;
        const isRealized = f.id && realized ? realized(f.id) : true;
        content.push(
            featureHeadingNode(
                { fid: f.id, level, retired: f.retired, realized: isRealized },
                textToInlineRuns(f.title),
            ),
        );
        if (f.description) {
            for (const para of f.description.split(/\n{2,}/)) {
                content.push(paragraphNode(textToInlineRuns(para)));
            }
        }
    }
    return makeDoc(content);
}
