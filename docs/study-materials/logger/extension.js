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
//   {"t":1786653982848,"p":"p04","ws":"scribe","ev":"focus","surface":"code",
//    "file":"scribe/furniture.py"}
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
//   ask      a /codoc:ask walkthrough was drawn (.codoc/ask.json appeared).
//            Carries the number of stops, never the question. Only the codoc
//            condition produces the file, so this never appears in the baseline.
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

    // No code means this is not a study workspace, and nothing here is recorded.
    //
    // Setup writes the code into each study project's own .vscode/settings.json,
    // so a workspace without one is the participant's own work. This extension
    // is installed globally and days ahead of the session, so without this check
    // it recorded the file paths of every repo they opened in the meantime,
    // under an empty code, into the same folder collect.sh sweeps, and therefore
    // into the zip they email back. Their consent covers the study projects and
    // nothing else.
    //
    // The commands are still registered, because "show what is being recorded"
    // is how the researcher checks the logger before a task starts, and a
    // command that does not exist is a worse answer than one that says why.
    activation.logging = Boolean(participant);
    if (!participant) {
        const why = `No participant code is set in ${workspaceName || 'this window'}, `
            + 'so nothing here is recorded. Only the study projects are logged.';
        channel.appendLine(why);
        context.subscriptions.push(
            vscode.commands.registerCommand('codocStudyLogger.showLog', () => {
                channel.appendLine(why);
                channel.show();
            }),
            vscode.commands.registerCommand('codocStudyLogger.openStudyPage', () => {
                void openStudyPage(cfg, true);
            }));
        return;
    }

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

    // The /codoc:ask walkthrough. codoc writes .codoc/ask.json when the participant
    // asks a question and the agent draws the answer as a numbered reading path over
    // the tree; it is deleted on dismiss. The file exists in the codoc condition
    // ONLY — the baseline has no such surface — so the very same watcher run in both
    // arms produces ASK in one and nothing in the other, which is exactly why ASK is
    // codoc-only. We log that an ask happened and how many stops it drew, and dedupe
    // on the walkthrough's own id so a double-fired watcher or a re-render is one ASK.
    let lastAskId = '';
    const askWatcher = vscode.workspace.createFileSystemWatcher('**/.codoc/ask.json');
    const onAsk = uri => {
        let steps = 0, id = '';
        try {
            const j = JSON.parse(fs.readFileSync(uri.fsPath, 'utf8'));
            steps = Array.isArray(j.steps) ? j.steps.length : 0;
            id = typeof j.id === 'string' ? j.id : '';
        } catch (_e) { return; }               // mid-write or already gone — a later event catches it
        if (!steps || id === lastAskId) return; // no stops, or the same walkthrough re-observed
        lastAskId = id;
        write('ask', { steps });                // a count, never the question text
    };
    sub.push(askWatcher);
    sub.push(askWatcher.onDidCreate(onAsk));
    sub.push(askWatcher.onDidChange(onAsk));

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

    // Offer the study page once, when a study project is opened for the first
    // time. Nobody has to be sent a link, and nobody has to find one in an email
    // while a researcher waits.
    offerStudyPage(context, cfg);

    sub.push(vscode.commands.registerCommand('codocStudyLogger.openStudyPage', () => {
        void openStudyPage(cfg, true);
    }));

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

const STUDY_PROJECTS = ['scribe', 'tally'];

/** Whether this folder is one of the study's, so nothing is offered elsewhere. */
function isStudyProject(name) {
    return STUDY_PROJECTS.includes(name);
}

function studyPageUrl(cfg) {
    const base = cfg.get('studyPage') || 'https://codoc-11b10.web.app/participant/';
    const code = cfg.get('participant') || process.env.CODOC_STUDY_PARTICIPANT || '';
    if (!code) return null;
    const order = cfg.get('order') || '';
    const q = new URLSearchParams({ code });
    if (order) q.set('order', order);
    return `${base}?${q.toString()}`;
}

async function openStudyPage(cfg, explicit) {
    const url = studyPageUrl(cfg);
    if (!url) {
        const msg = 'No participant code is set, so there is no study page to open.';
        if (explicit) void vscode.window.showWarningMessage(msg);
        channel.appendLine(msg);
        return false;
    }
    await vscode.env.openExternal(vscode.Uri.parse(url));
    return true;
}

/**
 * Ask once per project, and remember the answer.
 *
 * Once, because a prompt that returns every time a window reopens is a prompt
 * people learn to dismiss without reading, and this one appears while somebody
 * is being watched on a call.
 */
function offerStudyPage(context, cfg) {
    if (!isStudyProject(workspaceName)) return;
    if (!studyPageUrl(cfg)) return;
    const key = `codocStudyLogger.offered:${workspaceName}`;
    if (context.globalState.get(key)) return;
    void context.globalState.update(key, true);
    void vscode.window.showInformationMessage(
        'Open your study page to carry on with the session.',
        'Open', 'Not now',
    ).then((pick) => {
        if (pick === 'Open') void openStudyPage(cfg, true);
    });
}

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
    // Set by setup, and the only thing on the machine that says which arm this
    // is: the folders are named for the project alone, so there is no longer a
    // folder name to fall back to guessing from.
    const condition = cfg.get('condition') || '';
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
