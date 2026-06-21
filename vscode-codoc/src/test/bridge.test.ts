/**
 * bridge.test.ts — the pure logic of the live cross-surface diff bridge (P2 / spec §A).
 *
 * The bridge's host + webview both read these deterministic helpers; binding anchors are the
 * ground truth of "what code this prose is about" (no LLM, no doc-parse — the round-trip is
 * untouched), so getting the leaf/decl/line mapping exactly right is the whole correctness
 * story. The DOM (open-beside, green decorations, the spark) is EDH-only; this is the half
 * that's machine-checkable.
 */
import { describe, it, expect, vi } from 'vitest';
import {
    bindingLeaf, primaryBinding, implicatedLeaves, declLines, implicatedDeclLines,
    featureIdsForChangedLines, changedLineNumbers, userTouchedFids, bridgeDismissals, BridgeDebounce,
} from '../state/bridge';

describe('bindingLeaf — the declared name a binding anchor resolves to', () => {
    it('strips the file qualifier and the class path to the declared leaf', () => {
        expect(bindingLeaf('loop/loop_a.py::run_loop_a')).toBe('run_loop_a');
        expect(bindingLeaf('store/db.py::Store.upsert')).toBe('upsert');
        expect(bindingLeaf('bare_name')).toBe('bare_name');
    });
    it('returns "" for a file-level (__module__) anchor (no decl line)', () => {
        expect(bindingLeaf('pkg/mod.py::__module__')).toBe('');
        expect(bindingLeaf('pkg/mod.py::‹module›')).toBe('');
        expect(bindingLeaf('pkg/mod.py::<module>')).toBe('');
    });
});

describe('primaryBinding — the top-weight (first) binding (§A.1)', () => {
    it('is the first entry (the sidecar emits by_feature ranked)', () => {
        const b = [{ file: 'a.py', symbol: 'a.py::f' }, { file: 'b.py', symbol: 'b.py::g' }];
        expect(primaryBinding(b)?.file).toBe('a.py');
    });
    it('is null when the feature has no binding (→ the A.4 no-binding path)', () => {
        expect(primaryBinding([])).toBeNull();
    });
});

describe('implicatedLeaves — the decl names a feature implicates IN its primary file', () => {
    it('keeps only the bindings in `file`, dropping file-level anchors', () => {
        const binds = [
            { file: 'a.py', symbol: 'a.py::Foo.bar' },
            { file: 'a.py', symbol: 'a.py::baz' },
            { file: 'a.py', symbol: 'a.py::__module__' },  // file-level → no decl
            { file: 'b.py', symbol: 'b.py::other' },       // different file → excluded
        ];
        expect(implicatedLeaves(binds, 'a.py')).toEqual(new Set(['bar', 'baz']));
    });
});

describe('declLines — declaration scan (shared with code-lens)', () => {
    it('finds python + ts/js declarations with their names and 0-based lines', () => {
        const src = [
            'import os',                 // 0
            'def run_loop_a(x):',        // 1
            '    return x',              // 2
            'class Store:',              // 3
            '    def upsert(self): ...', // 4
            'export function build() {}',// 5
        ];
        expect(declLines(src)).toEqual([
            { name: 'run_loop_a', line: 1, qualified: 'run_loop_a' },
            { name: 'Store', line: 3, qualified: 'Store' },
            { name: 'upsert', line: 4, qualified: 'Store.upsert' },  // nested under Store
            { name: 'build', line: 5, qualified: 'build' },          // popped back to top level
        ]);
    });

    it('qualifies two same-leaf methods by their enclosing class', () => {
        const src = [
            'class A:',                  // 0
            '    def run(self): ...',    // 1
            'class B:',                  // 2
            '    def run(self): ...',    // 3
        ];
        expect(declLines(src)).toEqual([
            { name: 'A', line: 0, qualified: 'A' },
            { name: 'run', line: 1, qualified: 'A.run' },
            { name: 'B', line: 2, qualified: 'B' },
            { name: 'run', line: 3, qualified: 'B.run' },
        ]);
    });
});

