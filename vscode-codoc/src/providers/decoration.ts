import * as vscode from 'vscode';
import { parseTreeCodoc } from '../state/tree-model';
import { SidecarData, emptySidecar } from '../state/bindings-model';
import { PendingChange } from '../state/realize-model';

// Identity / event markers ⟨f-…⟩ ⟨e-…⟩ (plus the two spaces before them) are
// collapsed to nothing — the human never sees or types an id.
const HIDDEN_ID_RE = /\s*⟨(?:f|e)-[0-9a-f]+⟩/g;
// A retired *live* feature line: '~ Title' (3rd char is a letter, not a marker).
const RETIRED_RE = /^\s*~\s+\S/;

// Proposal hunk accents. Literal rgba (VS Code decoration colors don't resolve CSS
// vars), centralized here so each colour is defined once: `tint` is the whole-line
// wash, `rule` the 2px left border + overview-ruler tick.
const HUNK = {
    add: { tint: 'rgba(60,180,80,0.10)', rule: 'rgba(60,180,80,0.7)' },      // green
    retire: { tint: 'rgba(200,80,80,0.10)', rule: 'rgba(200,80,80,0.7)' },   // red
    move: { tint: 'rgba(80,120,200,0.10)', rule: 'rgba(80,120,200,0.7)' },   // blue
} as const;

/** A whole-line proposal hunk: tinted background + 2px left border + ruler tick. */
function hunkDecoration(c: { tint: string; rule: string }): vscode.TextEditorDecorationType {
    return vscode.window.createTextEditorDecorationType({
        isWholeLine: true,
        backgroundColor: c.tint,
        overviewRulerColor: c.rule,
        overviewRulerLane: vscode.OverviewRulerLane.Right,
        borderColor: c.rule,
        borderWidth: '0 0 0 2px',
        borderStyle: 'solid',
    });
}

export interface CodocDecorations {
    hiddenId: vscode.TextEditorDecorationType;
    addHunk: vscode.TextEditorDecorationType;
    retireHunk: vscode.TextEditorDecorationType;
    moveHunk: vscode.TextEditorDecorationType;
    retired: vscode.TextEditorDecorationType;
    activeFeature: vscode.TextEditorDecorationType;
    dimmed: vscode.TextEditorDecorationType;
    // In-place overlays for sidecar-driven proposals on the *live* node.
    retireStrike: vscode.TextEditorDecorationType;   // proposed retire (node not yet retired)
    amendInline: vscode.TextEditorDecorationType;     // proposed title/description edit
    unrealizedPlaceholder: vscode.TextEditorDecorationType;  // accepted plan node, no code yet
    pendingCodeChange: vscode.TextEditorDecorationType;  // source code a queued tree edit will rework
}

export function createDecorations(_context: vscode.ExtensionContext): CodocDecorations {
    return {
        // display:none collapses the range so the id occupies no visual width.
        hiddenId: vscode.window.createTextEditorDecorationType({
            textDecoration: 'none; display: none;',
        }),
        addHunk: hunkDecoration(HUNK.add),
        retireHunk: hunkDecoration(HUNK.retire),
        moveHunk: hunkDecoration(HUNK.move),
        retired: vscode.window.createTextEditorDecorationType({
            textDecoration: 'line-through',
            opacity: '0.55',
        }),
        activeFeature: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: new vscode.ThemeColor('diffEditor.insertedLineBackground'),
            overviewRulerColor: new vscode.ThemeColor('charts.yellow'),
            overviewRulerLane: vscode.OverviewRulerLane.Left,
        }),
        dimmed: vscode.window.createTextEditorDecorationType({
            opacity: '0.8',
        }),
        // Proposed retire: strike the live node where it stands (it is NOT yet
        // retired in the store), tinted red so it reads as "going away".
        retireStrike: vscode.window.createTextEditorDecorationType({
            textDecoration: 'line-through',
            opacity: '0.7',
            overviewRulerColor: HUNK.retire.rule,
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            borderColor: HUNK.retire.rule,
            borderWidth: '0 0 0 2px',
            borderStyle: 'solid',
        }),
        // Proposed amend: a blue left-border marker; the proposed title/description
        // ride in a per-range hover + trailing "→ New title" hint.
        amendInline: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: HUNK.move.rule,
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            borderColor: HUNK.move.rule,
            borderWidth: '0 0 0 2px',
            borderStyle: 'solid',
        }),
        // Accepted plan placeholder with no code yet: muted + italic + dashed rule.
        unrealizedPlaceholder: vscode.window.createTextEditorDecorationType({
            fontStyle: 'italic',
            opacity: '0.7',
            borderColor: new vscode.ThemeColor('charts.purple'),
            borderWidth: '0 0 0 2px',
            borderStyle: 'dashed',
        }),
        // Reverse direction: source code a queued tree edit will rework. Dashed
        // purple (matches the "planned, not yet done" language of unrealized),
        // calm — no animation on the code side.
        pendingCodeChange: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            overviewRulerColor: new vscode.ThemeColor('charts.purple'),
            overviewRulerLane: vscode.OverviewRulerLane.Left,
            borderColor: new vscode.ThemeColor('charts.purple'),
            borderWidth: '0 0 0 2px',
            borderStyle: 'dashed',
        }),
    };
}

/**
 * Decorate a SOURCE editor (python/ts/js, not the codoc tree) with the lines a
 * queued tree edit will rework, so the reverse direction (codoc → codebase) is
 * visible before the agent touches the code. Matches declaration lines by the
 * leaf name of each pending symbol; falls back to a file-level marker on line 0
 * for file-scoped (no-symbol) changes.
 */
