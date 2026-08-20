/**
 * plan-materialize.ts — a proposal is written INTO the document, where it will land.
 *
 * ## Why it moved out of the margin
 *
 * A proposed ADD used to be drawn as a widget: a dimmed block pinned near the parent,
 * outside the document, holding the title and description the agent suggested. It was
 * honest about being a proposal and dishonest about everything else — it did not sit
 * at the rank it would take, it did not participate in the surrounding prose, and the
 * reader could not tell how the tree would READ with it in. Which is the only question
 * a verdict actually asks. Accepting one meant imagining the result and then finding
 * out.
 *
 * So a plan is materialized: the node goes where it will go, the wording goes where it
 * will go, and it is distinguished by how it LOOKS — faded, in the plan channel's
 * opacity axis (settlement.ts) — rather than by living somewhere else. Amends already
 * worked this way; this brings adds and retires into the same treatment, so "planned"
 * is one idea in the surface instead of two that happen to share a verdict button.
 *
 * ## The safety rule that makes it possible
 *
 * The instant an agent's words are in the document, every path that projects the
 * document back to authored state is a path that can author them as the human's. That
 * is not a hypothetical: `commands-from-doc` sees a heading with a localId and no fid
 * and emits `add`, and the settle after that would write the machine's proposal into
 * the store under the reader's name, with no verdict and nothing in the ledger to say
 * where it came from.
 *
 * The `proposed` attr is the guard, and it is deliberately the same device as
 * `retired`: a flag on a node that is genuinely in the document. Three call sites
 * honour it — `featureUnits` (no commands), `renderTreeFromDoc` (not exported to
 * `tree.codoc`), and the baseline-aware `inlineRunsToText` (insertion-marked runs are
 * already excluded). A fourth would be a bug, so the flag is checked at the boundary
 * rather than at each reader.
 *
 * ## Where a plan node may be inserted
 *
 * Only at a heading boundary, never between a feature's heading and its prose. Prose
 * routes to its feature by `ownerId` where one is stamped and by POSITION where one is
 * not, so a node dropped mid-description would capture the paragraphs below it — and
 * since a planned node's contents are discarded, those paragraphs would vanish from
 * the real feature's `set_description`. Silent prose loss, from a rendering decision.
 * `insertAt` enforces the boundary; the plan node's own paragraphs carry an explicit
 * `ownerId` so they never rely on position at all.
 *
 * Pure — returns a new doc; the clean baseline it is given is never mutated.
 */
import {
    PMNode, PMMark, NODE_TEXT, NODE_CODE_REF, NODE_FEATURE_HEADING,
    MARK_INSERTION, MARK_DELETION,
    textNode, codeRefNode, paragraphNode, featureHeadingNode, textToInlineRuns,
    type CodeRefAttrs, type FeatureHeadingAttrs,
} from './pm-doc';
import { wordDiff, sentenceDiff, type DiffRun } from './doc-diff';
import type { BlockRuns } from './settlement';

/** One proposal, flattened for materialization. `changeId` is the store event id: it
 *  drives the verdict, the `proposed` attr, and the settlement layer's `layerId`. */
export interface PlanNode {
    kind: 'add' | 'retire';
    changeId: string;
    authorId: string;
    /** add: where it lands. `parentId` "" / null = top level. */
    parentId?: string | null;
    afterId?: string | null;
    beforeId?: string | null;
    /** retire: the feature being struck. */
    featureId?: string | null;
    title: string;
    description: string;
}

function mark(type: string, changeId: string, authorId: string): PMMark {
    return { type, attrs: { changeId, authorId, authorName: authorId, authorColor: '', timestamp: '' } };
}

/** Inline runs for a whole string under one engine mark, re-parsing `codoc:` refs so a
 *  planned description cites code the same way a real one does. */
function markedAll(text: string, m: PMMark): PMNode[] {
    const out: PMNode[] = [];
    for (const n of textToInlineRuns(text)) {
        const all = [...(n.marks ?? []), m];
        if (n.type === NODE_TEXT) { if (n.text) out.push(textNode(n.text, all)); }
        else if (n.type === NODE_CODE_REF && n.attrs) out.push(codeRefNode(n.attrs as unknown as CodeRefAttrs, all));
    }
    return out;
}

