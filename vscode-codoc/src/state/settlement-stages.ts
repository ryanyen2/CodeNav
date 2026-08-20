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
import { consequenceOf } from './grammar';
import { planRuns, descParas, type PlanNode } from './plan-materialize';
import type { FeatureText, BlockRuns } from './settlement';
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
    plan?: { layerId: string; stage: 'proposed'; runs: BlockRuns[] };
    /** A pending REFLECTION — Loop A saw the code change and offers the tree new
     *  wording. Structurally identical to `plan` (materialized old-and-new runs
     *  awaiting a verdict) and drawn in a different channel, because the words are the
     *  codebase's and not a plan's. Same shape so `claimsFor` can walk them together. */
    reflected?: { layerId: string; stage: 'proposed'; runs: BlockRuns[] };
    /** An ACCEPTED plan the code has not caught up with — see `FeatureLayers.accepted`.
     *  Shipped from the queued directive, because the proposal row it came from is
     *  deleted the moment the verdict applies. */
    accepted?: { layerId: string; prev: FeatureText };
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
    titleOld: string;
    titleNew: string;
    descOld: string;
    descNew: string;
    /** True when accepting this proposal is a BUILD request — the prose describes code
     *  that does not exist yet (`realized === false` upstream, surfaced as
     *  `writesCode === 'build'`). It decides which CHANNEL draws the proposal, and it
     *  is the same bit `grammar.consequenceOf` reads for the button verb, so the words
     *  and the colour can never tell different stories.
     *
     *  False means a REFLECTION: Loop A watched the code change and offers the tree new
     *  wording to match. Those were drawn in the plan channel with everything else, so a
     *  node nobody had planned came up gray-and-struck-through — the plan's ink on the
     *  codebase's claim. The palette says green/red is always what the code did; a
     *  reflection is exactly that, still awaiting a verdict. */
    builds: boolean;
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

/** Split a stored description the way the renderer round-trips it, in display space.
 *
 *  Split FIRST, project each paragraph second — never the other way round. Display
 *  space collapses every newline to one atom char, so a `\n\n` split taken after the
 *  projection can never match. See `plan-materialize.descParas`. */
function textOf(title: string, description: string): FeatureText {
    return { title: mdDisplayText(title), paras: descParas(description).map(mdDisplayText) };
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
        if (!texts.has(fid)) continue;
        // A plan ADD replaced NOTHING, so its empty baseline is a real value rather
        // than a missing one: the whole node is the plan's words. Reading it as "no
        // baseline, nothing to draw" left an accepted placeholder rendering as ordinary
        // settled prose the moment it was minted — the one node on the page where every
        // word is unbuilt. Only `add_node` gets this reading; for any other kind an
        // empty baseline genuinely means the daemon did not record one.
        const wholeNode = d.origin === 'plan' && d.kind === 'add_node';
        if (!d.baseline && !wholeNode) continue;
        // The queued directive's pre-edit description. The TITLE is the current one:
        // the hold carries only the description, and inventing a title diff from its
        // absence would mark a rename nobody made — except for a whole new node, where
        // the title is as new as the prose and has no earlier form to hold on to.
        const prev = wholeNode && !d.baseline
            ? { title: '', paras: [] }
            : textOf(texts.get(fid)!.title, d.baseline ?? '');
        // WHOSE words the queue is holding decides which channel draws them, and there
        // is nothing else left to decide it from: both are "applied to the store, not
        // yet in the code", and the proposal row that would have said "this came from
        // an agent" is deleted by the accept itself. `origin` is the daemon answering
        // the question directly (codoc/loop/edits.Directive.origin).
        //
        // An older daemon ships no `origin`. Reading that as the author's own edit is
        // the safe default: it keeps the pre-existing behaviour exactly, and the failure
        // it risks (an accepted plan drawn in the author's ink) is the one the surface
        // already had, rather than a new one where the author's words go gray.
        if (d.origin === 'plan') at(fid).accepted = { layerId: `hold:${fid}`, prev };
        else at(fid).humanBase = prev;
    }

    for (const p of proposals) {
        if (p.kind === 'move') continue;   // a move changes rank, not text — no claim
        const s = at(p.key);
        // Paragraphs are split from the RAW text and each piece projected into display
        // space — the order matters and getting it backwards is the bug that made a
        // multi-paragraph amend read as the author's own typing (see `descParas`).
        const runs = planRuns(p.kind, mdDisplayText(p.titleOld), mdDisplayText(p.titleNew),
            descParas(p.descOld).map(mdDisplayText), descParas(p.descNew).map(mdDisplayText));
        // `planned` is the CONCATENATION of those runs, not the proposal's new text —
        // and the difference is the whole reason this is computed here rather than
        // guessed. The tracked-change engine materializes an amend as old AND new
        // together, so what the document actually holds is the two interleaved. Taking
        // `planned` to be the new text alone would make the human diff read every
        // displaced sentence still on screen as something the author had just typed.
        s.planned = concatRuns(runs);
        // WHICH channel draws it follows what accepting would do, not the mere fact of
        // being pending. A build request is the plan's words (gray); a reflection is the
        // codebase's (green/red ground), awaiting a verdict rather than landed.
        if (p.builds) s.plan = { layerId: p.layerId, stage: 'proposed', runs };
        else s.reflected = { layerId: p.layerId, stage: 'proposed', runs };
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
 *
 * Every proposal here is `proposed`, and that is not a simplification: a proposal only
 * exists while it is unanswered. The plan channel's OTHER stage — accepted, and waiting
 * on the code — cannot be read from this list at all, because accepting deletes the row
 * it would have been read from. It comes from the queued directive instead (see the
 * `holdDetail` branch of `buildStages`). A `stage` field here used to be filled from an
 * `accepted` flag on the suggestion that nothing ever set, so the second stage was
 * unreachable and the plan's gray vanished at the click.
 */
export function stagedProposals(
    suggestions: readonly {
        kind: string; id: string; featureId: string | null; eventId?: string;
        titleOld?: string; titleNew?: string; descOld?: string; descNew?: string;
        verdictPending?: boolean; writesCode?: 'build' | 'remove' | null; tag?: string;
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
            titleOld: s.titleOld ?? '', titleNew: s.titleNew ?? '',
            descOld: s.descOld ?? '', descNew: s.descNew ?? '',
            // Via `consequenceOf` rather than a bare `writesCode === 'build'` so the
            // tag fallback for an older daemon's payload is applied in ONE place.
            builds: consequenceOf(s.writesCode, s.tag) === 'build',
        });
    }
    return out;
}