describe('implicatedDeclLines — doc→code targets (§A.2)', () => {
    it('lights only the decl lines whose name is implicated', () => {
        const src = ['def a():', '    pass', 'def b():', '    pass', 'def c():'];
        expect(implicatedDeclLines(src, new Set(['a', 'c']))).toEqual([0, 4]);
    });
    it('lights nothing for an empty leaf set (caller then uses the file-level lens)', () => {
        expect(implicatedDeclLines(['def a():'], new Set())).toEqual([]);
    });
});

describe('featureIdsForChangedLines — code→doc mapping (§A.3)', () => {
    const fileEntries = [
        { symbol: 'm.py::Store.upsert', feature_id: 'f-store' },
        { symbol: 'm.py::run', feature_id: 'f-run' },
        { symbol: 'm.py::run', feature_id: 'f-run2' },   // a decl can belong to two features
    ];
    const decls = [
        { name: 'Store', line: 0 },
        { name: 'upsert', line: 2 },
        { name: 'run', line: 6 },
    ];

    it('maps an edited line to the nearest ENCLOSING declaration → its feature(s)', () => {
        // line 3 is inside `upsert` (decl at 2) → f-store
        expect(featureIdsForChangedLines(fileEntries, decls, [3])).toEqual(['f-store']);
    });
    it('returns every feature bound to the touched decl (shared decl)', () => {
        // line 7 is inside `run` (decl at 6) → both f-run and f-run2
        expect(featureIdsForChangedLines(fileEntries, decls, [7]).sort()).toEqual(['f-run', 'f-run2']);
    });
    it('de-duplicates across multiple changed lines in the same decl', () => {
        expect(featureIdsForChangedLines(fileEntries, decls, [6, 7, 8])).toEqual(['f-run', 'f-run2']);
    });
    it('ignores a change above the first declaration (imports / module prologue)', () => {
        const d = [{ name: 'run', line: 6 }];
        expect(featureIdsForChangedLines(fileEntries, d, [1])).toEqual([]);
    });
    it('is empty when the file has no bindings', () => {
        expect(featureIdsForChangedLines([], decls, [3])).toEqual([]);
    });

    it('disambiguates two same-leaf methods by qualified path (§6 — no cross-spark)', () => {
        // Two `run` methods in different classes, each bound to its OWN feature.
        const entries = [
            { symbol: 'm.py::A.run', feature_id: 'f-a' },
            { symbol: 'm.py::B.run', feature_id: 'f-b' },
        ];
        const qdecls = [
            { name: 'A', line: 0, qualified: 'A' },
            { name: 'run', line: 1, qualified: 'A.run' },
            { name: 'B', line: 2, qualified: 'B' },
            { name: 'run', line: 3, qualified: 'B.run' },
        ];
        // Editing inside A.run sparks ONLY f-a (not f-b, despite the shared leaf).
        expect(featureIdsForChangedLines(entries, qdecls, [1])).toEqual(['f-a']);
        expect(featureIdsForChangedLines(entries, qdecls, [3])).toEqual(['f-b']);
    });

    it('falls back to leaf matching when a decl carries no qualified path', () => {
        // A binding with only a partial/leaf symbol still resolves via the leaf index.
        const entries = [{ symbol: 'm.py::solo', feature_id: 'f-solo' }];
        const d = [{ name: 'solo', line: 0 }];  // no `qualified`
        expect(featureIdsForChangedLines(entries, d, [1])).toEqual(['f-solo']);
    });
});

describe('changedLineNumbers — a change range → its lines', () => {
    it('expands an inclusive [start,end] range, clamped to lineCount', () => {
        expect(changedLineNumbers(2, 4, 10)).toEqual([2, 3, 4]);
        expect(changedLineNumbers(8, 12, 10)).toEqual([8, 9]);   // clamp to last line (9)
        expect(changedLineNumbers(5, 5, 10)).toEqual([5]);
    });
    it('normalizes a reversed range', () => {
        expect(changedLineNumbers(4, 2, 10)).toEqual([2, 3, 4]);
    });
});

