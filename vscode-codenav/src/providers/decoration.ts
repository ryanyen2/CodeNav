import * as vscode from 'vscode';

const STATE_RE = /\[([^\]]+)\]/;
const PROPOSAL_PREFIX_RE = /^\s*\?\s/;
const RETIRED_PREFIX_RE = /^\s*~\s/;

interface StateDecorations {
    stable: vscode.TextEditorDecorationType;
    drafting: vscode.TextEditorDecorationType;
    strained: vscode.TextEditorDecorationType;
    severed: vscode.TextEditorDecorationType;
    deprecated: vscode.TextEditorDecorationType;
    stub: vscode.TextEditorDecorationType;
    proposal: vscode.TextEditorDecorationType;
    retired: vscode.TextEditorDecorationType;
}

export function createDecorations(): StateDecorations {
    return {
        stable: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(0,200,0,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        drafting: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(255,200,0,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        strained: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(255,140,0,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        severed: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(220,0,0,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        deprecated: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(120,120,120,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
            opacity: '0.6',
        }),
        stub: vscode.window.createTextEditorDecorationType({
            overviewRulerColor: 'rgba(150,150,255,0.5)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        proposal: vscode.window.createTextEditorDecorationType({
            borderWidth: '0 0 0 3px',
            borderStyle: 'solid',
            borderColor: 'rgba(50,150,255,0.7)',
            isWholeLine: true,
        }),
        retired: vscode.window.createTextEditorDecorationType({
            textDecoration: 'line-through',
            opacity: '0.5',
        }),
    };
}

export function applyDecorations(editor: vscode.TextEditor, dec: StateDecorations): void {
    if (editor.document.languageId !== 'codoc') return;
    const stable: vscode.Range[] = [];
    const drafting: vscode.Range[] = [];
    const strained: vscode.Range[] = [];
    const severed: vscode.Range[] = [];
    const deprecated: vscode.Range[] = [];
    const stub: vscode.Range[] = [];
    const proposal: vscode.Range[] = [];
    const retired: vscode.Range[] = [];

    for (let i = 0; i < editor.document.lineCount; i++) {
        const text = editor.document.lineAt(i).text;
        const range = new vscode.Range(i, 0, i, text.length);
        if (PROPOSAL_PREFIX_RE.test(text)) proposal.push(range);
        if (RETIRED_PREFIX_RE.test(text)) retired.push(range);
        const m = STATE_RE.exec(text);
        if (!m) continue;
        const state = m[1].toLowerCase();
        switch (state) {
            case 'stable': stable.push(range); break;
            case 'drafting': drafting.push(range); break;
            case 'strained': strained.push(range); break;
            case 'severed': severed.push(range); break;
            case 'deprecated': deprecated.push(range); break;
            case 'stub': stub.push(range); break;
        }
    }
    editor.setDecorations(dec.stable, stable);
    editor.setDecorations(dec.drafting, drafting);
    editor.setDecorations(dec.strained, strained);
    editor.setDecorations(dec.severed, severed);
    editor.setDecorations(dec.deprecated, deprecated);
    editor.setDecorations(dec.stub, stub);
    editor.setDecorations(dec.proposal, proposal);
    editor.setDecorations(dec.retired, retired);
}
