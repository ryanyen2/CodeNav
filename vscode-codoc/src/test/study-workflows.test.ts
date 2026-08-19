/**
 * study-workflows.test.ts — what a participant can actually DO in the 20 live
 * minutes, and what the surface owes them for each of it.
 *
 * The settlement redesign is unit-tested per module. This file is the other test:
 * it walks the SEQUENCES a session produces — edit a proposal, edit while the agent
 * is working, answer nothing, accept something you reshaped — because those are
 * where modules that are individually right combine into something wrong, and a
 * study session is exactly one long uncontrolled combination of them.
 *
 * It exists because that is not hypothetical. The gate matched slices by `fid`, a
 * materialized proposal has none, so every payload placed the projection's copy in
 * position AND appended the local copy at the end: a duplicate per payload,
 * compounding, because the next payload's `local` already held both. No unit test
 * saw it — `plan-materialize` and `doc-gate` are each correct alone. A participant
 * would have watched a proposed feature multiply down the page.
 *
 * The cases below are named for what the PERSON does, not for what the code does.
 */
import { describe, it, expect } from 'vitest';
import { gateProjection } from '../webview/doc-gate';
import { materializePlan, type PlanNode } from '../state/plan-materialize';
import { buildStages, stagedProposals, featureTextsFrom, type StagedProposal } from '../state/settlement-stages';
import { claimsFor, type FeatureText } from '../state/settlement';
import { nodeStatus, statusGlyphs } from '../state/node-status';
import { featureUnits, commandsForSettle } from '../state/commands-from-doc';
import { renderTreeFromDoc } from '../state/doc-serialize';
import { nodeEditsFor } from '../webview/tiptap/suggestion-decorations';
import {
    makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns,
    NODE_FEATURE_HEADING, type PMNode, type FeatureHeadingAttrs,
} from '../state/pm-doc';
import { codocSchema } from '../webview/tiptap/schema';
import type { Suggestion } from '../state/suggestion-model';
import type { AutoEdit } from '../state/bindings-model';

// ── the workspace a participant meets ────────────────────────────────────────

const head = (fid: string | null, title: string, level = 0, extra: Partial<FeatureHeadingAttrs> = {}): PMNode =>
    featureHeadingNode({ fid, level, retired: false, realized: true, ...extra }, [textNode(title)]);
const para = (text: string, owner: string | null = null): PMNode =>
    paragraphNode(textToInlineRuns(text), owner);

/** The tree as the replayed agent session leaves it. */
const tree = (): PMNode => makeDoc([
    head('f-1', 'Recording a sale'), para('Each sale is written once, in order.', 'f-1'),
    head('f-2', 'Rounding'), para('Totals round once, at the summary.', 'f-2'),
]);

const planAdd: PlanNode = {
    kind: 'add', changeId: 'e-add', authorId: 'claude-code', beforeId: 'f-2',
    title: 'Duplicate detection', description: 'Repeated sales are flagged, not dropped.',
};

const titlesOf = (doc: PMNode): string[] =>
    (doc.content ?? []).filter(b => b.type === NODE_FEATURE_HEADING)
        .map(b => (b.content ?? []).map(n => n.text ?? '').join(''));

const attrsOf = (b: PMNode) => (b.attrs ?? {}) as Partial<FeatureHeadingAttrs>;

/** Re-run the gate as if the daemon had reposted the same payload. */
const repost = (incoming: PMNode, local: PMNode, pending: Set<string> = new Set()): PMNode =>
    gateProjection({ incoming, local, localVersions: new Map(), pendingFids: pending }).doc;