describe('BridgeDebounce — the 180 ms trailing-edge gate (§A.1/§A.5)', () => {
    it('coalesces a burst into ONE fire after the last call', () => {
        vi.useFakeTimers();
        const d = new BridgeDebounce(180, (fn, ms) => setTimeout(fn, ms) as unknown as number, id => clearTimeout(id));
        const fn = vi.fn();
        d.fire(fn); d.fire(fn); d.fire(fn);   // a typing burst
        expect(fn).not.toHaveBeenCalled();
        vi.advanceTimersByTime(179);
        expect(fn).not.toHaveBeenCalled();    // not yet — re-armed each keystroke
        vi.advanceTimersByTime(1);
        expect(fn).toHaveBeenCalledTimes(1);  // exactly once, after the last
        vi.useRealTimers();
    });

    it('clear() cancels a pending fire (caret left before the 180 ms elapsed)', () => {
        vi.useFakeTimers();
        const d = new BridgeDebounce(180, (fn, ms) => setTimeout(fn, ms) as unknown as number, id => clearTimeout(id));
        const fn = vi.fn();
        d.fire(fn);
        expect(d.pending).toBe(true);
        d.clear();
        expect(d.pending).toBe(false);
        vi.advanceTimersByTime(500);
        expect(fn).not.toHaveBeenCalled();
        vi.useRealTimers();
    });
});

describe('userTouchedFids — suppress the spark for the agent\'s own writes (P2 fix 4 / §A.3)', () => {
    it('passes everything through when no epoch is open (it\'s all the user)', () => {
        expect(userTouchedFids(['f-a', 'f-b'], { epochOpen: false, phase: { 'f-a': 'editing' }, held: new Set(['f-b']) }))
            .toEqual(['f-a', 'f-b']);
    });
    it('drops a feature the agent is actively editing/reflecting during an open epoch', () => {
        expect(userTouchedFids(['f-a', 'f-b', 'f-c'], {
            epochOpen: true,
            phase: { 'f-a': 'editing', 'f-b': 'reflecting' },
            held: new Set(),
        })).toEqual(['f-c']);   // f-a/f-b are the agent's own work
    });
    it('drops a HELD feature (its realize directive is queued) during an open epoch', () => {
        expect(userTouchedFids(['f-a', 'f-b'], { epochOpen: true, phase: {}, held: new Set(['f-a']) }))
            .toEqual(['f-b']);
    });
    it('keeps a feature in phase `done` (the agent finished — a later user edit is the user\'s)', () => {
        expect(userTouchedFids(['f-a'], { epochOpen: true, phase: { 'f-a': 'done' }, held: new Set() }))
            .toEqual(['f-a']);
    });
});

describe('bridgeDismissals — true close vs tab switch (§6 hardening)', () => {
    it('a bridge-opened file still OPEN as a (hidden) tab is NOT a dismissal', () => {
        const opened = ['a.py', 'b.py'];
        const openTabs = new Set(['a.py', 'b.py', 'codoc.tree']);  // both still open, just not visible
        const { closed, dismissed } = bridgeDismissals(opened, openTabs);
        expect(closed).toEqual([]);
        expect(dismissed).toBe(false);
    });

    it('a bridge-opened file gone from ALL tabs IS a dismissal', () => {
        const { closed, dismissed } = bridgeDismissals(['a.py', 'b.py'], new Set(['b.py']));
        expect(closed).toEqual(['a.py']);   // a.py truly closed → forget it
        expect(dismissed).toBe(true);
    });

    it('no bridge-opened files → never a dismissal', () => {
        expect(bridgeDismissals([], new Set(['a.py']))).toEqual({ closed: [], dismissed: false });
    });
});
