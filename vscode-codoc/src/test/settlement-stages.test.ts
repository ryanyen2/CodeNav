import { describe, it, expect } from 'vitest';
import { buildStages, featureTextsFrom, planNodesFrom, type StagedProposal } from '../state/settlement-stages';
import { claimsFor } from '../state/settlement';
import { materializePlan } from '../state/plan-materialize';
import { applyAgentProposals } from '../state/agent-proposals';
import { featureTextsFrom as textsOf } from '../state/settlement-stages';
import {
    makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns,
    type PMNode, type FeatureHeadingAttrs,
} from '../state/pm-doc';
import type { AutoEdit } from '../state/bindings-model';

const head = (fid: string | null, title: string, level = 0, extra: Partial<FeatureHeadingAttrs> = {}): PMNode =>
    featureHeadingNode({ fid, level, retired: false, realized: true, ...extra }, [textNode(title)]);
const para = (text: string, owner: string | null = null): PMNode =>
    paragraphNode(textToInlineRuns(text), owner);

const doc = (): PMNode => makeDoc([
    head('f-1', 'Uploads'), para('It retries five times.', 'f-1'),
    head('f-2', 'Downloads'), para('How downloading works.', 'f-2'),
]);

const amend = (over: Partial<StagedProposal> = {}): StagedProposal => ({
    kind: 'amend', key: 'f-1', layerId: 'e-9',
    titleOld: 'Uploads', titleNew: 'Uploads',
    descOld: 'It retries five times.', descNew: 'It retries five times. It then backs off.',
    ...over,
});

const autoEdit = (prev: string): Record<string, AutoEdit> => ({
    'f-1': { at: 'h-1', prev, written_by: 'human', rationale: '' },
});

describe('featureTextsFrom', () => {
    it('reads each feature\'s title and paragraphs', () => {
        const texts = featureTextsFrom(doc());
        expect(texts.get('f-1')).toEqual({ title: 'Uploads', paras: ['It retries five times.'] });
    });

    it('keys a materialized plan node by its proposal id, since it has no other identity', () => {
        const withPlan = materializePlan(doc(), [{
            kind: 'add', changeId: 'e-add', authorId: 'claude-code',
            beforeId: 'f-2', title: 'Backoff', description: 'It waits longer.',
        }]);
        expect([...featureTextsFrom(withPlan).keys()]).toContain('e-add');
    });

    it('collapses a code citation to one char, so offsets address the live document', () => {
        const cited = makeDoc([head('f-1', 'Uploads'), para('See [it](codoc:a.py#f) here.', 'f-1')]);
        const t = featureTextsFrom(cited).get('f-1')!;
        // One object-replacement char stands for the whole citation.
        expect(t.paras[0]).toBe('See ￼ here.');
    });
});

describe('buildStages — only what is unsettled', () => {
    it('is empty for a settled document, so the decoration layer does no work', () => {
        expect(buildStages(doc(), [], {})).toEqual({});
    });

    it('carries the loop\'s displaced wording as the code channel\'s base', () => {
        const stages = buildStages(doc(), [], autoEdit('It retries twice.'));
        expect(stages['f-1'].code!.prev.paras[0]).toBe('It retries twice.');
        // …and the title it displaced is the CURRENT one: the loop only ever rewrites a
        // description on its own authority, so a title diff here would be invented.
        expect(stages['f-1'].code!.prev.title).toBe('Uploads');
    });

    it('carries a proposal\'s own wording as the planned reading', () => {
        const stages = buildStages(doc(), [amend()], {});
        expect(stages['f-1'].planned!.paras[0]).toContain('backs off');
        expect(stages['f-1'].plan!.stage).toBe('proposed');
    });

    it('reads a proposed retire as the node AS IT STANDS — the claim is the strike', () => {
        const stages = buildStages(doc(), [amend({
            kind: 'retire', titleNew: '', descNew: '',
        })], {});
        expect(stages['f-1'].planned!.paras[0]).toBe('It retries five times.');
        expect(stages['f-1'].plan!.runs.every(r => r.runs.every(x => x.t === 'del'))).toBe(true);
    });

    it('ignores a move — it changes rank, not text, so it makes no claim on any span', () => {
        expect(buildStages(doc(), [amend({ kind: 'move' })], {})).toEqual({});
    });
});