/** Type into the paragraph of the node identified by `key` (fid or proposal id). */
function typeInto(doc: PMNode, key: string, extra: string): PMNode {
    const out: PMNode[] = [];
    let inNode = false;
    for (const b of doc.content ?? []) {
        if (b.type === NODE_FEATURE_HEADING) {
            const a = attrsOf(b);
            inNode = (a.fid ?? a.proposed) === key;
            out.push(b);
            continue;
        }
        if (inNode && b.type === 'paragraph') {
            const text = (b.content ?? []).map(n => n.text ?? '').join('');
            out.push(paragraphNode(textToInlineRuns(text + extra), (b.attrs as { ownerId?: string })?.ownerId ?? null));
            inNode = false;
            continue;
        }
        out.push(b);
    }
    return { ...doc, content: out };
}

// ── the participant meets a proposal ─────────────────────────────────────────

describe('a proposal is in the document, and stays exactly once', () => {
    it('does not multiply down the page as payloads arrive', () => {
        const withPlan = materializePlan(tree(), [planAdd]);
        let local = withPlan;
        // The daemon reposts on every Loop pass; a study session sees many.
        for (let i = 0; i < 5; i++) local = repost(withPlan, local);
        expect(titlesOf(local)).toEqual(['Recording a sale', 'Duplicate detection', 'Rounding']);
    });

    it('sits at the rank it would take, so the tree reads as it would with it in', () => {
        expect(titlesOf(materializePlan(tree(), [planAdd])))
            .toEqual(['Recording a sale', 'Duplicate detection', 'Rounding']);
    });

    it('answering nothing costs nothing — no command, and nothing in tree.codoc', () => {
        const clean = tree();
        const withPlan = materializePlan(clean, [planAdd]);
        expect(commandsForSettle(featureUnits(clean), featureUnits(withPlan), 't')).toEqual([]);
        expect(renderTreeFromDoc(withPlan)).toBe(renderTreeFromDoc(clean));
    });
});

// ── the participant EDITS a proposal ─────────────────────────────────────────

describe('the participant edits a planned node before deciding', () => {
    const withPlan = () => materializePlan(tree(), [planAdd]);

    it('their words survive the next payload', () => {
        // The regression this pins: without the proposal id in the gate's identity and
        // in the pending set, the next projection re-materializes the agent's wording
        // straight over the participant's, with nothing to say it happened.
        const edited = typeInto(withPlan(), 'e-add', ' We should count near-duplicates too.');
        const after = repost(withPlan(), edited, new Set(['e-add']));
        const text = (after.content ?? []).map(b => (b.content ?? []).map(n => n.text ?? '').join('')).join(' ');
        expect(text).toContain('near-duplicates');
    });

    it('…and are adopted away only when they have NOT touched it', () => {
        const after = repost(withPlan(), withPlan(), new Set());
        expect(titlesOf(after)).toEqual(['Recording a sale', 'Duplicate detection', 'Rounding']);
    });

    it('still emits no command — editing a proposal is not authoring it', () => {
        const clean = tree();
        const edited = typeInto(withPlan(), 'e-add', ' And near-duplicates.');
        expect(commandsForSettle(featureUnits(clean), featureUnits(edited), 't')).toEqual([]);
        expect(renderTreeFromDoc(edited)).toBe(renderTreeFromDoc(clean));
    });

    it('what they typed rides the Accept', () => {
        const edited = typeInto(withPlan(), 'e-add', ' And near-duplicates.');
        const blocks = edited.content ?? [];
        const at = blocks.findIndex(b => b.type === NODE_FEATURE_HEADING && attrsOf(b).proposed === 'e-add');
        const schema = codocSchema();
        const loc = {
            heading: schema.nodeFromJSON(blocks[at]),
            body: [{ node: schema.nodeFromJSON(blocks[at + 1]) }],
        };
        const s = { id: 'e-add', titleNew: planAdd.title, descNew: planAdd.description } as Suggestion;
        expect(nodeEditsFor(loc, s)?.description).toContain('near-duplicates');
    });

    it('their typing reads as THEIRS — blue over the grey, not more grey', () => {
        const clean = tree();
        const proposals: StagedProposal[] = [{
            kind: 'add', key: 'e-add', layerId: 'e-add', stage: 'proposed',
            titleOld: '', titleNew: planAdd.title, descOld: '', descNew: planAdd.description,
        }];
        const stages = buildStages(clean, proposals, {});
        const planned = stages['e-add'].planned!;
        const live: FeatureText = {
            title: planned.title,
            paras: [planned.paras[0] + ' And near-duplicates.'],
        };
        const claims = claimsFor({ ...stages['e-add'], live });
        const mine = claims.find(c => c.channel === 'human' && c.edit === 'add')!;
        expect(live.paras[0].slice(mine.start, mine.end)).toContain('near-duplicates');
        const theirs = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(live.paras[0].slice(theirs.start, theirs.end)).not.toContain('near-duplicates');
    });

    it('rejecting it takes their words with it — no orphan left behind', () => {
        // A proposal the projection has dropped is let go pending edit or not: keeping
        // one the reader had typed into would leave their words on a node that has no
        // remaining way to be accepted.
        const edited = typeInto(materializePlan(tree(), [planAdd]), 'e-add', ' And near-duplicates.');
        const after = repost(tree(), edited, new Set(['e-add']));
        expect(titlesOf(after)).toEqual(['Recording a sale', 'Rounding']);
    });
});