export function applyPendingCodeDecorations(
    editor: vscode.TextEditor,
    dec: CodocDecorations,
    pending: PendingChange[],
): void {
    if (editor.document.languageId === 'codoc' || pending.length === 0) {
        editor.setDecorations(dec.pendingCodeChange, []);
        return;
    }
    const leaves = new Map<string, string>();   // leaf symbol → driving title
    let fileLevel: string | null = null;
    for (const c of pending) {
        if (c.symbol) leaves.set(c.symbol.split('::').pop()!, c.title);
        else if (fileLevel === null) fileLevel = c.title;
    }

    const ranges: vscode.DecorationOptions[] = [];
    if (leaves.size) {
        for (let i = 0; i < editor.document.lineCount; i++) {
            const line = editor.document.lineAt(i).text;
            if (!/^\s*(def |class |function |async def |export\s+(function|class|default))/.test(line)) continue;
            const name = (/(?:def |class |function |async def )\s*(\w+)/.exec(line) ?? [])[1];
            const title = name ? leaves.get(name) : undefined;
            if (!title) continue;
            ranges.push({
                range: new vscode.Range(i, 0, i, line.length),
                hoverMessage: new vscode.MarkdownString(`**codoc** — a queued tree edit (“${title}”) will rework this. Run \`/codoc:realize\`.`),
            });
        }
    }
    if (ranges.length === 0 && fileLevel) {
        ranges.push({
            range: new vscode.Range(0, 0, 0, editor.document.lineAt(0).text.length),
            hoverMessage: new vscode.MarkdownString(`**codoc** — a queued tree edit (“${fileLevel}”) will add code to this file. Run \`/codoc:realize\`.`),
        });
    }
    editor.setDecorations(dec.pendingCodeChange, ranges);
}

export function applyDecorations(
    editor: vscode.TextEditor,
    dec: CodocDecorations,
    activeFeatureLines: number[] = [],
    sidecar: SidecarData = emptySidecar(),
): void {
    if (editor.document.languageId !== 'codoc') return;
    const hiddenId: vscode.Range[] = [];
    const addHunk: vscode.Range[] = [];
    const retireHunk: vscode.Range[] = [];
    const moveHunk: vscode.Range[] = [];
    const retired: vscode.Range[] = [];

    // Colour each ADD/MOVE ghost hunk by op, using the parser's line ranges.
    // Their lines are excluded from the retired-strike scan below (a move ghost
    // title starts with '~', which RETIRED_RE would otherwise match).
    const { features, proposals } = parseTreeCodoc(editor.document.getText());
    const proposalLines = new Set<number>();
    for (const p of proposals) {
        const bucket = p.op === 'add' ? addHunk : p.op === 'retire' ? retireHunk : moveHunk;
        for (let ln = p.line; ln <= p.endLine; ln++) {
            bucket.push(new vscode.Range(ln, 0, ln, 0));
            proposalLines.add(ln);
        }
    }

    // In-place overlays on the *live* node, driven by the sidecar (RETIRE/AMEND
    // emit no text, so they reach us only here). Also mark unrealized placeholders.
    const overlay = sidecar.proposals?.by_feature ?? {};
    const retireStrike: vscode.Range[] = [];
    const amendInline: vscode.DecorationOptions[] = [];
    const unrealized: vscode.Range[] = [];
    for (const f of features) {
        if (!f.id) continue;
        const line = editor.document.lineAt(f.line).text;
        const prop = overlay[f.id];
        if (prop?.op === 'retire') {
            retireStrike.push(new vscode.Range(f.line, 0, f.line, line.length));
        } else if (prop?.op === 'amend') {
            const newTitle = prop.title && prop.title !== f.title ? prop.title : null;
            const md = new vscode.MarkdownString(
                `**Proposed amend** · ${prop.tag}\n\n` +
                (newTitle ? `**Title →** ${newTitle}\n\n` : '') +
                (prop.description ? `${prop.description}` : ''),
            );
            amendInline.push({
                range: new vscode.Range(f.line, 0, f.line, line.length),
                hoverMessage: md,
                renderOptions: newTitle ? {
                    after: { contentText: `  ✎ → ${newTitle}`, color: 'rgba(120,150,220,0.9)', fontStyle: 'italic' },
                } : undefined,
            });
        }
        if (sidecar.features[f.id]?.realized === false) {
            unrealized.push(new vscode.Range(f.line, 0, f.line, line.length));
        }
    }

    for (let i = 0; i < editor.document.lineCount; i++) {
        const text = editor.document.lineAt(i).text;

        HIDDEN_ID_RE.lastIndex = 0;
        let m: RegExpExecArray | null;
        while ((m = HIDDEN_ID_RE.exec(text)) !== null) {
            hiddenId.push(new vscode.Range(i, m.index, i, m.index + m[0].length));
        }

        if (!proposalLines.has(i) && RETIRED_RE.test(text)) {
            retired.push(new vscode.Range(i, 0, i, text.length));
        }
    }

    editor.setDecorations(dec.hiddenId, hiddenId);
    editor.setDecorations(dec.addHunk, addHunk);
    editor.setDecorations(dec.retireHunk, retireHunk);
    editor.setDecorations(dec.moveHunk, moveHunk);
    editor.setDecorations(dec.retired, retired);
    editor.setDecorations(dec.retireStrike, retireStrike);
    editor.setDecorations(dec.amendInline, amendInline);
    editor.setDecorations(dec.unrealizedPlaceholder, unrealized);

    const activeRanges = activeFeatureLines.map(
        line => new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER)
    );
    editor.setDecorations(dec.activeFeature, activeRanges);
}
