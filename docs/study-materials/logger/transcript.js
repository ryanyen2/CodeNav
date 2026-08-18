// The agent's half of the session, read out of the Claude Code transcript.
//
// Why this exists. The interaction log is written by a VS Code extension, and a
// VS Code extension can only see files that are OPEN IN AN EDITOR. Measured on
// the first pilot: the agent touched 12 distinct files in each condition and the
// logger saw 1 and 2 of them. Every other read and every other edit — 11 of 12,
// in both arms — happened to a file nobody had open, and left no trace at all.
//
// That is not a bug in the logger; it is the boundary of what an editor can
// observe. The work has moved into the terminal. So "who wrote what", which is
// the first research question, cannot be answered from the editor log alone, and
// the analysis plan has always said agent actions come from the transcript. This
// is the code that gets them out.
//
// The output is RAW EVENTS in the same schema the logger writes, deliberately:
// `{t, p, ws, ev, …}`. That way one vocabulary (actions-vocab.js) maps both
// halves, and nothing downstream needs to know where an event came from.
//
// It is pure postprocessing over files already collected, so it applies to
// sessions that have already run.
//
//   node transcript.js <collected-folder> [--out merged]
//
const fs = require('fs');
const path = require('path');
const { surfaceOf, relativeTo } = require('./classify');

/** Tools that mean the agent LOOKED at a file. */
const READ_TOOLS = new Set(['Read', 'NotebookRead']);
/** Tools that mean the agent CHANGED a file. */
const WRITE_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit']);

/**
 * Parse one transcript file into raw events.
 *
 * `ws` is the workspace name (scribe / tally / …), which is also the condition
 * carrier everywhere else in the study, so it is stamped on every line exactly
 * as the extension stamps it.
 */
function eventsFromTranscript(text, { ws, participant = '', root = '' } = {}) {
    const out = [];
    for (const line of text.split('\n')) {
        const s = line.trim();
        if (!s) continue;
        let row;
        try { row = JSON.parse(s); } catch { continue; }
        const t = Date.parse(row.timestamp || '') || 0;
        if (!t) continue;

        // A human turn. The transcript is the only place a prompt's real shape
        // survives — the study's own hook records one too, and either is enough,
        // so these carry `via` and the merge keeps whichever arrived.
        if (row.type === 'user') {
            const c = row.message && row.message.content;
            const text = typeof c === 'string' ? c
                : Array.isArray(c) ? c.filter((b) => b && b.type === 'text').map((b) => b.text).join('\n')
                : '';
            // Tool RESULTS also arrive as user rows. They are not somebody typing.
            const isToolResult = Array.isArray(c) && c.some((b) => b && b.type === 'tool_result');
            if (text.trim() && !isToolResult) {
                out.push({
                    t, p: participant, ws, ev: 'prompt', via: 'transcript',
                    chars: text.length,
                    words: text.split(/\s+/).filter(Boolean).length,
                    lines: text.split('\n').length,
                });
            }
            continue;
        }
        if (row.type !== 'assistant') continue;

        for (const b of (row.message && row.message.content) || []) {
            if (!b || b.type !== 'tool_use') continue;
            const input = b.input || {};
            const fp = input.file_path || input.notebook_path || '';
            const file = fp ? relativeTo(root, fp) : '';

            if (READ_TOOLS.has(b.name) && file) {
                // The agent reading is not the participant reading, and the two
                // must never merge into one number — that is the whole of RQ1.
                // `ms` is what the `view` mapping expects; an agent read has no
                // duration, so it carries none and is filed at its own instant.
                out.push({ t, p: participant, ws, ev: 'agent-read', by: 'agent',
                           file, surface: surfaceOf(file), tool: b.name });
                continue;
            }
            if (WRITE_TOOLS.has(b.name) && file) {
                // `active:false, focused:false` is exactly how the extension marks
                // a file changing while nobody is typing in it, so the existing
                // vocabulary maps this to AGENT_EDIT / AGENT_DOC with no change.
                const s = (input.new_string ?? input.content ?? '');
                const old = (input.old_string ?? '');
                out.push({ t, p: participant, ws, ev: 'edit', by: 'agent',
                           file, surface: surfaceOf(file),
                           added: String(s).length, removed: String(old).length,
                           active: false, focused: false, tool: b.name });
                continue;
            }
            if (b.name === 'Bash') {
                const cmd = String(input.command || '').trim();
                if (cmd) {
                    out.push({ t, p: participant, ws, ev: 'agent', by: 'agent',
                               cmd: cmd.split(/\s+/)[0] || '', len: cmd.length });
                }
            }
        }
    }
    out.sort((a, b) => a.t - b.t);
    return out;
}

