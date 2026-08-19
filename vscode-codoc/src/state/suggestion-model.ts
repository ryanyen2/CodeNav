/**
 * suggestion-model.ts — the unified diff/suggestion model (pure, testable).
 *
 * A *suggestion* is a persistent annotation that the doc and the code disagree at
 * a feature, resolved by whichever side is behind (see
 * docs/codoc-collaborative-editing-model.md):
 *   • code-ahead — an agent reflected a code change or proposed a plan; the HUMAN
 *     resolves via inbox.json. These are derived from the store proposals already
 *     surfaced in the sidecar — never persisted here.
 *   • doc-ahead  — a human changed intent the code must follow; the AGENT resolves
 *     by implementing it (realize.md), then it clears. These are persisted in the
 *     tree.doc.json wrapper because they are doc-side state with no store event yet.
 *   • yours      — the reader's OWN edit, deferred because a peer wrote the same lines
 *     (loop_b._resolve_content). Also a store proposal, also resolved by the human —
 *     it is a separate direction because it is the one that is not the machine's, and
 *     folding it into code-ahead is what made the surface hand people their own
 *     sentences back labelled "from code".
 *
 * Storage is block-level ({old,new} per field); the editor renders a word-level
 * inline diff (see doc-diff.ts). One Suggestion can carry a title change and/or a
 * description change, plus the structural kinds add/move/retire.
 */
import type { SidecarData } from './bindings-model';
import type { CommentThread } from './comment-model';
import type { Direction } from './grammar';
import {
    PMNode,
    NODE_FEATURE_HEADING,
    FeatureHeadingAttrs,
} from './pm-doc';

export type SuggestionDirection = Direction;
export type SuggestionKind = 'amend' | 'add' | 'move' | 'retire';

export interface Suggestion {
    id: string;
    direction: SuggestionDirection;
    kind: SuggestionKind;
    /** Target existing feature (amend/move/retire); null for an add. */
    featureId: string | null;
    /** Destination parent for add/move ("" / null = root). */
    parentId?: string | null;
    /** Who proposed it: human | claude-code | codex | …. */
    originRole: string;
    /** Human-readable origin label ("code drift" | "agent reflection" | "agent plan" |
     *  "your edit", the deferred-own-edit tag). */
    tag?: string;
    /** Store event id — present for code-ahead (drives inbox.json verdicts). */
    eventId?: string;
    /** Causality (v4 ledger): the directive (d-…) this change implements. A
     *  directive only ever exists because a doc edit queued it, so a non-empty
     *  value means "this surfaced back from your edit" — the cascade cue. */
    causedBy?: string;
    titleOld?: string;
    titleNew?: string;
    descOld?: string;
    descNew?: string;
    /** What accepting does to the CODE — see grammar.consequenceOf. Absent on a
     *  payload from a daemon older than the field; the tag is the fallback. */
    writesCode?: 'build' | 'remove' | null;
    /** A verdict is already recorded for this proposal and has not drained yet, so
     *  the surface must say "waiting" rather than offer the click again. */
    verdictPending?: boolean;
    /** Sibling anchors for add/move — apply honours them on accept
     *  (store.rank_between), so the ghost must be drawn where the node will
     *  actually land instead of defaulting to "last child" and jumping on accept. */
    afterId?: string | null;
    beforeId?: string | null;
}

/** The tree.doc.json wrapper: settled doc + persisted doc-ahead suggestions +
 *  inline comment threads (span-anchored steering notes; see comment-model.ts). */
export interface DocFile {
    version: number;
    doc: PMNode;
    suggestions: Suggestion[];
    comments: CommentThread[];
}

export const DOC_FILE_VERSION = 1;

export function emptyDocFile(doc: PMNode): DocFile {
    return { version: DOC_FILE_VERSION, doc, suggestions: [], comments: [] };
}

/** Accept either a wrapper {version,doc,suggestions,comments} or a bare ProseMirror
 *  doc (forward-compat with the U4 format) and normalize to a DocFile. */
export function parseDocFile(json: unknown): DocFile | null {
    if (!json || typeof json !== 'object') return null;
    const o = json as Record<string, unknown>;
    if (o.type === 'doc') return emptyDocFile(o as unknown as PMNode);
    if (o.doc && (o.doc as PMNode).type === 'doc') {
        return {
            version: typeof o.version === 'number' ? o.version : DOC_FILE_VERSION,
            doc: o.doc as PMNode,
            suggestions: Array.isArray(o.suggestions) ? (o.suggestions as Suggestion[]) : [],
            comments: Array.isArray(o.comments) ? (o.comments as CommentThread[]) : [],
        };
    }
    return null;
}

/**
 * The direction a sidecar proposal reads in, from the ledger's own `actor` field.
 *
 * Almost every pending proposal is the machine's: Loop A reflected a code change, or an
 * agent proposed a plan. ONE is not — `loop_b._resolve_content` defers a contended edit
 * by parking the author's own text as a proposal (actor stays `human`) — and stamping
 * that `code-ahead` with everything else is what made the strip say "from code" over the
 * reader's own words. `actor` (v4 ledger) is the field that already knows; a payload
 * from a daemon that predates it carries no actor and falls through to `code-ahead`,
 * which is what those rows meant.
 */
function directionFromActor(actor: string | undefined): SuggestionDirection {
    return actor === 'human' ? 'yours' : 'code-ahead';
}

/**
 * Derive the pending suggestions from the sidecar proposals — every one of them a
 * change the HUMAN resolves with a verdict, whether the machine proposed it
 * (`code-ahead`) or it is the reader's own deferred edit (`yours`; see
 * `directionFromActor`). `currentTitle` / `currentDescription` provide the settled (old)
 * text for amend diffs (the sidecar proposal carries only the proposed new values).
 */
