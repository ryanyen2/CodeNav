/**
 * bridge.ts — the pure logic of the live cross-surface diff bridge (P2 / spec §A).
 *
 * The bridge connects the two panes WITHOUT touching the doc round-trip: binding anchors
 * are the ground truth of "what code this prose is about" (no LLM). This module holds the
 * deterministic, host-and-webview-shared pieces so they are unit-testable in isolation:
 *
 *   - doc→code (A.2): which symbols a feature implicates, and which declaration lines in
 *     its bound file those symbols sit on (the green rail / lens / gutter targets).
 *   - code→doc (A.3): which feature ids a set of edited source lines belong to (the spark
 *     targets), by mapping each changed decl line back through the file's bindings.
 *   - the 180 ms debounce gate (A.1/A.5) that keeps a fast typist from thrashing the split.
 *
 * The decl-line regex is the SAME shape the existing code-lens / pending-code decorations
 * use (decoration.ts:183, code-lens.ts:30), so the bridge lights the same lines those do.
 */
import { symbolLeaf } from './registry-model';

/** A binding anchor as it rides in the sidecar (`by_feature` / `by_file`). Only the symbol
 *  path matters here; the file is the join key the host already has. */
export interface BridgeBinding {
    file: string;
    /** `file.py::Class.method` — split on `::` then `.` for the declared leaf name. */
    symbol: string;
}

/** A declaration line in a source file: the matched declared name, its 0-based
 *  line, and its `qualified` nesting path (`Class.method`) reconstructed from
 *  indentation. `qualified` is what disambiguates two same-leaf decls (e.g. `run`
 *  in two classes) so they don't spark each other (§6 fix). */
export interface DeclLine {
    name: string;
    line: number;
    /** The nesting path (`Class.method`) from indentation. Always set by
     *  `declLines`; optional so a hand-built decl (or an older caller) without it
     *  falls back to leaf matching. */
    qualified?: string;
}

/** The leaf (actually-declared) name of a binding symbol path. `file.py::Class.method`
 *  → `method`; `file.py::func` → `func`; a `__module__` anchor → '' (file-level, no decl).
 *  Wraps the canonical `symbolLeaf` (registry-model) — same `::`/`.` rule — adding only the
 *  file-level sentinel → '' mapping, per the "wrap this, not fork it" convention. */
export function bindingLeaf(symbol: string): string {
    const leaf = symbolLeaf(symbol);
    if (leaf === '__module__' || leaf === '<module>' || leaf === '‹module›') return '';
    return leaf;
}

/** The qualified path of a binding symbol path = everything after the `file::`
 *  prefix (`m.py::A.run` → `A.run`; `m.py::run` → `run`). The exact-match key used
 *  to disambiguate same-leaf decls in `featureIdsForChangedLines`. */
export function symbolQualified(symbol: string): string {
    const after = symbol.split('::', 2)[1] ?? symbol;
    if (after === '__module__' || after === '<module>' || after === '‹module›') return '';
    return after;
}

/** The primary (highest-weight) binding of a feature = the FIRST entry. The sidecar emits
 *  `by_feature` already ranked, so "open the top-weight file" is just `[0]` (spec A.1). Null
 *  when the feature has no binding (→ the doc-ahead "will create code" path, A.4). */
export function primaryBinding(bindings: BridgeBinding[]): BridgeBinding | null {
    return bindings.length ? bindings[0] : null;
}

/** The set of leaf names a feature's prose implicates — its binding anchors, file-level
 *  (`__module__`) ones dropped (they have no decl line; they drive the A.4 file-level lens).
 *  Restricted to the bindings IN `file` so the code side only lights its own file. */
export function implicatedLeaves(bindings: BridgeBinding[], file: string): Set<string> {
    const out = new Set<string>();
    for (const b of bindings) {
        if (b.file !== file) continue;
        const leaf = bindingLeaf(b.symbol);
        if (leaf) out.add(leaf);
    }
    return out;
}

