// codoc study logger — records what a participant opens, looks at, and edits.
//
// The same extension runs in BOTH conditions. That is the whole point: if the
// codoc condition logged navigation and the other one did not, every navigation
// measure would be a property of the tool rather than of the person, and the two
// could not be compared. One extension, one schema, both conditions.
//
// It records paths, line numbers and character counts. It never records the text
// of a file, a prompt, or a description. What the participant typed lives in the
// Claude Code transcript and in the saved copies of the project, both of which
// they are told about.
//
// One line of JSON per event, appended:
//
//   {"t":1786653982848,"p":"p04","ws":"hearth","ev":"focus","surface":"code",
//    "file":"hearth/build.py"}
//
// Events:
//   session  the log started or the window closed. Carries the workspace name.
//   focus    the active surface changed. surface is "document" (the codoc tree
//            or CLAUDE.md), "code", "test", or "other".
//   view     a file left the screen after being visible. Carries the line range
//            that was on screen and how long, in ms. This is what "did they
//            actually look at the change" is computed from.
//   edit     a document changed. Carries characters added and removed, whether
//            the window had focus, and whether that editor was the active one,
//            which is how a person's own typing is told from a file an agent
//            rewrote underneath them.
//   save     a document was saved.
//   agent    a command started in a terminal. Present only on VS Code versions
//            that expose shell integration; the Claude Code transcript is the
//            authoritative record of agent turns either way.
//   window   the whole window gained or lost focus, so time away is not counted
//            as time spent looking.
const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { surfaceOf, relativeTo } = require('./classify');

let out = null;          // absolute path of the log file
let participant = '';
let workspaceName = '';
let rootDir = '';
let channel = null;

// The file currently on screen, and since when.
let current = null;      // { file, surface, since, lo, hi }
let windowFocused = true;

function rel(uri) {
    if (!uri) return '';
    return relativeTo(rootDir, uri.fsPath || String(uri));
}

function write(ev, extra) {
    if (!out) return;
    const line = Object.assign(
        { t: Date.now(), p: participant, ws: workspaceName, ev }, extra || {});
    try {
        fs.appendFileSync(out, JSON.stringify(line) + '\n');
    } catch (err) {
        // A logger must never interrupt a session. Surface it in the channel and
        // carry on; a missing line is recoverable, a modal dialog mid-task is not.
        if (channel) channel.appendLine(`could not write: ${err && err.message}`);
    }
}

/** Close off the file that was on screen, recording how long it was looked at. */
function closeCurrent(now) {
    if (!current) return;
    const ms = now - current.since;
    // Anything under a second is a flick past on the way somewhere else. Keeping
    // those would inflate "files opened" without meaning anyone read them.
    if (ms >= 1000) {
        write('view', {
            file: current.file, surface: current.surface,
            from: current.lo, to: current.hi, ms,
        });
    }
    current = null;
}

function openCurrent(file, surface, ranges) {
    const now = Date.now();
    closeCurrent(now);
    if (!file) return;
    let lo = 0, hi = 0;
    if (ranges && ranges.length) {
        lo = Math.min.apply(null, ranges.map(r => r.start.line));
        hi = Math.max.apply(null, ranges.map(r => r.end.line));
    }
    current = { file, surface, since: now, lo, hi };
    write('focus', { surface, file });
}

