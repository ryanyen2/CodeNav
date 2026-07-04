/**
 * doc-gate.test.ts — the per-feature HLC version gate (U5 / R14 / KTD4).
 *
 * Verifies that a returning/daemon-pushed projection never clobbers a newer optimistic
 * local edit to the feature the user is editing, while an advance on an UNRELATED
 * feature is adopted — the cross-feature no-clobber the removed whole-doc gate could
 * not provide. HLC `to_str()` strings are lexicographically ordered, so the gate
 * compares them as plain strings.
 */
import { describe, it, expect } from 'vitest';
import { gateProjection, shouldAdopt, type GateInput } from '../webview/doc-gate';
import type { PMNode } from '../state/pm-doc';

/** A featureHeading + one body paragraph carrying `body` text. `version` rides on the
 *  heading attrs exactly as `build_doc_from_store` projects it (U2). */
function feature(fid: string, title: string, body: string, version: string): PMNode[] {
    return [
        { type: 'featureHeading', attrs: { fid, level: 0, realized: true, version }, content: [{ type: 'text', text: title }] },
        { type: 'paragraph', content: [{ type: 'text', text: body }] },
    ];
}

function doc(...features: PMNode[][]): PMNode {
    return { type: 'doc', content: features.flat() };
}

/** The body text the gate produced for a feature (asserts adopt-vs-keep at a glance). */
function bodyOf(d: PMNode, fid: string): string | null {
    const blocks = d.content ?? [];
    const idx = blocks.findIndex(b => b.type === 'featureHeading' && (b.attrs as { fid?: string }).fid === fid);
    if (idx < 0) return null;
    const para = blocks[idx + 1];
    return para?.content?.[0]?.text ?? null;
}

function base(over: Partial<GateInput>): GateInput {
    return {
        incoming: doc(),
        local: doc(),
        localVersions: new Map(),
        pendingFids: new Set(),
        ...over,
    };
}

describe('shouldAdopt — the per-feature decision rule (KTD4)', () => {
    it('adopts when there is no pending local edit (regardless of version order)', () => {
        expect(shouldAdopt('100', '200', false)).toBe(true);  // even an older projection
        expect(shouldAdopt('300', '200', false)).toBe(true);
        expect(shouldAdopt('', '', false)).toBe(true);
    });
    it('with a pending edit, adopts only when the projection is strictly newer', () => {
        expect(shouldAdopt('300', '200', true)).toBe(true);   // newer → adopt
        expect(shouldAdopt('200', '200', true)).toBe(false);  // equal → keep local
        expect(shouldAdopt('100', '200', true)).toBe(false);  // older → keep local
    });
    it('compares HLC strings lexicographically', () => {
        // HLC to_str() is sortable as a string; a higher logical clock sorts later.
        expect(shouldAdopt('0000000000010:0001:n', '0000000000009:0001:n', true)).toBe(true);
        expect(shouldAdopt('0000000000009:0001:n', '0000000000010:0001:n', true)).toBe(false);
    });
});