describe('the stages compose into claims end to end', () => {
    it('reports all three channels on one feature the whole pipeline produced', () => {
        const clean = doc();
        const proposals = [amend()];
        const stages = buildStages(clean, proposals, autoEdit('It retries twice.'));

        // The document as the reader sees it: the plan materialized in, then typing.
        const planned = textsOf(clean).get('f-1')!;
        const live = {
            title: planned.title,
            paras: [stages['f-1'].planned!.paras[0] + ' Measure it.'],
        };

        const claims = claimsFor({ ...stages['f-1'], live });
        expect(new Set(claims.map(c => c.channel))).toEqual(new Set(['code', 'plan', 'human']));

        const text = live.paras[0];
        const plan = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(text.slice(plan.start, plan.end)).toContain('backs off');
        const human = claims.find(c => c.channel === 'human' && c.edit === 'add')!;
        expect(text.slice(human.start, human.end)).toContain('Measure it');
        expect(claims.some(c => c.channel === 'code' && c.removed?.includes('twice'))).toBe(true);
    });
});

describe('planNodesFrom — the structural half of the same list', () => {
    it('turns adds and retires into plan nodes and leaves amends alone', () => {
        const nodes = planNodesFrom(
            [amend(), amend({ kind: 'add', key: 'e-add', layerId: 'e-add', titleNew: 'Backoff' }),
             amend({ kind: 'retire', key: 'f-2', layerId: 'e-ret' })],
            key => (key === 'e-add' ? { beforeId: 'f-2' } : { featureId: 'f-2' }),
        );
        expect(nodes.map(n => n.kind)).toEqual(['add', 'retire']);
        expect(nodes[0].beforeId).toBe('f-2');
        expect(nodes[1].featureId).toBe('f-2');
    });

    it('takes an add\'s wording from the proposal and a retire\'s from what stands', () => {
        const nodes = planNodesFrom(
            [amend({ kind: 'add', key: 'e-a', layerId: 'e-a', titleNew: 'New', descNew: 'New prose.' }),
             amend({ kind: 'retire', key: 'f-1', layerId: 'e-r', titleOld: 'Old', descOld: 'Old prose.' })],
            () => ({}),
        );
        expect(nodes[0].title).toBe('New');
        expect(nodes[1].title).toBe('Old');
    });
});

describe('planned text is what the document actually holds', () => {
    it('interleaves the displaced sentence with its replacement, the way the engine does', () => {
        // The engine materializes an amend as old AND new together so the reader can
        // compare. If `planned` were the new text alone, the human diff would read the
        // struck sentence still on screen as something the author had just typed.
        const stages = buildStages(doc(), [amend({
            descOld: 'It retries twice.', descNew: 'It retries five times.',
        })], {});
        const planned = stages['f-1'].planned!.paras[0];
        expect(planned).toContain('twice');
        expect(planned).toContain('five times');
    });

    it('marks the displaced sentence as a CUT, not as a deletion point', () => {
        // It is still on screen, so it is struck where it stands; a `del` point would
        // print it a second time beside the copy already there.
        const clean = doc();
        const stages = buildStages(clean, [amend({
            descOld: 'It retries five times.', descNew: 'It backs off instead.',
        })], {});
        const planned = stages['f-1'].planned!;
        const claims = claimsFor({ ...stages['f-1'], live: planned });
        const cut = claims.find(c => c.channel === 'plan' && c.edit === 'cut')!;
        expect(planned.paras[0].slice(cut.start, cut.end)).toContain('five times');
        const added = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(planned.paras[0].slice(added.start, added.end)).toContain('backs off');
        expect(claims.some(c => c.channel === 'plan' && c.edit === 'del')).toBe(false);
    });

    it('does not report the struck sentence as the author\'s own typing', () => {
        const clean = doc();
        const stages = buildStages(clean, [amend({
            descOld: 'It retries five times.', descNew: 'It backs off instead.',
        })], {});
        const claims = claimsFor({ ...stages['f-1'], live: stages['f-1'].planned! });
        expect(claims.some(c => c.channel === 'human')).toBe(false);
    });

    it('strikes the whole node for a proposed retire, without duplicating its words', () => {
        const stages = buildStages(doc(), [amend({
            kind: 'retire', titleNew: '', descNew: '',
        })], {});
        const claims = claimsFor({ ...stages['f-1'], live: stages['f-1'].planned! });
        expect(claims.every(c => c.channel !== 'plan' || c.edit === 'cut')).toBe(true);
        const cut = claims.find(c => c.channel === 'plan' && c.block.kind === 'para')!;
        expect(stages['f-1'].planned!.paras[0].slice(cut.start, cut.end))
            .toBe('It retries five times.');
    });
});

