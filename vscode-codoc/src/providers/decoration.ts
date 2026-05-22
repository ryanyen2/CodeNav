import * as vscode from 'vscode';

const PROPOSAL_PREFIX_RE = /^\s*\?\s/;
const RETIRED_PREFIX_RE = /^\s*~\s/;

export const DIFF_INTRO_PREFIX = '+ ';
export const DIFF_RETIRE_PREFIX = '- ';
export const DIFF_AMEND_PREFIX = '~ ';

interface StateDecorations {
    stable: vscode.TextEditorDecorationType;
    drafting: vscode.TextEditorDecorationType;
    strained: vscode.TextEditorDecorationType;
    severed: vscode.TextEditorDecorationType;
    deprecated: vscode.TextEditorDecorationType;
    stub: vscode.TextEditorDecorationType;
    proposal: vscode.TextEditorDecorationType;
    retired: vscode.TextEditorDecorationType;
    introHunk: vscode.TextEditorDecorationType;
    retireHunk: vscode.TextEditorDecorationType;
    amendHunk: vscode.TextEditorDecorationType;
}

export function createDecorations(context: vscode.ExtensionContext): StateDecorations {
    return {
        stable: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-stable.svg'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(0,200,0,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        drafting: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-drafting.svg'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(107,159,212,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        strained: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-strained.svg'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(255,140,0,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        severed: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-severed.svg'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(220,0,0,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
        }),
        deprecated: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-deprecated.svg'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(120,120,120,0.6)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            isWholeLine: true,
            opacity: '0.6',
        }),
        stub: vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-stub.svg'),
            gutterIconSize: 'contain',
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
        introHunk: vscode.window.createTextEditorDecorationType({
            backgroundColor: 'rgba(60,180,80,0.10)',
            before: { contentText: '✓ ', color: new vscode.ThemeColor('diffEditor.insertedTextBackground') },
            isWholeLine: true,
        }),
        retireHunk: vscode.window.createTextEditorDecorationType({
            backgroundColor: 'rgba(200,80,80,0.10)',
            before: { contentText: '✗ ' },
            isWholeLine: true,
        }),
        amendHunk: vscode.window.createTextEditorDecorationType({
            backgroundColor: 'rgba(80,120,200,0.10)',
            before: { contentText: '✎ ' },
            isWholeLine: true,
        }),
    };
}

// State badge regex — kept for backward compat with rendered files that still carry [State]
const STATE_RE = /\[([^\]]+)\]/;

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
    const introHunk: vscode.Range[] = [];
    const retireHunk: vscode.Range[] = [];
    const amendHunk: vscode.Range[] = [];

    for (let i = 0; i < editor.document.lineCount; i++) {
        const text = editor.document.lineAt(i).text;
        const range = new vscode.Range(i, 0, i, text.length);

        // Proposal / retired prefix decorations
        if (PROPOSAL_PREFIX_RE.test(text)) proposal.push(range);
        if (RETIRED_PREFIX_RE.test(text)) retired.push(range);

        // Diff hunk decorations
        if (text.startsWith(DIFF_INTRO_PREFIX)) {
            introHunk.push(range);
        } else if (text.startsWith(DIFF_RETIRE_PREFIX)) {
            retireHunk.push(range);
        } else if (text.startsWith(DIFF_AMEND_PREFIX)) {
            amendHunk.push(range);
        }

        // State badge gutter icons
        const m = STATE_RE.exec(text);
        if (!m) continue;
        const state = m[1].toLowerCase();
        switch (state) {
            case 'stable':     stable.push(range);     break;
            case 'drafting':   drafting.push(range);   break;
            case 'strained':   strained.push(range);   break;
            case 'severed':    severed.push(range);     break;
            case 'deprecated': deprecated.push(range); break;
            case 'stub':       stub.push(range);       break;
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
    editor.setDecorations(dec.introHunk, introHunk);
    editor.setDecorations(dec.retireHunk, retireHunk);
    editor.setDecorations(dec.amendHunk, amendHunk);
}
