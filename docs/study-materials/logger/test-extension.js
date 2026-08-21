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
const ROOT = '/ws/tally';

// ── the stub ────────────────────────────────────────────────────────────────
const handlers = {};
const on = (name) => (fn) => { (handlers[name] = handlers[name] || []).push(fn); return { dispose() {} }; };
const fire = (name, arg) => (handlers[name] || []).forEach(fn => fn(arg));

let activeEditor = null;
let activeTab = null;

const vscode = {
    StatusBarAlignment: { Left: 1, Right: 2 },
    Uri: { parse: (u) => ({ href: u }) },
    env: { openExternal(uri) { opened.push(uri.href); return Promise.resolve(true); } },
    window: {
        createOutputChannel: () => ({ appendLine() {}, show() {}, dispose() {} }),
        // The visible sign that recording is on. Stubbed because the real thing is
        // the only place a participant, or a researcher watching their screen, can
        // see whether this window is being recorded at all.
        createStatusBarItem: () => ({ text: '', tooltip: '', command: '', show() {}, dispose() {} }),
        showInformationMessage: (...args) => { offers.push(args[0]); return Promise.resolve(undefined); },
        showWarningMessage: () => Promise.resolve(undefined),
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
        // Both the code and the condition, because setup writes both into the study
        // project's own settings.json and the mirror now needs both to send.
        getConfiguration: () => ({
            get: (k) => (k === 'file' ? LOG
                : k === 'participant' ? 'p04'
                : k === 'condition' ? 'codoc' : ''),
        }),
        onDidChangeTextDocument: on('change'),
        onDidSaveTextDocument: on('save'),
        createFileSystemWatcher: () => ({
            onDidCreate: on('askCreate'),
            onDidChange: on('askChange'),
            onDidDelete: on('askDelete'),
            dispose() {},
        }),
    },
    commands: {
        registerCommand: (id, fn) => { commands.set(id, fn); return { dispose() {} }; },
    },
};

const opened = [];
const offers = [];
const commands = new Map();
const globalState = new Map();

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
ext.activate({
    subscriptions: [],
    globalState: { get: (k) => globalState.get(k), update: (k, v) => { globalState.set(k, v); } },
});

// Open a source file and scroll down it.
activeEditor = editorFor('tally/digest.py');
fire('editor', activeEditor);
fire('ranges', { textEditor: activeEditor, visibleRanges: [range(80, 120)] });
sleep(1100);

// Type into it.
fire('change', {
    document: { uri: uri('tally/digest.py') },
    contentChanges: [{ text: 'hello', rangeLength: 0 }],
});

// Switch to the written description.
activeEditor = editorFor('.codoc/tree.codoc');
fire('editor', activeEditor);
sleep(1100);

// A file changes underneath us while a different editor is active: an agent
// edit, and it must be distinguishable from the typing above.
fire('change', {
    document: { uri: uri('tally/archive.py') },
    contentChanges: [{ text: 'x'.repeat(200), rangeLength: 12 }],
});

// A /codoc:ask walkthrough is drawn — .codoc/ask.json appears with three stops.
// The watcher reads it to count them (a real file, since the extension reads it).
const askFile = path.join(path.dirname(LOG), 'ask.json');
fs.writeFileSync(askFile, JSON.stringify({
    version: 1, id: 'ask-99', question: 'WALKTHROUGH_QUESTION_TEXT', answer: 'because',
    steps: [{ feature_id: 'f-1' }, { feature_id: 'f-2' }, { feature_id: 'f-3' }],
}));
fire('askCreate', { fsPath: askFile, scheme: 'file' });
// Re-observing the SAME walkthrough (a double-fired watcher, or a re-render) must
// not count twice — the dedupe is on the walkthrough's own id.
fire('askChange', { fsPath: askFile, scheme: 'file' });

// Save, then lose the window.
fire('save', { uri: uri('.codoc/tree.codoc') });
fire('window', { focused: false });

const rows = lines();

// ── assertions ──────────────────────────────────────────────────────────────
const kinds = rows.map(r => r.ev);
assert.ok(kinds.includes('session'), 'a session start is recorded');
assert.ok(rows.every(r => r.p === 'p04' && r.ws === 'tally'),
    'every line carries the participant and the workspace');