// ─── the two ways a plan used to read as the author's own typing ─────────────

/** What the EDITOR holds after the host materializes a proposal: every run, including
 *  the insertion-marked ones the baseline-aware serializer drops. `featureTextsFrom`
 *  reads the BASELINE (that is what keeps a proposal out of `tree.codoc`), so it is the
 *  wrong lens for "what is on screen". */
function liveTextsOf(doc: PMNode): Map<string, { title: string; paras: string[] }> {
    const out = new Map<string, { title: string; paras: string[] }>();
    let cur: { title: string; paras: string[] } | null = null;
    const flat = (ns: PMNode[] | undefined): string =>
        (ns ?? []).map(n => (n.type === 'text' ? (n.text ?? '') : '\u{FFFC}')).join('');
    for (const b of doc.content ?? []) {
        if (b.type === 'featureHeading') {
            const fid = (b.attrs as { fid?: string | null })?.fid ?? null;
            cur = fid ? { title: flat(b.content), paras: [] } : null;
            if (cur && fid) out.set(fid, cur);
        } else if (cur) cur.paras.push(flat(b.content));
    }
    return out;
}

describe('a plan amend is the PLAN\'s words, on every paragraph', () => {
    const P1 = 'It retries five times.';
    const P2 = 'Failures are logged.';
    const NEW1 = 'It retries five times. It then backs off.';

    it('claims every paragraph of a multi-paragraph amend, and inks none of it blue', () => {
        // The bug: `buildStages` handed `planRuns` a DISPLAY-space description, where
        // every newline is already one atom char, and `planRuns` then split it on
        // `\n\n`. The split never matched, so a two-paragraph amend produced ONE planned
        // block against the editor's two — paragraph 2 came back unpaired, diffed against
        // '', and was reported as a sentence the author had just typed. In blue, with a
        // ⌘S prompt on prose nobody had touched.
        const doc = makeDoc([head('f-1', 'Uploads'), para(P1, 'f-1'), para(P2, 'f-1')]);
        const p: StagedProposal = {
            kind: 'amend', key: 'f-1', layerId: 'e-9',
            titleOld: 'Uploads', titleNew: 'Uploads',
            descOld: `${P1}\n\n${P2}`, descNew: `${NEW1}\n\n${P2}`,
        };
        const stages = buildStages(doc, [p], {});
        expect(stages['f-1'].planned!.paras).toHaveLength(2);

        const live = applyAgentProposals(doc, [{
            featureId: 'f-1', changeId: 'e-9', authorId: 'claude-code',
            titleOld: 'Uploads', titleNew: 'Uploads',
            descOld: `${P1}\n\n${P2}`, descNew: `${NEW1}\n\n${P2}`,
        }]);
        const claims = claimsFor({ ...stages['f-1'], live: liveTextsOf(live).get('f-1')! });
        expect(claims.filter(c => c.channel === 'human')).toEqual([]);
        expect(claims.some(c => c.channel === 'plan')).toBe(true);
    });

    it('keeps the plan gray on a feature that is ALSO holding an edit of the author\'s', () => {
        // `humanBase` (the queued directive's pre-edit wording) knows nothing about a
        // proposal materialized after it, so diffing it straight to the live document
        // swallowed every word the plan had put there.
        const doc = makeDoc([head('f-1', 'Uploads'), para(P1, 'f-1')]);
        const p: StagedProposal = {
            kind: 'amend', key: 'f-1', layerId: 'e-9',
            titleOld: 'Uploads', titleNew: 'Uploads', descOld: P1, descNew: NEW1,
        };
        const stages = buildStages(doc, [p], {},
            { 'f-1': { kind: 'amend', intent: 'x', baseline: 'It retries twice.' } });
        const live = applyAgentProposals(doc, [{
            featureId: 'f-1', changeId: 'e-9', authorId: 'claude-code',
            titleOld: 'Uploads', titleNew: 'Uploads', descOld: P1, descNew: NEW1,
        }]);
        const text = liveTextsOf(live).get('f-1')!;
        const claims = claimsFor({ ...stages['f-1'], live: text, committed: true });
        const inked = claims
            .filter(c => c.channel === 'human' && c.edit !== 'del')
            .map(c => text.paras[0].slice(c.start, c.end)).join('|');
        expect(inked).not.toContain('backs off');
        expect(inked).toContain('five times');     // what they actually wrote is theirs
    });
});

