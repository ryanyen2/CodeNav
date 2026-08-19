import { describe, it, expect } from 'vitest';
import { buildStages, featureTextsFrom, planNodesFrom, type StagedProposal } from '../state/settlement-stages';
import { claimsFor } from '../state/settlement';
import { materializePlan } from '../state/plan-materialize';
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
    kind: 'amend', key: 'f-1', layerId: 'e-9', stage: 'proposed',
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
