/**
 * realize-model.ts — parse `.codoc/realize.md` (the directive queue Loop B
 * writes for the live session) into structured directives, and index the code
 * those directives will touch. Pure (no `vscode`, no I/O) so it can be tested.
 *
 * Directive blocks look like (see codoc/loop/loop_b.py::build_directive):
 *
 *   ### 2. ⟨d-1a2b3c4d⟩ UPDATE FEATURE: "Sandboxed code execution"
 *     New intent: …
 *     Bound code: execution.py::execute_code, execution.py::time_limit
 *     Edit only: execution.py
 *     Align the bound code with the new intent.
 *
 * The heading carries an optional directive id (`⟨d-…⟩`). NEW FEATURE blocks
 * carry only an `Intent:` (no bound code yet); UPDATE/RETIRE/STEER carry
 * `Bound code:` symbol paths + an `Edit only:` file scope (STEER's prose rides
 * in `Author note:`). Sentinel values like "(no bound code yet)" / "(none)"
 * are ignored.
 */

export type RealizeKind = 'new' | 'update' | 'retire' | 'steer';

export interface RealizeDirective {
    kind: RealizeKind;
    title: string;
    intent: string;
    boundCode: string[];   // full symbol_paths, e.g. "execution.py::execute_code"
    editOnly: string[];    // repo-relative files
}

/** A code site a queued directive will touch, for source-editor surfacing. */
export interface PendingChange {
    symbol?: string;       // full symbol_path (absent ⇒ file-level scope only)
    title: string;         // feature title driving the change
    kind: RealizeKind;
}

const HEADER_RE = /^###\s+\d+\.\s+(?:⟨d-[0-9a-f]+⟩\s+)?(NEW|UPDATE|RETIRE|STEER)\s+FEATURE:\s*"?(.*?)"?\s*$/;

function splitList(value: string): string[] {
    const v = value.trim();
    if (!v || v.startsWith('(')) return [];   // sentinel like "(no bound code yet)"
    return v.split(',').map(s => s.trim()).filter(s => s.length > 0 && !s.startsWith('('));
}

export function parseRealize(text: string): RealizeDirective[] {
    if (!text.trim()) return [];
    const lines = text.split('\n');
    const directives: RealizeDirective[] = [];
    let cur: RealizeDirective | null = null;

    for (const line of lines) {
        const h = HEADER_RE.exec(line.trim());
        if (h) {
            if (cur) directives.push(cur);
            const kind = h[1].toLowerCase() as RealizeKind;
            cur = { kind, title: h[2].trim(), intent: '', boundCode: [], editOnly: [] };
            continue;
        }
        if (!cur) continue;
        const m = /^\s*(Intent|New intent|Author note|Bound code|Edit only):\s*(.*)$/.exec(line);
        if (!m) continue;
        const field = m[1].toLowerCase();
        if (field === 'intent' || field === 'new intent' || field === 'author note') cur.intent = m[2].trim();
        else if (field === 'bound code') cur.boundCode = splitList(m[2]);
        else if (field === 'edit only') cur.editOnly = splitList(m[2]);
    }
    if (cur) directives.push(cur);
    return directives;
}

const fileOf = (symbolPath: string): string => symbolPath.split('::', 1)[0];

/**
 * Index directives by the repo-relative file they will touch. Each bound symbol
 * yields a symbol-scoped change; an `Edit only:` file with no bound symbols
 * yields a file-level change (symbol undefined).
 */
export function pendingCodeByFile(directives: RealizeDirective[]): Map<string, PendingChange[]> {
    const out = new Map<string, PendingChange[]>();
    const push = (file: string, change: PendingChange): void => {
        const list = out.get(file);
        if (list) list.push(change); else out.set(file, [change]);
    };
    for (const d of directives) {
        const filesWithSymbols = new Set<string>();
        for (const sym of d.boundCode) {
            const file = fileOf(sym);
            filesWithSymbols.add(file);
            push(file, { symbol: sym, title: d.title, kind: d.kind });
        }
        for (const file of d.editOnly) {
            if (!filesWithSymbols.has(file)) push(file, { title: d.title, kind: d.kind });
        }
    }
    return out;
}