const focus = rows.filter(r => r.ev === 'focus');
assert.deepStrictEqual(focus.map(f => f.surface), ['code', 'document'],
    'a focus event per surface change, in order');
assert.strictEqual(focus[0].file, 'tally/digest.py', 'paths are relative to the project');

// The view event is what review coverage is computed from, so it must carry the
// range that was scrolled through and a duration.
const view = rows.find(r => r.ev === 'view');
assert.ok(view, 'leaving a file records what was on screen');
assert.strictEqual(view.file, 'tally/digest.py');
assert.ok(view.from <= 0 && view.to >= 120, `scrolled range is kept: got ${view.from}..${view.to}`);
assert.ok(view.ms >= 1000, `duration is recorded: got ${view.ms}`);

// Telling a person's typing from an agent's rewrite is what the origin-of-change
// measure rests on.
const edits = rows.filter(r => r.ev === 'edit');
assert.strictEqual(edits.length, 2, 'both edits recorded');
const typed = edits.find(e => e.file === 'tally/digest.py');
const agentish = edits.find(e => e.file === 'tally/archive.py');
assert.strictEqual(typed.active, true, 'typing happens in the active editor');
assert.strictEqual(typed.added, 5);
assert.strictEqual(agentish.active, false, 'a file changing underneath is not the active editor');
assert.strictEqual(agentish.added, 200);
assert.strictEqual(agentish.removed, 12);

// A /codoc:ask walkthrough is recorded once, with its stop count and not its text.
const asks = rows.filter(r => r.ev === 'ask');
assert.strictEqual(asks.length, 1, 'one ASK per walkthrough, deduped on its id');
assert.strictEqual(asks[0].steps, 3, 'the number of stops is recorded');

// No file contents anywhere.
const blob = fs.readFileSync(LOG, 'utf8');
assert.ok(!blob.includes('hello'), 'the text of an edit is never written');
assert.ok(!blob.includes('x'.repeat(20)), 'the text of an edit is never written');
assert.ok(!blob.includes('WALKTHROUGH_QUESTION_TEXT'), 'the question behind an ask is never written');

assert.ok(rows.some(r => r.ev === 'save'), 'saves are recorded');
assert.ok(rows.some(r => r.ev === 'window' && r.focused === false), 'losing focus is recorded');

// The snapshot recorder starts itself. Here the workspace is a stub path that is
// not on disk, so it declines quietly — a logger must never fail an activation.
// What it does on a real workspace is test-snapshot.js.
assert.strictEqual(ext.activation.snapshots, null,
    'a workspace that is not on disk is declined, not thrown at');

// The mirror is an ES module and this file is loaded as CommonJS, which is what
// the extension host does. Loading it the wrong way fails quietly and the session
// looks healthy while nothing is sent, so assert that it really loads.
// Opening the study page. The workspace here is 'tally', which is a study
// project, and a code is configured, so the offer should have fired exactly once.
assert.equal(offers.length, 1, 'the page is offered');
assert.match(offers[0], /study page/i);
assert.ok(commands.has('codocStudyLogger.openStudyPage'), 'and there is a command to reopen it');

commands.get('codocStudyLogger.openStudyPage')();
assert.equal(opened.length, 1, 'the command opens it');
assert.match(opened[0], /[?&]code=p04\b/, 'carrying the participant code');

(async () => {
    const m = await ext.activation.mirrorReady;
    assert.ok(m, 'the mirror module loaded and a mirror was constructed');
    assert.equal(m.code, 'p04');
    // Read from the setting, which is the only place it lives. The folders are
    // named for the project alone, so there is no workspace name to guess it from,
    // and an unset condition now stops the mirror rather than defaulting to this
    // one. The old assertion passed on that default and read as if it had checked
    // something.
    assert.equal(m.condition, 'codoc', 'the condition comes through from the setting');
    await m.stop();
    console.log(`study logger: ${rows.length} events, all assertions pass`);
})().catch((err) => { console.error(err); process.exit(1); });