describe('gateProjection — cross-feature no-clobber (R14 / KTD4)', () => {
    it('an advance on feature B does NOT revert a pending local edit on feature A', () => {
        // Local: A edited optimistically ("A-local"); B untouched. Adopted versions known.
        const local = doc(
            feature('f-A', 'A', 'A-local', 'vA1'),
            feature('f-B', 'B', 'B-old', 'vB1'),
        );
        // Projection: A unchanged at vA1 (no advance), B advanced to vB2 with new content.
        const incoming = doc(
            feature('f-A', 'A', 'A-old', 'vA1'),
            feature('f-B', 'B', 'B-new', 'vB2'),
        );
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1'], ['f-B', 'vB1']]),
            pendingFids: new Set(['f-A']),   // user is editing A
        }));
        expect(bodyOf(r.doc, 'f-A')).toBe('A-local'); // kept — not clobbered
        expect(bodyOf(r.doc, 'f-B')).toBe('B-new');   // unrelated advance adopted
        expect(r.adopted.get('f-B')).toBe('vB2');
        expect(r.adopted.has('f-A')).toBe(false);
    });

    it('adopts a feature whose per-feature version advanced even with a pending edit; keeps an older/equal one', () => {
        const local = doc(
            feature('f-A', 'A', 'A-local', 'vA1'),
            feature('f-B', 'B', 'B-local', 'vB1'),
        );
        // A advanced past the local edit (daemon genuinely moved on); B is only equal.
        const incoming = doc(
            feature('f-A', 'A', 'A-newer', 'vA2'),
            feature('f-B', 'B', 'B-server', 'vB1'),
        );
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1'], ['f-B', 'vB1']]),
            pendingFids: new Set(['f-A', 'f-B']),  // both edited locally
        }));
        expect(bodyOf(r.doc, 'f-A')).toBe('A-newer');  // newer projection adopted
        expect(bodyOf(r.doc, 'f-B')).toBe('B-local');  // equal version → kept local
        expect(r.adopted.get('f-A')).toBe('vA2');
        expect(r.adopted.has('f-B')).toBe(false);
    });

    it('after a reload (empty local tracking), the first projection at a higher version is adopted (no pending edit ⇒ adopt)', () => {
        // A daemon-restart batch lands a higher version; the webview's in-memory
        // last-applied state was reset by the reload (empty maps, no pending edits).
        const local = doc(feature('f-A', 'A', 'A-stale', 'vA1'));
        const incoming = doc(feature('f-A', 'A', 'A-batch', 'vA9'));
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map(),   // reset by reload
            pendingFids: new Set(),     // no pending edits after reload
        }));
        expect(bodyOf(r.doc, 'f-A')).toBe('A-batch');
        expect(r.adopted.get('f-A')).toBe('vA9');
    });

    it('rapid local edits then a delayed (not-newer) projection do not revert the edits', () => {
        // User typed several times → pending edit at the locally-tracked version; a delayed
        // projection arrives carrying the PRE-edit version → must keep local.
        const local = doc(feature('f-A', 'A', 'A-rapid', 'vA1'));
        const incoming = doc(feature('f-A', 'A', 'A-delayed', 'vA1'));
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1']]),
            pendingFids: new Set(['f-A']),
        }));
        expect(bodyOf(r.doc, 'f-A')).toBe('A-rapid');
        expect(r.adopted.has('f-A')).toBe(false);
    });

    it('structure follows the projection; a kept-local feature occupies its projected slot', () => {
        // Projection reorders B before A; A has a pending kept-local edit. The merged doc
        // takes the projection's order, with A's LOCAL blocks at A's projected position.
        const local = doc(
            feature('f-A', 'A', 'A-local', 'vA1'),
            feature('f-B', 'B', 'B-x', 'vB1'),
        );
        const incoming = doc(
            feature('f-B', 'B', 'B-x', 'vB1'),
            feature('f-A', 'A', 'A-old', 'vA1'),
        );
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1'], ['f-B', 'vB1']]),
            pendingFids: new Set(['f-A']),
        }));
        const fids = (r.doc.content ?? []).filter(b => b.type === 'featureHeading').map(b => (b.attrs as { fid: string }).fid);
        expect(fids).toEqual(['f-B', 'f-A']);     // projection order
        expect(bodyOf(r.doc, 'f-A')).toBe('A-local'); // but A's local content kept
    });

    it('keeps a local-only feature with a pending edit that the projection has not rendered yet', () => {
        // Optimistic add: local has f-NEW (pending) the daemon has not echoed back.
        const local = doc(
            feature('f-A', 'A', 'A-x', 'vA1'),
            feature('f-NEW', 'New', 'new-local', 'vN1'),
        );
        const incoming = doc(feature('f-A', 'A', 'A-x', 'vA1'));
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1']]),
            pendingFids: new Set(['f-NEW']),
        }));
        expect(bodyOf(r.doc, 'f-NEW')).toBe('new-local'); // not dropped
    });

    it('drops a local feature the projection no longer carries when it has no pending edit (a deletion)', () => {
        const local = doc(
            feature('f-A', 'A', 'A-x', 'vA1'),
            feature('f-GONE', 'Gone', 'gone', 'vG1'),
        );
        const incoming = doc(feature('f-A', 'A', 'A-x', 'vA1'));
        const r = gateProjection(base({
            incoming, local,
            localVersions: new Map([['f-A', 'vA1'], ['f-GONE', 'vG1']]),
            pendingFids: new Set(),  // f-GONE was not being edited → accept the deletion
        }));
        expect(bodyOf(r.doc, 'f-GONE')).toBeNull();
    });

    it('keeps a null-fid (mid-mint) authored heading so patchMintedIds can fill it', () => {
        const local: PMNode = {
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: null, level: 0, realized: true }, content: [{ type: 'text', text: 'Draft' }] },
                { type: 'paragraph', content: [{ type: 'text', text: 'draft-body' }] },
            ],
        };
        const incoming = doc(feature('f-A', 'A', 'A-x', 'vA1'));
        const r = gateProjection(base({ incoming, local }));
        const titles = (r.doc.content ?? []).filter(b => b.type === 'featureHeading').map(b => b.content?.[0]?.text);
        expect(titles).toContain('Draft'); // the not-yet-minted heading survives
        expect(titles).toContain('A');
    });
});
