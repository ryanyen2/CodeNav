import * as vscode from 'vscode';

// Identity / event markers ⟨f-…⟩ ⟨e-…⟩ (plus the two spaces before them) are
// collapsed to nothing — the human never sees or types an id.
const HIDDEN_ID_RE = /\s*⟨(?:f|e)-[0-9a-f]+⟩/g;
// A proposal diff hunk: col-0 op char, space, a feature marker, space.
const DIFF_HUNK_RE = /^([+\-~]) [-~] /;
// A retired *live* feature line: '~ Title' (3rd char is a letter, not a marker).
const RETIRED_RE = /^\s*~\s+\S/;

export interface CodocDecorations {
    hiddenId: vscode.TextEditorDecorationType;
    addHunk: vscode.TextEditorDecorationType;
    retireHunk: vscode.TextEditorDecorationType;
    moveHunk: vscode.TextEditorDecorationType;
    retired: vscode.TextEditorDecorationType;
    activeFeature: vscode.TextEditorDecorationType;
}

export function createDecorations(_context: vscode.ExtensionContext): CodocDecorations {
    return {
        // display:none collapses the range so the id occupies no visual width.
        hiddenId: vscode.window.createTextEditorDecorationType({
            textDecoration: 'none; display: none;',
        }),
        addHunk: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: 'rgba(60,180,80,0.10)',
            overviewRulerColor: 'rgba(60,180,80,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
        }),
        retireHunk: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: 'rgba(200,80,80,0.10)',
            overviewRulerColor: 'rgba(200,80,80,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
        }),
        moveHunk: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: 'rgba(80,120,200,0.10)',
            overviewRulerColor: 'rgba(80,120,200,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
        }),
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
    };
}

export function applyDecorations(editor: vscode.TextEditor, dec: CodocDecorations, activeFeatureLines: number[] = []): void {
    if (editor.document.languageId !== 'codoc') return;
    const hiddenId: vscode.Range[] = [];
    const addHunk: vscode.Range[] = [];
    const retireHunk: vscode.Range[] = [];
    const moveHunk: vscode.Range[] = [];
    const retired: vscode.Range[] = [];

    for (let i = 0; i < editor.document.lineCount; i++) {
        const text = editor.document.lineAt(i).text;

        HIDDEN_ID_RE.lastIndex = 0;
        let m: RegExpExecArray | null;
        while ((m = HIDDEN_ID_RE.exec(text)) !== null) {
            hiddenId.push(new vscode.Range(i, m.index, i, m.index + m[0].length));
        }

        const hunk = DIFF_HUNK_RE.exec(text);
        if (hunk) {
            const range = new vscode.Range(i, 0, i, text.length);
            if (hunk[1] === '+') addHunk.push(range);
            else if (hunk[1] === '-') retireHunk.push(range);
            else moveHunk.push(range);
            continue;
        }

        if (RETIRED_RE.test(text)) retired.push(new vscode.Range(i, 0, i, text.length));
    }

    editor.setDecorations(dec.hiddenId, hiddenId);
    editor.setDecorations(dec.addHunk, addHunk);
    editor.setDecorations(dec.retireHunk, retireHunk);
    editor.setDecorations(dec.moveHunk, moveHunk);
    editor.setDecorations(dec.retired, retired);

    const activeRanges = activeFeatureLines.map(
        line => new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER)
    );
    editor.setDecorations(dec.activeFeature, activeRanges);
}
