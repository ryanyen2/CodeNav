/**
 * command-emitter.test.ts — the hub's half of the edit channel.
 *
 * In VS Code the extension host turns a settled doc into identity-keyed commands. On the
 * hub there is no such process, and what stood in for it posted the whole doc: the hub
 * wrote it to `tree.doc.json`, a daemon-owned projection that nothing reads as input
 * since U7, so a remote contributor's prose was overwritten at the next render — and the
 * write made `safe_write_tree` skip re-rendering the exports until something else moved.
 *
 * These pin that the client now emits the same commands the host would, with the same
 * baseline citation and the same `base_text` provenance — the shared modules are the point,
 * so the two homes cannot answer "what was this edit" differently.
 */
import { describe, it, expect } from 'vitest';
import { createCommandEmitter, commandMessage } from '../webview/command-emitter';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns } from '../state/pm-doc';
import type { PMNode } from '../state/pm-doc';

const feat = (fid: string, title: string, desc: string): PMNode[] => [
    featureHeadingNode({ fid, level: 0, retired: false, realized: true, localId: null },
                       textToInlineRuns(title)),
    paragraphNode(textToInlineRuns(desc), fid),
];

const doc = (...features: PMNode[][]): PMNode => makeDoc(features.flat());

describe('the hub client emits commands, not a document', () => {
    it('a settle against the cited baseline emits one command per changed field', () => {
        const e = createCommandEmitter('hub-a');
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original')), baselineId: 1 });

        const cmds = e.settle(doc(feat('f-1', 'Auth v2', 'original')), 1);

        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({
            kind: 'set_title', feature_id: 'f-1', base_text: 'Auth', session: 'hub-a',
            payload: { title: 'Auth v2' },
        });
    });

    it('an unchanged settle emits nothing at all', () => {
        const e = createCommandEmitter('hub-a');
        const d = doc(feat('f-1', 'Auth', 'original'));
        e.observe({ doc: d, baselineId: 1 });
        expect(e.settle(d, 1)).toEqual([]);
    });

    it('cites the baseline the author typed against, not the newest projection', () => {
        // The hub inherits finding #2's fix: a projection arriving mid-word flushes the
        // unsent text, and that flush must diff against what the author was looking at.
        const e = createCommandEmitter('hub-a');
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original'), feat('f-2', 'Theme', 'light')), baselineId: 1 });
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original'), feat('f-2', 'Theme', 'AGENT REWROTE')), baselineId: 2 });

        // Typed against baseline 1: f-1 edited, f-2 untouched (still the old text).
        const cmds = e.settle(doc(feat('f-1', 'Auth', 'my prose'), feat('f-2', 'Theme', 'light')), 1);

        expect(cmds.map(c => c.feature_id)).toEqual(['f-1']);
    });

    it('a burst of settles cites its own last write, not the text before it', () => {
        const e = createCommandEmitter('hub-a');
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original')), baselineId: 1 });

        e.settle(doc(feat('f-1', 'Auth', 'first')), 1);
        const second = e.settle(doc(feat('f-1', 'Auth', 'first second')), 1);

        // Without the optimistic overlay this would claim to replace 'original', which
        // the store has already moved past — a conflict with itself.
        expect(second[0].base_text).toBe('first');
    });

    it('each command carries a distinct id, so the ledger folds only real replays', () => {
        const e = createCommandEmitter('hub-a');
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original')), baselineId: 1 });
        const a = e.settle(doc(feat('f-1', 'Auth', 'one')), 1)[0];
        const b = e.settle(doc(feat('f-1', 'Auth', 'two')), 1)[0];
        expect(a.id).not.toBe(b.id);
    });
});

describe('the wire form dispatch._command reads', () => {
    it('maps a content command onto the camelCase keys the hub accepts', () => {
        expect(commandMessage({
            id: 'c-1', kind: 'set_description', feature_id: 'f-1',
            base_text: 'before', session: 'hub-a', payload: { description: 'after' },
        })).toEqual({
            kind: 'set_description', id: 'c-1', featureId: 'f-1',
            baseText: 'before', session: 'hub-a', payload: { description: 'after' },
        });
    });

    it('carries an add by its localId, since the fid is store-minted', () => {
        expect(commandMessage({
            id: 'c-add-L1', kind: 'add', local_id: 'L1',
            payload: { title: 'New', description: '', parent_id: null },
        })).toEqual({
            kind: 'add', id: 'c-add-L1', localId: 'L1',
            payload: { title: 'New', description: '', parent_id: null },
        });
    });

    it('omits an absent base_text rather than sending an empty claim', () => {
        // `base_text: ''` and "no claim" mean different things to the daemon: the empty
        // string says the author knew the field to be empty, and None says apply blind.
        const msg = commandMessage({ id: 'c-1', kind: 'move', feature_id: 'f-1',
                                     payload: { parent_id: 'f-2' } });
        expect('baseText' in msg).toBe(false);
    });

    it('keeps an empty base_text when that IS the claim', () => {
        const msg = commandMessage({ id: 'c-1', kind: 'set_description', feature_id: 'f-1',
                                     base_text: '', payload: { description: 'first prose' } });
        expect(msg).toMatchObject({ baseText: '' });
    });
});

describe('an explicitly authored command (a tree-pane drag)', () => {
    it('is folded into the overlay so the next settle does not re-derive it', () => {
        const e = createCommandEmitter('hub-a');
        e.observe({ doc: doc(feat('f-1', 'Auth', 'original')), baselineId: 1 });
        e.record([{ id: 'c-x', kind: 'set_description', feature_id: 'f-1',
                    payload: { description: 'set by another gesture' } }]);

        const cmds = e.settle(doc(feat('f-1', 'Auth', 'set by another gesture, then typing')), 1);

        expect(cmds[0].base_text).toBe('set by another gesture');
    });

    it('mints a fresh emission token per gesture', () => {
        const e = createCommandEmitter('hub-a');
        expect(e.token()).not.toBe(e.token());
    });
});