/**
 * Merge the agent's events with the editor's, and settle the one overlap.
 *
 * The editor reports an edit it did not cause whenever the changed file happens
 * to be open — the same edit the transcript already reports, better. So the rule
 * is: the TRANSCRIPT owns agent actions, the EDITOR owns human ones. An editor
 * edit with `active && focused` is somebody typing and is kept; any other editor
 * edit is an echo of the agent and is dropped, because the transcript has it with
 * the tool, the file and the exact text length.
 *
 * Everything else from the editor — focus, view, window, save, ask — is kept as
 * it is: the transcript cannot see any of it.
 */
function mergeEvents(editorEvents, agentEvents) {
    const kept = editorEvents.filter((e) => {
        if (e.ev !== 'edit') return true;
        return !!e.active && !!e.focused;   // a person typing
    });
    return [...kept, ...agentEvents].sort((a, b) => a.t - b.t);
}

/** Every transcript in a collected folder, by workspace name. */
function findTranscripts(root) {
    const base = path.join(root, 'claude-transcripts');
    if (!fs.existsSync(base)) return [];
    const out = [];
    for (const ws of fs.readdirSync(base)) {
        const dir = path.join(base, ws);
        if (!fs.statSync(dir).isDirectory()) continue;
        const stack = [dir];
        while (stack.length) {
            const d = stack.pop();
            for (const name of fs.readdirSync(d)) {
                const p = path.join(d, name);
                if (fs.statSync(p).isDirectory()) stack.push(p);
                else if (name.endsWith('.jsonl')) out.push({ ws, path: p });
            }
        }
    }
    return out;
}

module.exports = { eventsFromTranscript, mergeEvents, findTranscripts,
                   READ_TOOLS, WRITE_TOOLS };

if (require.main === module) {
    const root = process.argv[2];
    if (!root) {
        console.error('usage: node transcript.js <collected-folder>');
        process.exit(2);
    }
    const meta = (() => {
        try {
            const m = fs.readFileSync(path.join(root, 'collection.meta'), 'utf8');
            return (m.match(/participant:\s*(\S+)/) || [])[1] || '';
        } catch { return ''; }
    })();
    const byWs = new Map();
    for (const { ws, path: p } of findTranscripts(root)) {
        const evs = eventsFromTranscript(fs.readFileSync(p, 'utf8'),
                                         { ws, participant: meta, root: `/${ws}` });
        byWs.set(ws, [...(byWs.get(ws) || []), ...evs]);
    }
    const logs = path.join(root, 'session-logs');
    for (const [ws, agent] of byWs) {
        const logPath = path.join(logs, `interaction-${ws}.jsonl`);
        let editor = [];
        if (fs.existsSync(logPath)) {
            editor = fs.readFileSync(logPath, 'utf8').split('\n')
                .filter((l) => l.trim()).map((l) => { try { return JSON.parse(l); } catch { return null; } })
                .filter(Boolean);
        }
        const merged = mergeEvents(editor, agent);
        const out = path.join(logs, `merged-${ws}.jsonl`);
        fs.writeFileSync(out, merged.map((e) => JSON.stringify(e)).join('\n') + '\n');
        const agentEdits = agent.filter((e) => e.ev === 'edit').length;
        const agentReads = agent.filter((e) => e.ev === 'agent-read').length;
        console.log(`${ws}: ${editor.length} editor + ${agent.length} agent `
            + `(${agentReads} reads, ${agentEdits} edits) -> ${merged.length} merged`);
        console.log(`  ${out}`);
    }
}
