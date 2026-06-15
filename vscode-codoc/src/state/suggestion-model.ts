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
 *
 * Storage is block-level ({old,new} per field); the editor renders a word-level
 * inline diff (see doc-diff.ts). One Suggestion can carry a title change and/or a
 * description change, plus the structural kinds add/move/retire.
 */
import type { SidecarData } from './bindings-model';
import type { CommentThread } from './comment-model';
import {
    PMNode,
    NODE_FEATURE_HEADING,
    NODE_PARAGRAPH,
    FeatureHeadingAttrs,
    inlineRunsToText,
    blocksToDescriptionText,
} from './pm-doc';

export type SuggestionDirection = 'code-ahead' | 'doc-ahead';
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
    /** Human-readable origin label ("code drift" | "agent plan" | "you"). */
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
 * Derive code-ahead suggestions from the sidecar proposals. `currentTitle` /
 * `currentDescription` provide the settled (old) text for amend diffs (the sidecar
 * proposal carries only the proposed new values).
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
            out.push({ id: p.event_id, eventId: p.event_id, direction: 'code-ahead', kind: 'retire', featureId: fid, originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined });
        } else if (p.op === 'amend') {
            out.push({
                id: p.event_id, eventId: p.event_id, direction: 'code-ahead', kind: 'amend', featureId: fid,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
                titleOld: currentTitle(fid), titleNew: p.title ?? currentTitle(fid),
                descOld: currentDescription(fid), descNew: p.description ?? currentDescription(fid),
            });
        }
    }
    for (const [eventId, p] of Object.entries(props.by_event ?? {})) {
        if (p.op === 'add') {
            out.push({
                id: eventId, eventId, direction: 'code-ahead', kind: 'add', featureId: null, parentId: p.parent_id ?? null,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
                titleNew: p.title ?? '', descNew: p.description ?? '',
            });
        } else if (p.op === 'move') {
            out.push({
                id: eventId, eventId, direction: 'code-ahead', kind: 'move', featureId: p.feature_id ?? null, parentId: p.parent_id ?? null,
                originRole: p.actor || roleFromTag(p.tag), tag: p.tag, causedBy: p.caused_by || undefined,
            });
        }
    }
    return out;
}

/** Map a proposal tag to an authorship role for tinting (best effort). */
function roleFromTag(tag: string | undefined): string {
    if (!tag) return 'claude-code';
    if (tag.includes('plan')) return 'claude-code';
    if (tag.includes('reflection')) return 'claude-code';
    return 'claude-code'; // "code drift" etc. — agent-originated
}

/** The full pending-suggestion list: code-ahead (from sidecar) + doc-ahead (persisted). */
export function buildSuggestions(
    sidecar: SidecarData,
    docAhead: Suggestion[],
    currentTitle: (fid: string) => string,
    currentDescription: (fid: string) => string,
): Suggestion[] {
    return [...codeAheadSuggestions(sidecar, currentTitle, currentDescription), ...docAhead];
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

/** Extract {title, description} per fid from a whole-tree doc. */
function indexByFid(doc: PMNode): Map<string, { title: string; desc: string }> {
    const out = new Map<string, { title: string; desc: string }>();
    let cur: { fid: string; title: string; descs: PMNode[] } | null = null;
    const flush = (): void => {
        if (cur) out.set(cur.fid, { title: cur.title, desc: blocksToDescriptionText(cur.descs) });
    };
    for (const b of doc.content ?? []) {
        if (b.type === NODE_FEATURE_HEADING) {
            flush();
            const fid = (b.attrs as FeatureHeadingAttrs | undefined)?.fid ?? null;
            cur = fid ? { fid, title: inlineRunsToText(b.content).trim(), descs: [] } : null;
        } else if (cur && b.type === NODE_PARAGRAPH) {
            cur.descs.push(b);
        }
    }
    flush();
    return out;
}

/**
 * Diff an edited whole-tree doc against the settled baseline into DOC-AHEAD
 * suggestions — one per feature whose title and/or description changed. This is
 * the Suggesting-mode capture: the human's edits become tracked diffs awaiting the
 * agent, instead of settling. New headings (no fid) are handled by the structural
 * settle path, not here.
 */
export function diffDocsToSuggestions(baseline: PMNode, edited: PMNode, originRole = 'human'): Suggestion[] {
    const base = indexByFid(baseline);
    const cur = indexByFid(edited);
    const out: Suggestion[] = [];
    for (const [fid, c] of cur) {
        const b = base.get(fid);
        if (!b) continue;
        if (b.title !== c.title || b.desc !== c.desc) {
            // Stable id per feature so a re-capture (and a Withdraw) targets the same
            // card; the host merges title/description changes for the same feature.
            out.push({
                id: `d-${fid}`, direction: 'doc-ahead', kind: 'amend', featureId: fid,
                originRole, tag: 'you', titleOld: b.title, titleNew: c.title, descOld: b.desc, descNew: c.desc,
            });
        }
    }
    return out;
}