/** The heading's identity: level comes from the destination, id stays null (the store
 *  has not minted one and must not appear to have). `localId` is deliberately absent —
 *  a planned node is not a client-authored node and giving it one would put it in front
 *  of every gesture that keys on local identity. */
function plannedHeading(p: PlanNode, level: number): PMNode {
    const attrs: FeatureHeadingAttrs = {
        fid: null, level, retired: false, realized: false, localId: null, proposed: p.changeId,
    };
    return featureHeadingNode(attrs, markedAll(p.title, mark(MARK_INSERTION, p.changeId, p.authorId)));
}

/** A planned node's description blocks, each owned by the proposal so prose routing
 *  never has to fall back to position. */
function plannedParas(p: PlanNode): PMNode[] {
    const m = mark(MARK_INSERTION, p.changeId, p.authorId);
    return descParas(p.description).map(t => paragraphNode(markedAll(t, m), p.changeId));
}

/**
 * The block index a planned ADD is inserted at.
 *
 * Resolution mirrors what `apply` will actually do on accept (`store.rank_between`), so
 * the node the reader judged is the node they get: after `afterId` if it is present,
 * before `beforeId` if it is, else at the end of the parent's subtree. Every candidate
 * is snapped to a heading boundary — the index of a heading, or the end of the doc —
 * because landing inside a description silently steals its paragraphs (see the header).
 */
export function insertAt(
    blocks: readonly PMNode[], parentId: string | null,
    afterId?: string | null, beforeId?: string | null,
): number {
    const headIndex = (id: string): number => blocks.findIndex(
        b => b.type === NODE_FEATURE_HEADING && idOf(b) === id);

    /** The index of the next heading at or after `i` — i.e. the end of whatever
     *  feature `i` is inside. The document end when there is none. */
    const boundaryAfter = (i: number): number => {
        for (let j = i; j < blocks.length; j++) if (blocks[j].type === NODE_FEATURE_HEADING) return j;
        return blocks.length;
    };

    if (afterId) {
        const i = headIndex(afterId);
        // After a node means after its whole SUBTREE — a sibling anchor names the node,
        // not the gap immediately below its title line.
        if (i >= 0) return subtreeEnd(blocks, i);
    }
    if (beforeId) {
        const i = headIndex(beforeId);
        if (i >= 0) return i;
    }
    if (parentId) {
        const i = headIndex(parentId);
        if (i >= 0) return subtreeEnd(blocks, i);
    }
    return boundaryAfter(blocks.length);
}

/** The block index just past a heading's whole subtree (its prose and every deeper
 *  heading under it) — where a new last child belongs. */
export function subtreeEnd(blocks: readonly PMNode[], headIndex: number): number {
    const level = levelOf(blocks[headIndex]);
    for (let j = headIndex + 1; j < blocks.length; j++) {
        const b = blocks[j];
        if (b.type === NODE_FEATURE_HEADING && levelOf(b) <= level) return j;
    }
    return blocks.length;
}

function idOf(b: PMNode): string | null {
    const a = b.attrs as Partial<FeatureHeadingAttrs> | undefined;
    return a?.fid ?? a?.localId ?? null;
}

function levelOf(b: PMNode): number {
    const a = b.attrs as Partial<FeatureHeadingAttrs> | undefined;
    return typeof a?.level === 'number' ? a.level : 0;
}

/**
 * Materialize planned ADDs and RETIREs into a doc.
 *
 * An ADD becomes a real heading + paragraphs, insertion-marked and flagged `proposed`.
 * A RETIRE marks the existing node's own text for deletion instead of inserting
 * anything — the proposal is that these words should GO, so the words are what is
 * shown, struck where they stand. Neither touches a feature the plan does not name.
 */