// ── the participant edits while the agent is working ─────────────────────────

describe('the participant edits while the code is being implemented', () => {
    it('their sentence stays theirs while the loop rewrites a neighbour', () => {
        const projected: FeatureText = {
            title: 'Rounding',
            paras: ['Totals round once, at the summary.', 'Line items are not rounded.'],
        };
        // The loop rewrote paragraph 1 from the code; the participant is typing in 0.
        const live: FeatureText = {
            title: 'Rounding',
            paras: ['Totals round once, at the summary. I think this is the bug.', 'Line items are not rounded.'],
        };
        const claims = claimsFor({
            code: { layerId: 'auto:1', prev: { title: 'Rounding', paras: [projected.paras[0], 'Line items round too.'] } },
            projected, live,
        });
        const mine = claims.find(c => c.channel === 'human' && c.edit === 'add')!;
        expect((mine.block as { index: number }).index).toBe(0);
        expect(live.paras[0].slice(mine.start, mine.end)).toContain('this is the bug');
        // …and the loop's rewrite is reported on paragraph 1, in its own channel.
        const theirs = claims.find(c => c.channel === 'code' && c.edit === 'add')!;
        expect((theirs.block as { index: number }).index).toBe(1);
    });

    it('a sentence they rewrote themselves stops being reported as the codebase\'s', () => {
        // Otherwise the surface hands the participant their own sentence back with a
        // green ground, claiming the code said it — the exact confusion a pilot hit.
        const live: FeatureText = { title: 'Rounding', paras: ['I think rounding happens twice.'] };
        const claims = claimsFor({
            code: { layerId: 'auto:1', prev: { title: 'Rounding', paras: ['Totals round once.'] } },
            projected: { title: 'Rounding', paras: ['Totals round once, at the summary.'] },
            live,
        });
        expect(claims.some(c => c.channel === 'code' && c.edit === 'add')).toBe(false);
        expect(claims.some(c => c.channel === 'human')).toBe(true);
    });

    it('their committed edit stays marked through the daemon\'s echo', () => {
        // ⌘S → the daemon applies and reprojects → `projected` equals what they typed.
        // Without a separate base the ink clears exactly when it becomes true.
        const typed: FeatureText = { title: 'Rounding', paras: ['Totals round once, at the summary.'] };
        const claims = claimsFor({
            projected: typed, live: typed, committed: true,
            humanBase: { title: 'Rounding', paras: ['Totals round twice.'] },
        });
        expect(claims.some(c => c.channel === 'human' && c.stage === 'committed')).toBe(true);
    });
});

// ── what the margin says while all this is happening ─────────────────────────

