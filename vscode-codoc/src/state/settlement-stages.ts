/**
 * settlement-stages.ts — the payload half of the settlement model.
 *
 * `settlement.claimsFor` needs a feature's text at each earlier stage. Two of those
 * stages are the host's to know (what the daemon last wrote; what a proposal would
 * make of it) and one is the editor's (what is on screen right now), so this builds
 * the host's half into a plain serializable slice that rides the payload, and the
 * webview supplies `live` from the document it is holding.
 *
 * ## Why `planned` is built from the proposal, not from the marked document
 *
 * The obvious route — materialize the plan, then read the text back out — does not
 * work, and the reason is worth stating because it looks like an oversight: the
 * baseline-aware serializer (`inlineRunsToText`) DROPS insertion-marked runs by
 * design, which is exactly what keeps a proposal out of `tree.codoc`. Reading the
 * materialized doc through it returns the text WITHOUT the plan, so `planned` would
 * equal `projected` and the plan channel would report nothing. Building it from the
 * proposal's own strings sidesteps the question and is the more direct statement
 * anyway: the plan's text is what the plan says.
 *
 * ## Display space
 *
 * Both sides of every diff must live in the same coordinate space or the marks land
 * on the wrong characters — the bug class `display-text.ts` exists to close. The host
 * holds markdown-ish strings and the editor holds nodes, so this side runs stored text
 * through `mdDisplayText` (inline `codoc:` citations and hard breaks collapse to one
 * object-replacement char each) to match what `paraDisplayText` yields live.
 *
 * Pure. No vscode, no DOM — the host and the hub both call it.
 */
import { PMNode, NODE_FEATURE_HEADING, NODE_PARAGRAPH, inlineRunsToText, type FeatureHeadingAttrs } from './pm-doc';
import { mdDisplayText } from './display-space';
import { planRuns, type PlanNode } from './plan-materialize';
import type { FeatureText, BlockRuns, Stage } from './settlement';
import type { AutoEdit, HoldDetail } from './bindings-model';

/** What the host knows; the editor adds `live` and `committed`. Plain data — it
 *  crosses the webview boundary as JSON. */
export interface FeatureStages {
    projected: FeatureText;
    planned?: FeatureText;
    /** The last wording the CODE agreed with — see `FeatureLayers.humanBase`. Shipped
     *  for a HELD feature, whose edit the daemon has already applied and projected back:
     *  without it the author's own ink would clear the instant it became true. */
    humanBase?: FeatureText;
    code?: { layerId: string; prev: FeatureText };
    plan?: { layerId: string; stage: 'proposed' | 'accepted'; runs: BlockRuns[] };
}

/** One proposal, as much of it as the stages need. Mirrors `Suggestion` without
 *  depending on it, so the hub can build these from its own payload shape. */
export interface StagedProposal {
    kind: 'amend' | 'add' | 'retire' | 'move';
    /** The key the claims will be filed under: the feature id for an amend/retire,
     *  the proposal's own event id for an add (its materialized node carries no fid,
     *  and `proposed` is what identifies it — see plan-materialize). */
    key: string;
    layerId: string;
    stage: Stage & ('proposed' | 'accepted');
    titleOld: string;
    titleNew: string;
    descOld: string;
    descNew: string;
}

/** The text a set of block runs adds up to — what the materialized document holds. */
function concatRuns(blocks: readonly BlockRuns[]): FeatureText {
    const out: FeatureText = { title: '', paras: [] };
    for (const b of blocks) {
        const text = b.runs.map(r => r.s).join('');
        if (b.block.kind === 'title') out.title = text;
        else out.paras[b.block.index] = text;
    }
    for (let i = 0; i < out.paras.length; i++) out.paras[i] ??= '';
    return out;
}

/** Split a stored description the way the renderer round-trips it, in display space. */
function textOf(title: string, description: string): FeatureText {
    return {
        title: mdDisplayText(title),
        paras: (description ? description.split(/\n{2,}/) : []).map(mdDisplayText),
    };
}

/**
 * A doc's features as text, keyed the way the decoration layer keys them.
 *
 * `proposed` sits between `fid` and `localId` in the key order because a materialized
 * plan node has neither of the others — its proposal id IS its identity for as long as
 * it exists, and without this rung its claims would be filed under a key nothing on
 * screen matches.
 */
export function featureTextsFrom(doc: PMNode): Map<string, FeatureText> {
    const out = new Map<string, FeatureText>();
    let cur: FeatureText | null = null;
    for (const b of doc.content ?? []) {
        if (b.type === NODE_FEATURE_HEADING) {
            const a = (b.attrs ?? {}) as Partial<FeatureHeadingAttrs>;
            const key = a.fid ?? a.proposed ?? a.localId ?? null;
            cur = key ? { title: mdDisplayText(inlineRunsToText(b.content).trim()), paras: [] } : null;
            if (cur && key) out.set(key, cur);
        } else if (cur && b.type === NODE_PARAGRAPH) {
            cur.paras.push(mdDisplayText(inlineRunsToText(b.content)));
        }
    }
    return out;
}

