/**
 * agent-proposals.ts — materialize an agent's code-ahead AMEND proposal as the
 * vendored engine's tracked-change marks (U4).
 *
 * The single tracked-change mechanism in codoc is the vendored engine
 * (track-changes/, KTD1): a change is `insertion`/`deletion` marks in the doc. An
 * agent proposal (Loop A drift/reflection/amend, surfaced via the sidecar) is the
 * AGENT→HUMAN direction; here we project each amend's {old → new} title/description
 * into a feature's content as those marks, AUTHORED by the agent (tinted per role).
 *
 * This runs HOST-SIDE on the payload doc only — `tree.doc.json` keeps the clean
 * human baseline, and the baseline-aware serializer (pm-doc.inlineRunsToText:
 * insertions excluded, deletions kept) renders the marked doc back to the exact
 * pre-proposal `tree.codoc`. So old+new coexist for the human to review without ever
 * leaking the not-yet-accepted text into canonical state. Accept/reject is a verdict
 * (inbox.json → Loop B, authoritative); the marks clear on the re-render.
 *
 * Built from the proposal's {old,new} STRINGS (not by mapping a diff onto live
 * positions), so inline `[label](codoc:…)` refs are handled by construction
 * (textToInlineRuns re-parses them into codeRef nodes carrying the run's mark).
 */
import {
    PMNode, PMMark, NODE_TEXT, NODE_CODE_REF,
    MARK_INSERTION, MARK_DELETION,
    textNode, codeRefNode, paragraphNode, textToInlineRuns,
    type CodeRefAttrs,
} from './pm-doc';
import { wordDiff } from './doc-diff';
import { rebuildFeatures } from './suggestion-model';

/** One agent code-ahead AMEND, flattened for materialization. `changeId` is the
 *  store event id (drives the accept/reject verdict and dedup); `authorId` is the
 *  agent role (`claude-code` | `codex` | … — drives the tint via `data-author-id`
 *  CSS). `*Old` is the CURRENT (baseline) text the diff is taken against. */
export interface AgentAmend {
    featureId: string;
    changeId: string;
    authorId: string;
    titleOld: string;
    titleNew: string;
    descOld: string;
    descNew: string;
}

function mark(type: string, a: AgentAmend): PMMark {
    // authorColor is left blank — the per-role tint rides on `data-author-id` CSS
    // (ins/del[data-author-id="…"] → --author-color), so it stays themeable and
    // never hits the engine's inline-style sanitizer.
    return { type, attrs: { changeId: a.changeId, authorId: a.authorId, authorName: a.authorId, authorColor: '', timestamp: '' } };
}

/** old → new as inline runs with ins/del marks: `same` plain, `del` deletion-marked,
 *  `ins` insertion-marked. Refs in a run become codeRef nodes carrying the same mark. */
function markedRuns(oldStr: string, newStr: string, a: AgentAmend): PMNode[] {
    const out: PMNode[] = [];
    for (const run of wordDiff(oldStr, newStr)) {
        const marks: PMMark[] | undefined =
            run.t === 'del' ? [mark(MARK_DELETION, a)] :
            run.t === 'ins' ? [mark(MARK_INSERTION, a)] : undefined;
        for (const n of textToInlineRuns(run.s)) {
            if (n.type === NODE_TEXT) { if (n.text) out.push(textNode(n.text, marks)); }
            else if (n.type === NODE_CODE_REF && n.attrs) out.push(codeRefNode(n.attrs as unknown as CodeRefAttrs, marks));
        }
    }
    return out;
}

/** Paragraph-aligned marked diff of a description (blank-line-separated paragraphs).
 *  A wholly added/removed paragraph is all-ins / all-del. Always ≥1 block. */
function markedDescBlocks(descOld: string, descNew: string, a: AgentAmend): PMNode[] {
    const olds = descOld ? descOld.split(/\n{2,}/) : [];
    const news = descNew ? descNew.split(/\n{2,}/) : [];
    const n = Math.max(olds.length, news.length, 1);
    const blocks: PMNode[] = [];
    for (let i = 0; i < n; i++) {
        blocks.push(paragraphNode(markedRuns(olds[i] ?? '', news[i] ?? '', a)));
    }
    return blocks;
}

/**
 * Project each agent AMEND onto its feature in `doc` as engine tracked-change marks
 * (title runs + description blocks). Features without a pending amend are untouched.
 * Pure — returns a new doc; the input (the clean baseline) is never mutated. A no-op
 * when `amends` is empty.
 */
export function applyAgentProposals(doc: PMNode, amends: AgentAmend[]): PMNode {
    if (!amends.length) return doc;
    const byFid = new Map(amends.map(a => [a.featureId, a]));
    return rebuildFeatures(doc, (fid, heading, descBlocks) => {
        const a = byFid.get(fid);
        if (!a) return null;
        const titleChanged = a.titleOld !== a.titleNew;
        const descChanged = a.descOld !== a.descNew;
        if (!titleChanged && !descChanged) return null;
        return {
            heading: titleChanged ? { ...heading, content: markedRuns(a.titleOld, a.titleNew, a) } : heading,
            descBlocks: descChanged ? markedDescBlocks(a.descOld, a.descNew, a) : descBlocks,
        };
    });
}

/** Pull the code-ahead AMENDs out of the unified suggestion list, flattened to
 *  `AgentAmend`. (add/move/retire can't be in-prose tracked changes — they keep
 *  their compact widgets.) The signature feeds both the host's doc materialization
 *  and the editor's reload trigger so a resolved amend's marks clear on re-render. */
export function agentAmendsFrom(
    suggestions: { direction: string; kind: string; featureId: string | null; id: string;
                   eventId?: string; originRole: string;
                   titleOld?: string; titleNew?: string; descOld?: string; descNew?: string }[],
): AgentAmend[] {
    const out: AgentAmend[] = [];
    for (const s of suggestions) {
        if (s.direction !== 'code-ahead' || s.kind !== 'amend' || !s.featureId) continue;
        out.push({
            featureId: s.featureId,
            changeId: s.eventId ?? s.id,
            authorId: s.originRole || 'claude-code',
            titleOld: s.titleOld ?? '', titleNew: s.titleNew ?? '',
            descOld: s.descOld ?? '', descNew: s.descNew ?? '',
        });
    }
    return out;
}
