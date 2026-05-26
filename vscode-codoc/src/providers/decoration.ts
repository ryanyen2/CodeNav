import * as vscode from 'vscode';
import { ParsedFeature } from '../state/workspace-state';

// Identity / event markers ⟨f-…⟩ ⟨e-…⟩ (plus the two spaces before them) are
// collapsed to nothing — the human never sees or types an id.
const HIDDEN_ID_RE = /\s*⟨(?:f|e)-[0-9a-f]+⟩/g;
// A proposal diff hunk: col-0 op char, space, a feature marker, space.
const DIFF_HUNK_RE = /^([+\-~]) [-~] /;
// A retired *live* feature line: '~ Title' (3rd char is a letter, not a marker).
const RETIRED_RE = /^\s*~\s+\S/;
// A live feature title line: indent + marker (- or ~) + space + word char.
const FEATURE_RE = /^(\s*)[-~]\s+\S/;

export interface CodocDecorations {
    hiddenId: vscode.TextEditorDecorationType;
    addHunk: vscode.TextEditorDecorationType;
    retireHunk: vscode.TextEditorDecorationType;
    moveHunk: vscode.TextEditorDecorationType;
    retired: vscode.TextEditorDecorationType;
    activeFeature: vscode.TextEditorDecorationType;
    dimmed: vscode.TextEditorDecorationType;
    nodeGlyphParent: vscode.TextEditorDecorationType;
    nodeGlyphLeaf: vscode.TextEditorDecorationType;
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
            borderColor: 'rgba(60,180,80,0.7)',
            borderWidth: '0 0 0 2px',
            borderStyle: 'solid',
        }),
        retireHunk: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: 'rgba(200,80,80,0.10)',
            overviewRulerColor: 'rgba(200,80,80,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            borderColor: 'rgba(200,80,80,0.7)',
            borderWidth: '0 0 0 2px',
            borderStyle: 'solid',
        }),
        moveHunk: vscode.window.createTextEditorDecorationType({
            isWholeLine: true,
            backgroundColor: 'rgba(80,120,200,0.10)',
            overviewRulerColor: 'rgba(80,120,200,0.7)',
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            borderColor: 'rgba(80,120,200,0.7)',
            borderWidth: '0 0 0 2px',
            borderStyle: 'solid',
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
        dimmed: vscode.window.createTextEditorDecorationType({
            opacity: '0.45',
        }),
        nodeGlyphParent: vscode.window.createTextEditorDecorationType({
            before: {
                contentText: '▸ ',
                color: new vscode.ThemeColor('editorIndentGuide.activeBackground'),
                margin: '0 2px 0 0',
            },
        }),
        nodeGlyphLeaf: vscode.window.createTextEditorDecorationType({
            before: {
                contentText: '• ',
                color: new vscode.ThemeColor('editorGhostText.foreground'),
                margin: '0 2px 0 0',
            },
        }),
    };
}

export function applyDecorations(
    editor: vscode.TextEditor,
    dec: CodocDecorations,
    activeFeatureLines: number[] = [],
    features: ParsedFeature[] = [],
): void {
    if (editor.document.languageId !== 'codoc') return;
    const hiddenId: vscode.Range[] = [];
    const addHunk: vscode.Range[] = [];
    const retireHunk: vscode.Range[] = [];
    const moveHunk: vscode.Range[] = [];
    const retired: vscode.Range[] = [];
    const nodeParent: vscode.Range[] = [];
    const nodeLeaf: vscode.Range[] = [];

    // Set of ids that are parents (own at least one child).
    const parentIds = new Set(
        features.map(f => f.parent_id).filter((id): id is string => id !== null)
    );
    const featureByLine = new Map(features.map(f => [f.line, f]));

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

        // Node glyph: prepend ▸ (parent) or • (leaf) to each feature title line.
        if (FEATURE_RE.test(text)) {
            const feat = featureByLine.get(i);
            const range = new vscode.Range(i, 0, i, 0);
            if (feat?.id && parentIds.has(feat.id)) nodeParent.push(range);
            else nodeLeaf.push(range);
        }
    }

    editor.setDecorations(dec.hiddenId, hiddenId);
    editor.setDecorations(dec.addHunk, addHunk);
    editor.setDecorations(dec.retireHunk, retireHunk);
    editor.setDecorations(dec.moveHunk, moveHunk);
    editor.setDecorations(dec.retired, retired);
    editor.setDecorations(dec.nodeGlyphParent, nodeParent);
    editor.setDecorations(dec.nodeGlyphLeaf, nodeLeaf);

    const activeRanges = activeFeatureLines.map(
        line => new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER)
    );
    editor.setDecorations(dec.activeFeature, activeRanges);
}