/**
 * Build the per-feature stages for a payload.
 *
 * `doc` is the CLEAN projection — before any plan is materialized into it — because
 * that is what `projected` means: what the daemon last wrote and every channel is
 * measured against.
 *
 * A feature appears in the result only if it has something unsettled, so a settled
 * document produces an empty map and the decoration layer does no work at all. That
 * is the common case and it is what keeps this affordable on a large tree.
 */
export function buildStages(
    doc: PMNode,
    proposals: readonly StagedProposal[],
    autoEdits: Readonly<Record<string, AutoEdit>>,
    holdDetail: Readonly<Record<string, HoldDetail>> = {},
): Record<string, FeatureStages> {
    const texts = featureTextsFrom(doc);
    const out: Record<string, FeatureStages> = {};

    const at = (key: string): FeatureStages =>
        (out[key] ??= { projected: texts.get(key) ?? { title: '', paras: [] } });

    for (const [fid, e] of Object.entries(autoEdits)) {
        if (!texts.has(fid)) continue;
        // The loop only ever rewrites a DESCRIPTION on its own authority (render's
        // triage), so the displaced title is the current one — saying otherwise would
        // draw a title diff nobody made.
        at(fid).code = { layerId: `auto:${e.at}`, prev: textOf(texts.get(fid)!.title, e.prev) };
    }

    for (const [fid, d] of Object.entries(holdDetail)) {
        if (!d.baseline || !texts.has(fid)) continue;
        // The queued directive's pre-edit description. The TITLE is the current one:
        // the hold carries only the description, and inventing a title diff from its
        // absence would mark a rename nobody made.
        at(fid).humanBase = textOf(texts.get(fid)!.title, d.baseline);
    }

    for (const p of proposals) {
        if (p.kind === 'move') continue;   // a move changes rank, not text — no claim
        const s = at(p.key);
        const runs = planRuns(p.kind, mdDisplayText(p.titleOld), mdDisplayText(p.titleNew),
            mdDisplayText(p.descOld), mdDisplayText(p.descNew));
        // `planned` is the CONCATENATION of those runs, not the proposal's new text —
        // and the difference is the whole reason this is computed here rather than
        // guessed. The tracked-change engine materializes an amend as old AND new
        // together, so what the document actually holds is the two interleaved. Taking
        // `planned` to be the new text alone would make the human diff read every
        // displaced sentence still on screen as something the author had just typed.
        s.planned = concatRuns(runs);
        s.plan = { layerId: p.layerId, stage: p.stage, runs };
    }
    return out;
}

/** The plan nodes a payload must materialize into its doc — the structural half of
 *  the same proposal list `buildStages` reads, so the two can never disagree about
 *  which proposals are in play. */
export function planNodesFrom(
    proposals: readonly StagedProposal[],
    anchors: (key: string) => { parentId?: string | null; afterId?: string | null; beforeId?: string | null; featureId?: string | null; authorId?: string },
): PlanNode[] {
    const out: PlanNode[] = [];
    for (const p of proposals) {
        if (p.kind !== 'add' && p.kind !== 'retire') continue;
        const a = anchors(p.key);
        out.push({
            kind: p.kind, changeId: p.layerId, authorId: a.authorId || 'claude-code',
            parentId: a.parentId ?? null, afterId: a.afterId ?? null, beforeId: a.beforeId ?? null,
            featureId: a.featureId ?? null,
            title: p.kind === 'add' ? p.titleNew : p.titleOld,
            description: p.kind === 'add' ? p.descNew : p.descOld,
        });
    }
    return out;
}

/**
 * The pending suggestions, as the settlement model wants them.
 *
 * The `Suggestion` list is the surface's own vocabulary (directions, tags, verdict
 * state); this narrows it to the four strings a claim is computed from, and decides the
 * one thing the two models disagree about — the KEY.
 *
 * An amend or a retire is filed under the feature it targets, because that is the node
 * the claim lands on. An add has no feature yet, so it is filed under the proposal's own
 * event id — the same value that becomes the materialized node's `proposed` attr, which
 * is what lets the decoration layer match the claim to the node on screen.
 *
 * A proposal whose verdict is already recorded but not yet drained is EXCLUDED. The
 * reader has answered; showing them the question again for as long as the round trip
 * takes is how a surface teaches people that their clicks do not register.
 */
export function stagedProposals(
    suggestions: readonly {
        kind: string; id: string; featureId: string | null; eventId?: string;
        titleOld?: string; titleNew?: string; descOld?: string; descNew?: string;
        verdictPending?: boolean; accepted?: boolean;
    }[],
): StagedProposal[] {
    const out: StagedProposal[] = [];
    for (const s of suggestions) {
        if (s.verdictPending) continue;
        if (s.kind !== 'amend' && s.kind !== 'add' && s.kind !== 'retire') continue;
        const key = s.kind === 'add' ? (s.eventId ?? s.id) : s.featureId;
        if (!key) continue;
        out.push({
            kind: s.kind, key, layerId: s.eventId ?? s.id,
            // Accepted-but-unbuilt is the second plan stage; the surface reads it as
            // "agreed, still not real", which is a different thing to be told than
            // "somebody is waiting on you".
            stage: s.accepted ? 'accepted' : 'proposed',
            titleOld: s.titleOld ?? '', titleNew: s.titleNew ?? '',
            descOld: s.descOld ?? '', descNew: s.descNew ?? '',
        });
    }
    return out;
}
