// Drives the logger against a stubbed VS Code and checks what it writes.
//
// The measures in analysis-plan.md are computed from these lines, so this asserts
// the shape they need: a focus event per surface change, a view event carrying the
// line range and how long it was on screen, and an edit event that says whether
// the person typed it or a file changed underneath them.
//
//   node test-extension.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

// Honours CODOC_STUDY_LOG_OUT so the same run can double as a sample session
// for check-session-complete.py.
const LOG = process.env.CODOC_STUDY_LOG_OUT ||
    path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'logger-test-')), 'out.jsonl');
fs.mkdirSync(path.dirname(LOG), { recursive: true });
const ROOT = '/ws/ember';

// ── the stub ────────────────────────────────────────────────────────────────
const handlers = {};
const on = (name) => (fn) => { (handlers[name] = handlers[name] || []).push(fn); return { dispose() {} }; };
const fire = (name, arg) => (handlers[name] || []).forEach(fn => fn(arg));

let activeEditor = null;
let activeTab = null;

const vscode = {
    window: {
        createOutputChannel: () => ({ appendLine() {}, show() {} }),
        onDidChangeActiveTextEditor: on('editor'),
        onDidChangeTextEditorVisibleRanges: on('ranges'),
        onDidChangeWindowState: on('window'),
        tabGroups: {
            onDidChangeTabs: on('tabs'),
            get activeTabGroup() { return { activeTab }; },
        },
        get activeTextEditor() { return activeEditor; },
        // onDidStartTerminalShellExecution deliberately absent: it is optional,
        // and the logger must work on a VS Code that does not have it.
    },
    workspace: {
        workspaceFolders: [{ uri: { fsPath: ROOT } }],
        getConfiguration: () => ({ get: (k) => (k === 'file' ? LOG : k === 'participant' ? 'p04' : '') }),
        onDidChangeTextDocument: on('change'),
        onDidSaveTextDocument: on('save'),
    },
    commands: { registerCommand: () => ({ dispose() {} }) },
};

const realLoad = Module._load;
Module._load = function (request, parent, isMain) {
    if (request === 'vscode') return vscode;
    return realLoad.apply(this, arguments);
};

const ext = require('./extension');
Module._load = realLoad;

// ── helpers ─────────────────────────────────────────────────────────────────
const uri = (p) => ({ fsPath: path.join(ROOT, p), scheme: 'file' });
const range = (a, b) => ({ start: { line: a }, end: { line: b } });
const editorFor = (p, ranges) => ({ document: { uri: uri(p) }, visibleRanges: ranges || [range(0, 40)] });
const lines = () => fs.readFileSync(LOG, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);

function sleep(ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) { /* the logger measures wall clock, so really wait */ }
}

// ── drive a session ─────────────────────────────────────────────────────────
ext.activate({ subscriptions: [] });

// Open a source file and scroll down it.
activeEditor = editorFor('ember/digest.py');
fire('editor', activeEditor);
fire('ranges', { textEditor: activeEditor, visibleRanges: [range(80, 120)] });
sleep(1100);

// Type into it.
fire('change', {
    document: { uri: uri('ember/digest.py') },
    contentChanges: [{ text: 'hello', rangeLength: 0 }],
});

// Switch to the written description.
activeEditor = editorFor('.codoc/tree.codoc');
fire('editor', activeEditor);
sleep(1100);

// A file changes underneath us while a different editor is active: an agent
// edit, and it must be distinguishable from the typing above.
fire('change', {
    document: { uri: uri('ember/archive.py') },
    contentChanges: [{ text: 'x'.repeat(200), rangeLength: 12 }],
});

// Save, then lose the window.
fire('save', { uri: uri('.codoc/tree.codoc') });
fire('window', { focused: false });

const rows = lines();

// ── assertions ──────────────────────────────────────────────────────────────
const kinds = rows.map(r => r.ev);
assert.ok(kinds.includes('session'), 'a session start is recorded');
assert.ok(rows.every(r => r.p === 'p04' && r.ws === 'ember'),
    'every line carries the participant and the workspace');

const focus = rows.filter(r => r.ev === 'focus');
assert.deepStrictEqual(focus.map(f => f.surface), ['code', 'document'],
    'a focus event per surface change, in order');
assert.strictEqual(focus[0].file, 'ember/digest.py', 'paths are relative to the project');

// The view event is what review coverage is computed from, so it must carry the
// range that was scrolled through and a duration.
const view = rows.find(r => r.ev === 'view');
assert.ok(view, 'leaving a file records what was on screen');
assert.strictEqual(view.file, 'ember/digest.py');
assert.ok(view.from <= 0 && view.to >= 120, `scrolled range is kept: got ${view.from}..${view.to}`);
assert.ok(view.ms >= 1000, `duration is recorded: got ${view.ms}`);

// Telling a person's typing from an agent's rewrite is what the origin-of-change
// measure rests on.
const edits = rows.filter(r => r.ev === 'edit');
assert.strictEqual(edits.length, 2, 'both edits recorded');
const typed = edits.find(e => e.file === 'ember/digest.py');
const agentish = edits.find(e => e.file === 'ember/archive.py');
assert.strictEqual(typed.active, true, 'typing happens in the active editor');
assert.strictEqual(typed.added, 5);
assert.strictEqual(agentish.active, false, 'a file changing underneath is not the active editor');
assert.strictEqual(agentish.added, 200);
assert.strictEqual(agentish.removed, 12);

// No file contents anywhere.
const blob = fs.readFileSync(LOG, 'utf8');
assert.ok(!blob.includes('hello'), 'the text of an edit is never written');
assert.ok(!blob.includes('x'.repeat(20)), 'the text of an edit is never written');

assert.ok(rows.some(r => r.ev === 'save'), 'saves are recorded');
assert.ok(rows.some(r => r.ev === 'window' && r.focused === false), 'losing focus is recorded');

console.log(`study logger: ${rows.length} events, all ${13} assertions pass`);