function activate(context) {
    channel = vscode.window.createOutputChannel('codoc study logger');

    const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
    rootDir = folder ? folder.uri.fsPath : '';
    workspaceName = folder ? path.basename(rootDir) : 'no-folder';

    const cfg = vscode.workspace.getConfiguration('codocStudyLogger');
    participant = cfg.get('participant') || process.env.CODOC_STUDY_PARTICIPANT || '';
    out = cfg.get('file') || process.env.CODOC_STUDY_LOG || '';
    if (!out) {
        const dir = path.join(os.homedir(), 'codoc-study', 'session-logs');
        try { fs.mkdirSync(dir, { recursive: true }); } catch (e) { /* best effort */ }
        out = path.join(dir, `interaction-${workspaceName}.jsonl`);
    }
    channel.appendLine(`logging to ${out}`);
    write('session', { start: true, version: '1.0.0' });

    const sub = context.subscriptions;

    // Send a copy upward while the session runs, if a code was configured. The
    // local file is the source of truth and does not depend on any of this
    // working, so every failure here is reported and then ignored.
    activation.mirrorReady = startMirror(cfg, sub);

    // The active editor. Covers ordinary files.
    sub.push(vscode.window.onDidChangeActiveTextEditor(ed => {
        if (!ed) { closeCurrent(Date.now()); return; }
        const file = rel(ed.document.uri);
        openCurrent(file, surfaceOf(file), ed.visibleRanges);
    }));

    // Tabs. This is what catches the codoc tree, because a custom editor is a tab
    // and not a text editor, so the handler above never sees it.
    sub.push(vscode.window.tabGroups.onDidChangeTabs(() => {
        const tab = vscode.window.tabGroups.activeTabGroup &&
            vscode.window.tabGroups.activeTabGroup.activeTab;
        if (!tab || !tab.input) return;
        const uri = tab.input.uri || (tab.input.modified) || null;
        if (!uri) return;
        const file = rel(uri);
        if (current && current.file === file) return;
        openCurrent(file, surfaceOf(file), null);
    }));

    // Scrolling. Widen the range we know was on screen for the current file.
    sub.push(vscode.window.onDidChangeTextEditorVisibleRanges(e => {
        if (!current || !e.visibleRanges.length) return;
        if (rel(e.textEditor.document.uri) !== current.file) return;
        current.lo = Math.min(current.lo, Math.min.apply(null, e.visibleRanges.map(r => r.start.line)));
        current.hi = Math.max(current.hi, Math.max.apply(null, e.visibleRanges.map(r => r.end.line)));
    }));

    // Edits. Counts only, never the text.
    sub.push(vscode.workspace.onDidChangeTextDocument(e => {
        if (e.document.uri.scheme !== 'file' || !e.contentChanges.length) return;
        let added = 0, removed = 0;
        for (const c of e.contentChanges) { added += c.text.length; removed += c.rangeLength; }
        const file = rel(e.document.uri);
        const active = vscode.window.activeTextEditor &&
            vscode.window.activeTextEditor.document.uri.fsPath === e.document.uri.fsPath;
        write('edit', {
            file, surface: surfaceOf(file), added, removed,
            focused: windowFocused, active: !!active,
        });
    }));

    sub.push(vscode.workspace.onDidSaveTextDocument(doc => {
        if (doc.uri.scheme !== 'file') return;
        const file = rel(doc.uri);
        write('save', { file, surface: surfaceOf(file) });
    }));

    // Window focus. Time spent in another application is not time spent reading.
    sub.push(vscode.window.onDidChangeWindowState(s => {
        windowFocused = s.focused;
        if (!s.focused) closeCurrent(Date.now());
        write('window', { focused: s.focused });
    }));

    // Commands run in a terminal, where that is available. Optional on purpose:
    // shell integration is a recent addition and the transcript is the real
    // record of agent turns, so this is corroboration and not the measure.
    if (vscode.window.onDidStartTerminalShellExecution) {
        sub.push(vscode.window.onDidStartTerminalShellExecution(e => {
            const line = (e.execution && e.execution.commandLine &&
                e.execution.commandLine.value) || '';
            write('agent', { cmd: line.split(/\s+/)[0] || '', len: line.length });
        }));
    }

    sub.push(vscode.commands.registerCommand('codocStudyLogger.showLog', () => {
        channel.appendLine(`log file: ${out}`);
        channel.appendLine('Recorded: file paths, line numbers on screen, how long,');
        channel.appendLine('characters added and removed. Never file or prompt text.');
        channel.show();
    }));

    sub.push({ dispose: () => { closeCurrent(Date.now()); write('session', { start: false }); } });
}

let mirror = null;
const activation = { mirrorReady: null };

/**
 * Load the mirror and set it going. Returns a promise so a test can wait for it.
 *
 * The mirror and everything it uses are ES modules, because the web apps import
 * them too. This file is CommonJS, because that is what a VS Code extension host
 * loads. `require` of an ES module works on new Node and throws on the older one
 * VS Code actually bundles, so it is loaded with a dynamic import, which works on
 * both. Getting this wrong fails quietly: the mirror simply never starts, and the
 * session looks fine right up until nothing arrives.
 */
async function startMirror(cfg, sub) {
    const code = cfg.get('participant') || process.env.CODOC_STUDY_PARTICIPANT || '';
    if (!code) { channel.appendLine('no participant code set, so not mirroring'); return null; }
    let mod;
    try {
        mod = await import('./mirror.js');
    } catch (err) {
        channel.appendLine(`mirror could not be loaded: ${err && err.message}`);
        return null;
    }
    const condition = cfg.get('condition')
        || (workspaceName.includes('baseline') ? 'baseline' : 'codoc');
    mirror = new mod.Mirror({
        logPath: out, code, condition,
        config: mod.FIREBASE_CONFIG,
        onError: (m) => channel.appendLine(m),
    });
    sub.push({ dispose: () => { if (mirror) void mirror.stop(); } });
    const ok = await mirror.start();
    channel.appendLine(ok ? `mirroring ${code} (${condition})` : 'not mirroring yet, will keep trying');
    return mirror;
}

function deactivate() {
    closeCurrent(Date.now());
    write('session', { start: false });
}

module.exports = { activate, deactivate, activation };