// The declaration-line shape, matched the same way decoration.ts / code-lens.ts do (a leading
// def/class/function/async def/export). SCOPED to Python + JS/TS — the only languages codoc
// indexes today (lang/python.py + lang/typescript.py); a new language adapter would extend
// this (and the matching code-lens/decoration regexes) together. Fresh RegExp per call — no
// shared lastIndex.
const DECL_RE = /^\s*(def |class |function |async def |export\s+(function|class|default))/;
const DECL_NAME_RE = /(?:def |class |function |async def )\s*(\w+)/;

/** Scan source `lines` for every declaration line and its declared name (pure; the host
 *  passes `document` line texts). Used by both directions: doc→code filters these to the
 *  implicated leaves; code→doc maps an edited line back to the decl that owns it. */
export function declLines(lines: readonly string[]): DeclLine[] {
    const out: DeclLine[] = [];
    // Indentation stack of enclosing decls → reconstruct each decl's qualified path
    // (`Class.method`), mirroring the indexer's symbol_path. A new decl pops every
    // entry at the same or deeper indent (it has left those scopes) before nesting.
    const stack: { indent: number; name: string }[] = [];
    for (let i = 0; i < lines.length; i++) {
        if (!DECL_RE.test(lines[i])) continue;
        const name = (DECL_NAME_RE.exec(lines[i]) ?? [])[1];
        if (!name) continue;
        const indent = lines[i].length - lines[i].replace(/^\s*/, '').length;
        while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
        const qualified = [...stack.map(s => s.name), name].join('.');
        out.push({ name, line: i, qualified });
        stack.push({ indent, name });
    }
    return out;
}

/** The 0-based line numbers in `lines` that declare one of `leaves` — the green rail /
 *  gutter / lens targets for doc→code (A.2). Empty `leaves` → no lines (the caller then
 *  falls back to the file-level A.4 lens). */
export function implicatedDeclLines(lines: readonly string[], leaves: Set<string>): number[] {
    if (!leaves.size) return [];
    return declLines(lines).filter(d => leaves.has(d.name)).map(d => d.line);
}

/** The feature ids that own a set of EDITED source lines (code→doc, A.3). For each changed
 *  line we find the nearest enclosing declaration (the last decl at or above it), then map
 *  that decl name → the feature(s) bound to it via the file's `by_file` entries. Returns a
 *  de-duplicated id list. Pure: `fileEntries` is `sidecar.by_file[file]`, `decls` is
 *  `declLines(document lines)`, `changedLines` are the 0-based edited line numbers. */
export function featureIdsForChangedLines(
    fileEntries: readonly { symbol: string; feature_id: string }[],
    decls: readonly DeclLine[],
    changedLines: readonly number[],
): string[] {
    if (!fileEntries.length || !decls.length || !changedLines.length) return [];
    // Two indices: the precise QUALIFIED path (`Class.method`) and the bare leaf.
    // Qualified is tried first so two same-leaf decls (e.g. `run` in two classes)
    // map to their OWN feature instead of sparking each other (§6 fix); the leaf
    // index is the fallback for bindings/decls without a qualified path (an
    // authored partial ref, or a decl scan that didn't reconstruct nesting).
    const byQualified = new Map<string, Set<string>>();
    const byName = new Map<string, Set<string>>();
    for (const e of fileEntries) {
        const leaf = bindingLeaf(e.symbol);
        if (leaf) (byName.get(leaf) ?? byName.set(leaf, new Set()).get(leaf)!).add(e.feature_id);
        const q = symbolQualified(e.symbol);
        if (q) (byQualified.get(q) ?? byQualified.set(q, new Set()).get(q)!).add(e.feature_id);
    }
    // decls sorted ascending by line so "nearest enclosing" is the last one ≤ the change.
    const sorted = [...decls].sort((a, b) => a.line - b.line);
    const out = new Set<string>();
    for (const ln of changedLines) {
        let owner: DeclLine | null = null;
        for (const d of sorted) {
            if (d.line <= ln) owner = d; else break;
        }
        if (!owner) continue;
        // Prefer an exact qualified match; only fall back to the leaf when the
        // qualified path resolves nothing (so a precise binding wins over a
        // coincidental same-leaf one).
        const exact = owner.qualified ? byQualified.get(owner.qualified) : undefined;
        const fids = exact ?? byName.get(owner.name);
        for (const fid of fids ?? []) out.add(fid);
    }
    return [...out];
}

