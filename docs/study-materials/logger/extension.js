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
//   snapshot the 20-second recorder started (see snapshot.js). Written once, as
//            proof it is running, because the way this used to fail was silently.
const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { surfaceOf, relativeTo } = require('./classify');
const { Snapshotter, safeLabel } = require('./snapshot');

let out = null;          // absolute path of the log file
let participant = '';
let workspaceName = '';
let rootDir = '';
let channel = null;
let status = null;       // the one visible sign that recording is on

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

/**
 * Say on screen whether this window is being recorded.
 *
 * The logger used to report itself only on an output channel and only to the
 * dashboard, so a window that was recording nothing looked exactly like one that
 * was, both to the participant and to the researcher watching over their
 * shoulder. Two participants finished a condition that way. A status bar item is
 * the cheapest place a person actually looks, it reads the same in both arms so
 * it changes nothing about the comparison, and clicking it opens the channel that
 * says what is being recorded.
 */
function showState(context, recording) {
    if (!status) {
        status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
        status.command = 'codocStudyLogger.showLog';
        context.subscriptions.push(status);
    }
    status.text = recording ? '$(record) codoc study' : '$(circle-slash) codoc study: off';
    status.tooltip = recording
        ? 'This window is being recorded for the study. Click to see what is recorded.'
        : 'No participant code is set in this window, so nothing here is recorded. '
          + 'If setup has just run, this changes on its own.';
    status.show();
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
        showState(context, false);
        // Start the moment a code appears, rather than at the next window reload.
        //
        // Setup writes the code into the study project's own settings.json, and it
        // is ordinarily run from a terminal inside VS Code, so the window that ends
        // up holding the session is often already open when the code arrives. The
        // setting was read once at startup and this branch then returned for good,
        // which left a participant working in a window that recorded nothing, with
        // the reason on an output channel nobody has open. Two codes came back with
        // no sessions on them before anybody noticed, and by then the only place it
        // showed was the dashboard.
        const placeholders = [
            vscode.commands.registerCommand('codocStudyLogger.showLog', () => {
                channel.appendLine(why);
                channel.show();
            }),
            vscode.commands.registerCommand('codocStudyLogger.openStudyPage', () => {
                void openStudyPage(cfg, true);
            }),
            vscode.workspace.onDidChangeConfiguration((e) => {
                if (!e.affectsConfiguration('codocStudyLogger.participant')) return;
                const set = vscode.workspace.getConfiguration('codocStudyLogger')
                    .get('participant');
                if (!set) return;
                // Hand the channel and both commands back before activating again,
                // because registering a command twice under one name throws and the
                // second activation would die halfway through.
                for (const d of placeholders) d.dispose();
                if (status) { status.dispose(); status = null; }
                channel.dispose();
                channel = null;
                activate(context);
            }),
        ];
        context.subscriptions.push(...placeholders);
        return;
    }
    showState(context, true);

    out = cfg.get('file') || process.env.CODOC_STUDY_LOG || '';
    if (!out) {
        const dir = path.join(os.homedir(), 'codoc-study', 'session-logs');
        try { fs.mkdirSync(dir, { recursive: true }); } catch (e) { /* best effort */ }
        out = path.join(dir, `interaction-${workspaceName}.jsonl`);
    }
    channel.appendLine(`logging to ${out}`);
    write('session', { start: true, version: '1.1.1' });

    const sub = context.subscriptions;

    // The 20-second snapshots. This used to be a script somebody had to start by
    // hand in its own terminal, at the start of each condition, and on the first
    // pilot nobody did — the session looked completely normal and there is no
    // replay of it. Nothing about it needed a person: the extension is already
    // running, in both conditions, and already knows the code and the workspace.
    startSnapshots(cfg, sub);

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
        const st = snapshots && snapshots.status();
        if (!st) {
            channel.appendLine('Snapshots: off.');
        } else if (st.failed) {
            channel.appendLine(`Snapshots: FAILING (${st.failed}). Tell the experimenter.`);
        } else {
            channel.appendLine(`Snapshots: ${st.snapshots} taken, `
                + `${st.copies} copies of the description, into ${st.dir}`);
        }
        channel.show();
    }));

    sub.push({ dispose: () => { closeCurrent(Date.now()); write('session', { start: false }); } });
}

let mirror = null;
let snapshots = null;
const activation = { mirrorReady: null, snapshots: null };

/**
 * Record the workspace every 20 seconds, for the whole session.
 *
 * It is deliberately started from here rather than asked of anybody. See
 * snapshot.js for what a snapshot is and why it never touches their branch.
 */
function startSnapshots(cfg, sub) {
    if (cfg.get('snapshots') === false) { channel.appendLine('snapshots are off'); return null; }
    const every = Number(cfg.get('snapshotEverySeconds')) || 20;
    snapshots = new Snapshotter({
        repo: rootDir,
        // Both parts are folded through safeLabel: a participant code arrives from
        // a settings file, and a path component is not the place to find out it
        // had a slash in it.
        dir: path.join(os.homedir(), 'codoc-study', 'session-logs', 'snapshots',
                       safeLabel(participant), safeLabel(workspaceName)),
        label: `${safeLabel(participant)}-${safeLabel(workspaceName)}`,
        everyMs: Math.max(5, every) * 1000,
        log: (m) => channel.appendLine(m),
        onEvent: (e) => write(e.ev, e),
    });
    const started = snapshots.start();
    if (!started) { snapshots = null; }
    activation.snapshots = snapshots;
    sub.push({ dispose: () => { if (snapshots) snapshots.stop(); } });
    return snapshots;
}

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
    if (snapshots) { snapshots.stop(); snapshots = null; }
    write('session', { start: false });
}

module.exports = { activate, deactivate, activation };