export function codeAheadSuggestions(
    sidecar: SidecarData,
    currentTitle: (fid: string) => string,
    currentDescription: (fid: string) => string,
): Suggestion[] {
    const out: Suggestion[] = [];
    const props = sidecar.proposals;
    if (!props) return out;

    for (const [fid, p] of Object.entries(props.by_feature ?? {})) {
        if (p.op === 'retire') {
            out.push({ id: p.event_id, eventId: p.event_id, direction: directionFromActor(p.actor), kind: 'retire', featureId: fid, originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined, writesCode: p.writes_code ?? null, verdictPending: !!p.verdict_pending });
        } else if (p.op === 'amend') {
            out.push({
                id: p.event_id, eventId: p.event_id, direction: directionFromActor(p.actor), kind: 'amend', featureId: fid,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
                titleOld: currentTitle(fid), titleNew: p.title ?? currentTitle(fid),
                descOld: currentDescription(fid), descNew: p.description ?? currentDescription(fid),
                writesCode: p.writes_code ?? null, verdictPending: !!p.verdict_pending,
            });
        }
    }
    for (const [eventId, p] of Object.entries(props.by_event ?? {})) {
        if (p.op === 'add') {
            out.push({
                id: eventId, eventId, direction: directionFromActor(p.actor), kind: 'add', featureId: null, parentId: p.parent_id ?? null,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
                titleNew: p.title ?? '', descNew: p.description ?? '',
                writesCode: p.writes_code ?? null, verdictPending: !!p.verdict_pending,
                afterId: p.after_id ?? null, beforeId: p.before_id ?? null,
            });
        } else if (p.op === 'move') {
            out.push({
                id: eventId, eventId, direction: directionFromActor(p.actor), kind: 'move', featureId: p.feature_id ?? null, parentId: p.parent_id ?? null,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
                writesCode: p.writes_code ?? null, verdictPending: !!p.verdict_pending,
                afterId: p.after_id ?? null, beforeId: p.before_id ?? null,
            });
        }
    }
    return out;
}

/** Insert a ghost row's id into a sibling list at its proposal's anchored slot.
 *  apply honours after_id/before_id on accept (rank_between), so drawing the
 *  ghost anywhere else means the accepted node "jumps" to a different position
 *  than the placeholder the user judged. Unresolvable anchors fall back to
 *  append — the same resolution apply itself uses for a vanished sibling. */
export function insertAtAnchor(list: string[], id: string,
                               afterId?: string | null, beforeId?: string | null): void {
    if (afterId) {
        const i = list.indexOf(afterId);
        if (i >= 0) { list.splice(i + 1, 0, id); return; }
    }
    if (beforeId) {
        const i = list.indexOf(beforeId);
        if (i >= 0) { list.splice(i, 0, id); return; }
    }
    list.push(id);
}

/** Map a proposal tag to an authorship role for tinting (best effort) — the fallback
 *  for a sidecar written before `actor` existed. "your edit" is the deferred-own-edit
 *  tag (render._source_tag); everything else is machine-originated. */
function roleFromTag(tag: string | undefined): string {
    if (!tag) return 'claude-code';
    if (tag.includes('your')) return 'human';
    return 'claude-code'; // "code drift" / "agent plan" / "agent reflection"
}

/** The full pending-suggestion list. Since U3/U2b the human commits directly (no
 *  doc-ahead suggestions), so this is just the agent's code-ahead proposals derived
 *  from the sidecar. (The signature is kept for the host call site.) */
export function buildSuggestions(
    sidecar: SidecarData,
    currentTitle: (fid: string) => string,
    currentDescription: (fid: string) => string,
): Suggestion[] {
    return codeAheadSuggestions(sidecar, currentTitle, currentDescription);
}

/** Suggestions targeting a given feature (amend/move/retire). */
export function suggestionsForFeature(suggestions: Suggestion[], fid: string): Suggestion[] {
    return suggestions.filter(s => s.featureId === fid);
}

/** Add-ghost suggestions landing under a given parent ("" = root). */
export function addsUnderParent(suggestions: Suggestion[], parentId: string | null): Suggestion[] {
    const key = parentId ?? null;
    return suggestions.filter(s => s.kind === 'add' && (s.parentId ?? null) === key);
}

/** Rebuild a whole-tree doc, letting `replace(fid, heading, descBlocks)` swap a
 *  feature's heading + description blocks (return null to keep it unchanged). The
 *  shared spine for the agent-proposal mark materializer (agent-proposals.ts) —
 *  pure, no editor state. */
export function rebuildFeatures(
    doc: PMNode,
    replace: (fid: string, heading: PMNode, descBlocks: PMNode[]) => { heading: PMNode; descBlocks: PMNode[] } | null,
): PMNode {
    const blocks = doc.content ?? [];
    const out: PMNode[] = [];
    let i = 0;
    while (i < blocks.length) {
        const b = blocks[i];
        if (b.type !== NODE_FEATURE_HEADING) { out.push(b); i++; continue; }
        const fid = (b.attrs as FeatureHeadingAttrs | undefined)?.fid ?? null;
        const desc: PMNode[] = [];
        let j = i + 1;
        while (j < blocks.length && blocks[j].type !== NODE_FEATURE_HEADING) { desc.push(blocks[j]); j++; }
        const rep = fid ? replace(fid, b, desc) : null;
        if (rep) out.push(rep.heading, ...rep.descBlocks);
        else out.push(b, ...desc);
        i = j;
    }
    return { ...doc, content: out };
}