/** The contiguous 0-based line numbers touched by a text change spanning `[startLine,
 *  endLine]` (inclusive) — VS Code reports a change range; the bridge maps every line in it.
 *  Clamped to `lineCount`. Pure helper so the host's onDidChangeTextDocument stays thin. */
export function changedLineNumbers(startLine: number, endLine: number, lineCount: number): number[] {
    const lo = Math.max(0, Math.min(startLine, endLine));
    const hi = Math.min(lineCount - 1, Math.max(startLine, endLine));
    const out: number[] = [];
    for (let i = lo; i <= hi; i++) out.push(i);
    return out;
}

/**
 * Filter code→doc spark fids to only the USER's hand-edits (P2 fix 4 / §A.3). The reverse
 * spark is for "the human edited code, the doc may need to follow" — NOT the agent's own
 * realize writes (those would read as "external drift to review" when the agent is doing
 * exactly what was asked). While the activity epoch is OPEN, a feature the agent is actively
 * `editing`/`reflecting`, or that is HELD (its realize directive queued), is the pipeline's
 * own work → suppressed. With no open epoch, nothing is suppressed (it's all the user). Pure.
 */
export function userTouchedFids(
    fids: readonly string[],
    opts: {
        epochOpen: boolean;
        phase: Record<string, 'editing' | 'reflecting' | 'done'>;
        held: ReadonlySet<string>;
    },
): string[] {
    if (!opts.epochOpen) return [...fids];
    return fids.filter(fid => {
        const ph = opts.phase[fid];
        const agentOwned = ph === 'editing' || ph === 'reflecting' || opts.held.has(fid);
        return !agentOwned;
    });
}

/**
 * §A.6 dismiss-detection (P2 fix 3, §6 hardening). A bridge-opened file counts as a real
 * DISMISSAL only when it has left the set of OPEN TABS entirely — NOT merely the *visible*
 * set. A tab switch or editor-group reshuffle hides a bridge-opened file (drops it from
 * `visibleTextEditors`) without closing it; reading that as a dismissal would permanently
 * disable auto-open for the session on a benign interaction. Passing the open-TAB set (which
 * includes hidden-but-open tabs) instead distinguishes a true close from a switch.
 *
 * Returns the bridge-opened files that are truly `closed` (caller forgets them) and whether
 * any close happened (`dismissed` → caller persists the dismissal). Pure.
 */
export function bridgeDismissals(
    openedByBridge: Iterable<string>,
    openTabFiles: ReadonlySet<string>,
): { closed: string[]; dismissed: boolean } {
    const closed: string[] = [];
    for (const f of openedByBridge) {
        if (!openTabFiles.has(f)) closed.push(f);
    }
    return { closed, dismissed: closed.length > 0 };
}

/** A trailing-edge debounce gate (spec A.1/A.5): coalesce a burst of keystrokes into one
 *  fire `delayMs` after the LAST one. Pure-ish (takes a clock + a scheduler so it's testable
 *  with fake timers); the webview passes window.setTimeout/clearTimeout. */
export class BridgeDebounce {
    private timer: number | null = null;
    constructor(
        private readonly delayMs: number,
        private readonly schedule: (fn: () => void, ms: number) => number,
        private readonly cancel: (id: number) => void,
    ) {}

    /** Re-arm: cancel any pending fire and schedule `fn` after the configured delay. */
    fire(fn: () => void): void {
        this.clear();
        this.timer = this.schedule(fn, this.delayMs);
    }

    /** Cancel a pending fire (e.g. caret leaves the feature before the 180 ms elapses). */
    clear(): void {
        if (this.timer !== null) { this.cancel(this.timer); this.timer = null; }
    }

    /** True while a fire is scheduled (for tests / re-entrancy guards). */
    get pending(): boolean { return this.timer !== null; }
}