export function materializePlan(doc: PMNode, plans: readonly PlanNode[]): PMNode {
    if (!plans.length) return doc;
    let blocks = [...(doc.content ?? [])];

    // Retires first: they only re-mark existing nodes, so doing them before any
    // insertion keeps the ADD anchor indices computed against the same block list the
    // caller reasoned about.
    const retires = new Map(plans.filter(p => p.kind === 'retire' && p.featureId)
        .map(p => [p.featureId as string, p]));
    if (retires.size) {
        const out: PMNode[] = [];
        let striking: PlanNode | null = null;
        for (const b of blocks) {
            if (b.type === NODE_FEATURE_HEADING) {
                const id = idOf(b);
                striking = (id && retires.get(id)) || null;
                if (striking) {
                    const m = mark(MARK_DELETION, striking.changeId, striking.authorId);
                    out.push({ ...b, attrs: { ...(b.attrs ?? {}) }, content: reMark(b.content, m) });
                    continue;
                }
            } else if (striking) {
                const m = mark(MARK_DELETION, striking.changeId, striking.authorId);
                out.push({ ...b, content: reMark(b.content, m) });
                continue;
            }
            out.push(b);
        }
        blocks = out;
    }

    // Adds, back to front, so an earlier insertion cannot shift a later anchor.
    const adds = plans.filter(p => p.kind === 'add');
    const placed = adds
        .map(p => ({ p, at: insertAt(blocks, p.parentId ?? null, p.afterId, p.beforeId) }))
        .sort((a, b) => b.at - a.at);
    for (const { p, at } of placed) {
        const level = parentLevel(blocks, p.parentId ?? null) + 1;
        blocks.splice(at, 0, plannedHeading(p, level), ...plannedParas(p));
    }
    return { ...doc, content: blocks };
}

/** Re-mark a block's inline content, keeping each run's own marks under the new one. */
function reMark(content: PMNode[] | undefined, m: PMMark): PMNode[] {
    return (content ?? []).map(n => ({ ...n, marks: [...(n.marks ?? []), m] }));
}

function parentLevel(blocks: readonly PMNode[], parentId: string | null): number {
    if (!parentId) return -1;   // a root plan node lands at level 0
    const i = blocks.findIndex(b => b.type === NODE_FEATURE_HEADING && idOf(b) === parentId);
    return i >= 0 ? levelOf(blocks[i]) : -1;
}

/**
 * Split a stored description into the paragraph blocks the editor holds it as.
 *
 * The ONE place that answers "where do this description's blocks divide", so a plan's
 * runs and the materialized document can never disagree about it. `plannedParas` above
 * splits the same way, and `settlement-stages.textOf` does too.
 *
 * It takes RAW stored text, deliberately: a paragraph break is `\n\n` in the store and
 * the display-space projection collapses EVERY newline to one atom char, so splitting
 * after that projection can never find a break. That is not hypothetical — it is the
 * bug this function exists to make unrepeatable: a two-paragraph amend produced ONE
 * planned block against the editor's two, every later paragraph came back unpaired,
 * and the whole of it was reported as text the author had just typed. Split first,
 * project each piece second.
 */
export function descParas(description: string): string[] {
    return description ? description.split(/\n{2,}/) : [];
}

/**
 * The per-block diff runs a materialized plan contributes, for `settlement.claimsFor`.
 *
 * A planned ADD is wholly new, so every block is one insertion run — which is what
 * makes the whole node read as plan ink without a special case anywhere downstream. An
 * AMEND is the real diff of its two texts, at the granularity each block is judged in
 * (a title by word, prose by sentence — a word diff of a rewritten claim shreds both
 * versions into fragments the reader has to reassemble before they can agree).
 *
 * Descriptions arrive ALREADY SPLIT into paragraphs and already in display space (see
 * `descParas`); this function never splits, because the coordinate space it is handed
 * cannot be split correctly.
 */
export function planRuns(
    kind: 'add' | 'amend' | 'retire',
    titleOld: string, titleNew: string,
    descOld: readonly string[], descNew: readonly string[],
): BlockRuns[] {
    const whole = (s: string, t: DiffRun['t']): DiffRun[] => (s ? [{ t, s }] : []);

    if (kind === 'add') {
        return [
            { block: { kind: 'title' }, runs: whole(titleNew, 'ins') },
            ...descNew.map((t, i) => ({ block: { kind: 'para' as const, index: i }, runs: whole(t, 'ins') })),
        ];
    }
    if (kind === 'retire') {
        return [
            { block: { kind: 'title' }, runs: whole(titleOld, 'del') },
            ...descOld.map((t, i) => ({ block: { kind: 'para' as const, index: i }, runs: whole(t, 'del') })),
        ];
    }
    const n = Math.max(descOld.length, descNew.length);
    return [
        { block: { kind: 'title' }, runs: wordDiff(titleOld, titleNew) },
        ...Array.from({ length: n }, (_u, i) => ({
            block: { kind: 'para' as const, index: i },
            runs: sentenceDiff(descOld[i] ?? '', descNew[i] ?? ''),
        })),
    ];
}