describe('an accepted plan survives the verdict that applied it', () => {
    it('routes a plan-origin hold to the plan channel, not the author\'s', () => {
        const doc = makeDoc([head('f-1', 'Uploads'), para('It retries five times and backs off.', 'f-1')]);
        const stages = buildStages(doc, [], {}, {
            'f-1': { kind: 'amend', intent: 'add backoff', origin: 'plan',
                     baseline: 'It retries five times.' },
        });
        expect(stages['f-1'].humanBase).toBeUndefined();
        expect(stages['f-1'].accepted).toBeDefined();
        const claims = claimsFor({ ...stages['f-1'], live: stages['f-1'].projected });
        expect(claims.every(c => c.channel === 'plan' && c.stage === 'accepted')).toBe(true);
    });

    it('reads a daemon that predates `origin` as the author\'s own edit', () => {
        // Back-compat, and it keeps the OLD behaviour rather than inventing a new
        // failure: the risk is an accepted plan drawn in the author's ink, which is
        // exactly what the surface already did.
        const doc = makeDoc([head('f-1', 'Uploads'), para('It retries five times and backs off.', 'f-1')]);
        const stages = buildStages(doc, [], {},
            { 'f-1': { kind: 'amend', intent: 'x', baseline: 'It retries five times.' } });
        expect(stages['f-1'].humanBase).toBeDefined();
        expect(stages['f-1'].accepted).toBeUndefined();
    });
});

describe('an accepted plan ADD is gray over its whole node', () => {
    it('reads an empty baseline on a plan add as "there was nothing before"', () => {
        // A placeholder replaced nothing, so its empty baseline is a real value. Read as
        // "no baseline, nothing to draw", the one node on the page where every word is
        // unbuilt rendered as ordinary settled prose the instant it was minted.
        const doc = makeDoc([head('f-new', 'Backoff'), para('It waits longer each time.', 'f-new')]);
        const stages = buildStages(doc, [], {}, {
            'f-new': { kind: 'add_node', intent: 'build this', origin: 'plan', baseline: '' },
        });
        expect(stages['f-new'].accepted!.prev).toEqual({ title: '', paras: [] });
        const claims = claimsFor({ ...stages['f-new'], live: stages['f-new'].projected });
        expect(claims.length).toBeGreaterThan(0);
        expect(claims.every(c => c.channel === 'plan' && c.stage === 'accepted')).toBe(true);
    });

    it('still ignores an empty baseline on every other kind — there it IS missing', () => {
        // A steer carries no baseline at all. Treating that as "the description was
        // empty" would ink the whole feature as something a plan had just written.
        const doc = makeDoc([head('f-1', 'Uploads'), para('It retries five times.', 'f-1')]);
        const stages = buildStages(doc, [], {}, {
            'f-1': { kind: 'steer', intent: 'address your note', origin: 'human', baseline: '' },
        });
        expect(stages['f-1']?.accepted).toBeUndefined();
        expect(stages['f-1']?.humanBase).toBeUndefined();
    });
});