describe('the marker keeps up with a session', () => {
    const settled: FeatureText = { title: 'Rounding', paras: ['Totals round once.'] };

    it('says nothing about a feature nobody has touched', () => {
        expect(statusGlyphs(nodeStatus(claimsFor({ projected: settled, live: settled }), [], 0)))
            .toEqual([]);
    });

    it('shows unsent, then sent, as the participant works', () => {
        const live: FeatureText = { title: 'Rounding', paras: ['Totals round once, at the summary.'] };
        const open = nodeStatus(claimsFor({ projected: settled, live }), [], 0);
        expect(open.human).toBe('open');
        const sent = nodeStatus(claimsFor({ projected: settled, live, committed: true }), [], 0);
        expect(sent.human).toBe('committed');
    });

    it('shows planned AND drifted at once — the case a ranked badge could not say', () => {
        const s = nodeStatus([], [{ channel: 'plan', at: 0, diverged: 'both' }], 0);
        expect(s.plan).toBe('fulfilled');
        expect(s.diff).toBe('both');
        expect(statusGlyphs(s).map(g => g.slot)).toEqual(['plan', 'diff']);
    });
});

// ── the loop keeps running under all of it ───────────────────────────────────

describe('the daemon keeps writing while the participant reads', () => {
    it('an unrelated feature advancing does not disturb the one they are editing', () => {
        const before = tree();
        const advanced = makeDoc([
            head('f-1', 'Recording a sale'), para('Each sale is written once, in order.', 'f-1'),
            head('f-2', 'Rounding'), para('Totals round once, at the summary, and once per line.', 'f-2'),
        ]);
        const edited = typeInto(before, 'f-1', ' I do not believe this.');
        const after = repost(advanced, edited, new Set(['f-1']));
        const text = (after.content ?? []).map(b => (b.content ?? []).map(n => n.text ?? '').join('')).join(' | ');
        expect(text).toContain('I do not believe this');   // theirs kept
        expect(text).toContain('once per line');           // the daemon's landed
    });

    it('a proposal arriving mid-edit does not displace what they typed', () => {
        const edited = typeInto(tree(), 'f-1', ' I do not believe this.');
        const after = repost(materializePlan(tree(), [planAdd]), edited, new Set(['f-1']));
        const text = (after.content ?? []).map(b => (b.content ?? []).map(n => n.text ?? '').join('')).join(' | ');
        expect(text).toContain('I do not believe this');
        expect(titlesOf(after)).toContain('Duplicate detection');
    });
});

// ── the loop's own rewrites, which nobody asked for ──────────────────────────

describe('the loop rewrites a description while the participant is elsewhere', () => {
    const autoEdit = (prev: string): Record<string, AutoEdit> =>
        ({ 'f-2': { at: 'h-1', prev, written_by: 'human', rationale: 'the code moved' } });

    it('is reported as the code channel, with the words it displaced', () => {
        const stages = buildStages(tree(), [], autoEdit('Totals round twice.'));
        const live = featureTextsFrom(tree()).get('f-2')!;
        const claims = claimsFor({ ...stages['f-2'], live });
        expect(claims.some(c => c.channel === 'code' && c.removed?.includes('twice'))).toBe(true);
        expect(claims.some(c => c.channel === 'code' && c.edit === 'add')).toBe(true);
    });

    it('composes with a proposal on the SAME feature — three channels, no stand-down', () => {
        const proposals = stagedProposals([{
            kind: 'amend', id: 'e-9', eventId: 'e-9', featureId: 'f-2',
            titleOld: 'Rounding', titleNew: 'Rounding',
            descOld: 'Totals round once, at the summary.',
            descNew: 'Totals round once, at the summary. Line items are never rounded.',
        } as Suggestion]);
        const stages = buildStages(tree(), proposals, autoEdit('Totals round twice.'));
        const planned = stages['f-2'].planned!;
        const live: FeatureText = { title: planned.title, paras: [planned.paras[0] + ' Is that right?'] };
        const claims = claimsFor({ ...stages['f-2'], live });
        expect(new Set(claims.map(c => c.channel))).toEqual(new Set(['code', 'plan', 'human']));
    });
});
