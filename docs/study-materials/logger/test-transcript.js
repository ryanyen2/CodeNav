// The agent's half of the session, and the one overlap with the editor's half.
//
//   node test-transcript.js
const assert = require('assert');
const { eventsFromTranscript, mergeEvents } = require('./transcript');

const line = (o) => JSON.stringify(o);

const transcript = [
    line({ type: 'user', timestamp: '2026-08-17T15:00:00.000Z',
           message: { content: 'support block quotes please' } }),
    line({ type: 'assistant', timestamp: '2026-08-17T15:00:10.000Z', message: { content: [
        { type: 'tool_use', name: 'Read', input: { file_path: '/scribe/scribe/blocks.py' } },
    ] } }),
    line({ type: 'assistant', timestamp: '2026-08-17T15:00:20.000Z', message: { content: [
        { type: 'tool_use', name: 'Edit', input: { file_path: '/scribe/scribe/blocks.py',
                                                   old_string: 'ab', new_string: 'abcde' } },
    ] } }),
    line({ type: 'assistant', timestamp: '2026-08-17T15:00:30.000Z', message: { content: [
        { type: 'tool_use', name: 'Edit', input: { file_path: '/scribe/CLAUDE.md',
                                                   old_string: '', new_string: 'x'.repeat(40) } },
    ] } }),
    line({ type: 'assistant', timestamp: '2026-08-17T15:00:40.000Z', message: { content: [
        { type: 'tool_use', name: 'Bash', input: { command: 'pytest tests/ -q' } },
    ] } }),
    // A tool RESULT arrives as a user row. It is not somebody typing.
    line({ type: 'user', timestamp: '2026-08-17T15:00:45.000Z',
           message: { content: [{ type: 'tool_result', content: 'ok' }] } }),
].join('\n');

const evs = eventsFromTranscript(transcript, { ws: 'scribe', participant: 'p01', root: '/scribe' });

// ── what the agent did ──
const kinds = evs.map((e) => e.ev);
assert.deepStrictEqual(kinds, ['prompt', 'agent-read', 'edit', 'edit', 'agent'],
    `expected one of each, in time order; got ${kinds.join(',')}`);

const prompt = evs[0];
assert.strictEqual(prompt.chars, 'support block quotes please'.length,
    'a prompt carries its size');
assert.ok(!('text' in prompt), 'and never its words');

const read = evs[1];
assert.strictEqual(read.file, 'scribe/blocks.py', 'paths are project-relative');
assert.strictEqual(read.surface, 'code');
assert.strictEqual(read.by, 'agent');

const codeEdit = evs[2];
assert.strictEqual(codeEdit.surface, 'code');
assert.strictEqual(codeEdit.active, false,
    'an agent edit is marked the way the extension marks a file changing underneath');
assert.strictEqual(codeEdit.added, 5);
assert.strictEqual(codeEdit.removed, 2);

const docEdit = evs[3];
assert.strictEqual(docEdit.surface, 'document', 'CLAUDE.md is the description');

assert.strictEqual(evs[4].cmd, 'pytest', 'a command carries its first token only');

// A tool result must not have become a prompt: five events, not six.
assert.strictEqual(evs.filter((e) => e.ev === 'prompt').length, 1,
    'a tool result is not a human turn');

// ── the merge ──
const editor = [
    { t: 1, ev: 'focus', surface: 'code', file: 'scribe/blocks.py' },
    { t: 2, ev: 'edit', surface: 'code', file: 'scribe/blocks.py', active: true, focused: true, added: 3 },
    // The same agent edit the transcript already has, seen because the file was open.
    { t: 3, ev: 'edit', surface: 'code', file: 'scribe/blocks.py', active: false, focused: true, added: 200 },
    { t: 4, ev: 'window', focused: false },
];
const merged = mergeEvents(editor, evs);

assert.ok(merged.some((e) => e.ev === 'edit' && e.active === true),
    'a person typing survives the merge');
assert.strictEqual(
    merged.filter((e) => e.ev === 'edit' && e.active === false && e.by !== 'agent').length, 0,
    'the editor\'s echo of an agent edit is dropped; the transcript has it better');
assert.ok(merged.some((e) => e.ev === 'window'),
    'events only the editor can see are kept');
assert.ok(merged.some((e) => e.ev === 'focus'), 'including focus');

// Time order survives, which every downstream sequence depends on.
const times = merged.map((e) => e.t);
assert.deepStrictEqual(times, [...times].sort((a, b) => a - b), 'the merge stays in time order');

// ── the reason this file exists ──
// An agent that edits a file nobody has open leaves NOTHING in the editor log.
const unopened = mergeEvents([], eventsFromTranscript(transcript, { ws: 'scribe', root: '/scribe' }));
assert.strictEqual(unopened.filter((e) => e.ev === 'edit').length, 2,
    'both agent edits survive with no editor log at all');

console.log(`study logger: transcript — ${evs.length} agent events, `
    + `${merged.length} merged, all assertions pass`);
