// The logger records the study's projects and nothing else.
//
// It is installed globally and days before the session, so it activates in every
// window the participant opens in the meantime. It used to write a log for each
// of them: their own repos, under an empty participant code, into the folder
// collect.sh sweeps, and therefore into the zip they mail back. Their consent
// covers the study projects.
//
// Separate from test-extension.js because both drive activate() once at module
// scope and require() caches the module, so the two states cannot share a file.
//
//   node test-scope.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'logger-scope-'));
const LOG = path.join(DIR, 'out.jsonl');
const ROOT = '/Users/someone/work/their-own-repo';

const handlers = {};
const on = (name) => (fn) => { (handlers[name] = handlers[name] || []).push(fn); return { dispose() {} }; };
const fire = (name, arg) => (handlers[name] || []).forEach((fn) => fn(arg));

let activeEditor = null;
const said = [];
const commands = new Map();

const vscode = {
    Uri: { parse: (u) => ({ href: u }) },
    env: { openExternal: () => Promise.resolve(true) },
    window: {
        createOutputChannel: () => ({ appendLine: (m) => said.push(m), show() {} }),
        showInformationMessage: () => Promise.resolve(undefined),
        showWarningMessage: (m) => { said.push(m); return Promise.resolve(undefined); },
        onDidChangeActiveTextEditor: on('editor'),
        onDidChangeTextEditorVisibleRanges: on('ranges'),
        onDidChangeWindowState: on('window'),
        tabGroups: { onDidChangeTabs: on('tabs'), get activeTabGroup() { return { activeTab: null }; } },
        get activeTextEditor() { return activeEditor; },
    },
    workspace: {
        workspaceFolders: [{ uri: { fsPath: ROOT } }],
        // No participant code, which is what every workspace outside the study
        // looks like: setup writes the code into each study project's own
        // .vscode/settings.json and nowhere else.
        getConfiguration: () => ({ get: (k) => (k === 'file' ? LOG : '') }),
        onDidChangeTextDocument: on('change'),
        onDidSaveTextDocument: on('save'),
    },
    commands: {
        registerCommand: (id, fn) => { commands.set(id, fn); return { dispose() {} }; },
    },
};

const realLoad = Module._load;
Module._load = function (request) {
    if (request === 'vscode') return vscode;
    return realLoad.apply(this, arguments);
};
const ext = require('./extension');
Module._load = realLoad;

const uri = (p) => ({ fsPath: path.join(ROOT, p), scheme: 'file' });

const subscriptions = [];
ext.activate({
    subscriptions,
    globalState: { get: () => undefined, update: () => {} },
});

// Work in the window, the way somebody would on a Tuesday.
activeEditor = { document: { uri: uri('src/secret.py') }, visibleRanges: [{ start: { line: 0 }, end: { line: 40 } }] };
fire('editor', activeEditor);
fire('ranges', { textEditor: activeEditor, visibleRanges: [{ start: { line: 5 }, end: { line: 60 } }] });
fire('change', { document: { uri: uri('src/secret.py') }, contentChanges: [{ text: 'x', rangeLength: 0 }] });
fire('save', { uri: uri('src/secret.py') });
fire('window', { focused: false });
ext.deactivate();

assert.equal(ext.activation.logging, false, 'it knows it is not logging here');
assert.ok(!fs.existsSync(LOG),
    'no log file is created for a workspace with no participant code');

// Nothing about the file is anywhere in what it said, since a path in an output
// channel is still a path the participant did not agree to hand over.
const spoken = said.join('\n');
assert.ok(!spoken.includes('secret.py'), 'and no file path is even mentioned');

// The commands still answer. "Study logger: show what is being recorded" is how
// the researcher checks the logger before a task starts, and a command that does
// not exist reads as a broken extension rather than as a quiet one.
assert.ok(commands.has('codocStudyLogger.showLog'), 'showLog is still registered');
assert.ok(commands.has('codocStudyLogger.openStudyPage'), 'so is openStudyPage');
said.length = 0;
commands.get('codocStudyLogger.showLog')();
assert.match(said.join('\n'), /nothing here is recorded/i,
    'and it says why, rather than naming a file it is not writing');

fs.rmSync(DIR, { recursive: true, force: true });
console.log('ok  logger records only the study projects');
