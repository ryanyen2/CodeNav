// Edit classification, and the prompt hook installer.
//
//   node --test test-composition.js
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { classifyEdit, keptRatio, novelRatio, mentionsReason, summarise, EDIT_KINDS } from './composition.js';

const HERE = path.dirname(new URL(import.meta.url).pathname);

// ── classifying an edit ──────────────────────────────────────────────────────

test('every label is reachable, so none is dead', () => {
    const seen = new Set([
        classifyEdit('a b c', 'a b c').kind,
        classifyEdit('', 'brand new text here').kind,
        classifyEdit('some old text', '').kind,
        classifyEdit('the cache stores results', 'the cache stores results for speed and safety across runs').kind,
        classifyEdit('the cache stores results for speed and safety across runs', 'the cache stores results').kind,
        classifyEdit('the cache keeps results', 'the cache holds outputs').kind,
        classifyEdit('the cache stores results', 'feeds are fetched on a timer by the scheduler').kind,
    ]);
    for (const k of EDIT_KINDS) assert.ok(seen.has(k), `${k} is never produced`);
});

test('adding a sentence of reasoning is extended, not rewritten', () => {
    // The edit the study most wants to see: someone writing down why.
    const before = 'Rebuilds the aggregate pages when the collection changes.';
    const after = 'Rebuilds the aggregate pages when the collection changes. '
        + 'We rejected a per-output dependency graph because it was always subtly '
        + 'wrong after deletes.';
    const r = classifyEdit(before, after);
    assert.equal(r.kind, 'extended');
    assert.ok(r.kept > 0.9, 'the original survived');
});

test('replacing the text entirely is a rewrite', () => {
    const r = classifyEdit(
        'Reads feeds and stores items in sqlite.',
        'Chooses which posts appear on the home page and in the feed.',
    );
    assert.equal(r.kind, 'rewritten');
    assert.ok(r.kept < 0.35);
});

test('changing the wording without changing the length is reworded', () => {
    const r = classifyEdit(
        'The renderer turns markdown bodies into safe HTML output.',
        'The renderer converts markdown bodies into escaped HTML output.',
    );
    assert.equal(r.kind, 'reworded');
});

test('cutting most of a paragraph is trimmed, not rewritten', () => {
    // Both a deletion and a rewrite leave little of the original, so how much
    // survives cannot tell them apart on its own. What separates them is whether
    // anything new arrived.
    const before = 'one two three four five six seven eight nine ten eleven twelve';
    const r = classifyEdit(before, 'one two three four');
    assert.equal(r.kind, 'trimmed');
    assert.ok(r.kept < 0.35, 'little of the original survives');
    assert.equal(r.novel, 0, 'and nothing new arrived, which is what makes it a cut');
});

test('a deletion and a replacement of the same size are different edits', () => {
    const before = 'the cache stores what the last build wrote so the next run can skip it';
    const cut = classifyEdit(before, 'the cache stores what the last build wrote');
    const swap = classifyEdit(before, 'feeds arrive on a timer and are written straight to the store');
    assert.equal(cut.kind, 'trimmed');
    assert.equal(swap.kind, 'rewritten');
});

test('novelty is the share of the result that is new', () => {
    assert.equal(novelRatio('a b c', 'a b c'), 0);
    assert.equal(novelRatio('a b c', 'a b'), 0);
    assert.ok(novelRatio('a b c', 'x y z') > 0.9);
});

test('a rename that touches every line is not mistaken for a rewrite', () => {
    // Word level, not character level. Renaming one identifier throughout leaves
    // the writing intact, and calling that a rewrite would drown the real ones.
    const before = 'The cache stores what the last build wrote so the next run can skip it.';
    const after = 'The store stores what the last build wrote so the next run can skip it.';
    const r = classifyEdit(before, after);
    assert.notEqual(r.kind, 'rewritten');
    assert.ok(r.kept > 0.8);
});

test('kept ratio counts repeats rather than treating words as a set', () => {
    assert.equal(keptRatio('a a a', 'a'), 1 / 3);
    assert.equal(keptRatio('a b', 'a b'), 1);
    assert.equal(keptRatio('', 'x'), 0);
});

test('an edit that gives a reason is worth flagging for a human to read', () => {
    assert.ok(mentionsReason('We rejected a dependency graph because it was wrong after deletes.'));
    assert.ok(mentionsReason('Filters here rather than in the renderer, so the signature sees it.'));
    assert.ok(!mentionsReason('Renders the home page and the tag pages.'));
    // Never the measure itself; rationale is hand-scored against a written key.
});

test('a run of edits summarises without losing the shape', () => {
    const s = summarise([
        { kind: 'extended', beforeWords: 10, afterWords: 30, reason: true },
        { kind: 'reworded', beforeWords: 30, afterWords: 31 },
        { kind: 'extended', beforeWords: 31, afterWords: 40 },
    ]);
    assert.equal(s.total, 3);
    assert.equal(s.counts.extended, 2);
    assert.equal(s.netWords, 30);
    assert.equal(s.withReason, 1);
});

// ── installing the prompt hook ───────────────────────────────────────────────

function project(withCodocHooks) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hookproj-'));
    if (withCodocHooks) {
        fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
        fs.writeFileSync(path.join(dir, '.claude', 'settings.json'), JSON.stringify({
            hooks: {
                UserPromptSubmit: [{ hooks: [{ type: 'command', command: '/x/python -m codoc.agent.hook user-prompt', timeout: 10 }] }],
                Stop: [{ hooks: [{ type: 'command', command: '/x/python -m codoc.agent.hook stop', timeout: 10 }] }],
            },
        }, null, 2));
    }
    return dir;
}

const install = (dir, extra = []) =>
    execFileSync('python3', [path.join(HERE, 'install-prompt-hook.py'), dir, ...extra], { encoding: 'utf8' });

const settingsOf = (dir) =>
    JSON.parse(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf8'));

test('it installs into a project that has no settings at all', () => {
    const dir = project(false);
    install(dir);
    const s = settingsOf(dir);
    assert.equal(s.hooks.UserPromptSubmit.length, 1);
    assert.match(s.hooks.UserPromptSubmit[0].hooks[0].command, /prompt-hook\.py/);
});

test("it does not disturb codoc's own hooks", () => {
    // The whole reason this merges rather than writes. Replacing that file in the
    // codoc condition would quietly disable the tool being studied.
    const dir = project(true);
    install(dir);
    const s = settingsOf(dir);
    const commands = s.hooks.UserPromptSubmit.flatMap((e) => e.hooks.map((h) => h.command));
    assert.ok(commands.some((c) => c.includes('codoc.agent.hook')), "codoc's hook survived");
    assert.ok(commands.some((c) => c.includes('prompt-hook.py')), 'ours was added');
    assert.equal(s.hooks.Stop.length, 1, 'other events are untouched');
});

test('installing twice leaves one copy', () => {
    const dir = project(true);
    install(dir); install(dir); install(dir);
    const s = settingsOf(dir);
    const ours = s.hooks.UserPromptSubmit
        .flatMap((e) => e.hooks).filter((h) => h.command.includes('prompt-hook.py'));
    assert.equal(ours.length, 1);
});

test('a participant code is carried into the command', () => {
    const dir = project(false);
    install(dir, ['--participant', 'p-abcdefghjkmn']);
    const s = settingsOf(dir);
    assert.match(s.hooks.UserPromptSubmit[0].hooks[0].command, /CODOC_STUDY_PARTICIPANT=p-abcdefghjkmn/);
});

test('it refuses a settings file it cannot read rather than replacing it', () => {
    const dir = project(true);
    fs.writeFileSync(path.join(dir, '.claude', 'settings.json'), '{ not json');
    assert.throws(() => install(dir));
    assert.equal(fs.readFileSync(path.join(dir, '.claude', 'settings.json'), 'utf8'), '{ not json');
});

test('the installed command points at wherever the hook was copied to', () => {
    // setup.sh copies the hook out of the unzipped bundle before installing it,
    // because a participant is free to delete that folder and would otherwise
    // stop having prompts recorded with nothing to show it.
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'hookhome-'));
    const kept = path.join(home, 'logger');
    fs.mkdirSync(kept, { recursive: true });
    for (const f of ['install-prompt-hook.py', 'prompt-hook.py']) {
        fs.copyFileSync(path.join(HERE, f), path.join(kept, f));
    }
    const dir = project(true);
    execFileSync('python3', [path.join(kept, 'install-prompt-hook.py'), dir], { encoding: 'utf8' });

    const s = settingsOf(dir);
    const cmd = s.hooks.UserPromptSubmit
        .flatMap((e) => e.hooks).map((h) => h.command).find((c) => c.includes('prompt-hook.py'));
    const hookPath = cmd.match(/(\S+prompt-hook\.py)/)[1];
    // Compare resolved paths: the installer resolves symlinks, and on macOS the
    // temp directory lives behind one.
    assert.equal(fs.realpathSync(path.dirname(hookPath)), fs.realpathSync(kept),
        'it points at the kept copy');
    assert.ok(fs.existsSync(hookPath));
    assert.ok(!hookPath.includes('codoc-study-bundle'), 'and not into the bundle');
});

// ── the hook itself ──────────────────────────────────────────────────────────

test('the hook writes one line and never the prompt text', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hooklog-'));
    const log = path.join(dir, 'interaction.jsonl');
    execFileSync('python3', [path.join(HERE, 'prompt-hook.py')], {
        input: JSON.stringify({ prompt: 'add draft support, hidden in prod and shown in dev' }),
        env: { ...process.env, CODOC_STUDY_LOG: log, CODOC_STUDY_PARTICIPANT: 'p-x' },
    });
    const line = JSON.parse(fs.readFileSync(log, 'utf8').trim());
    assert.equal(line.ev, 'prompt');
    assert.equal(line.p, 'p-x');
    assert.equal(line.words, 10);
    assert.ok(line.chars > 40);
    assert.ok(!JSON.stringify(line).includes('draft support'), 'the words stay in the transcript');
});

test('a broken payload does not fail the turn', () => {
    // A hook that exits non-zero blocks the agent, so this must be impossible.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hooklog-'));
    for (const input of ['', 'not json', '{}', '{"prompt": null}']) {
        const out = execFileSync('python3', [path.join(HERE, 'prompt-hook.py')], {
            input, env: { ...process.env, CODOC_STUDY_LOG: path.join(dir, 'l.jsonl') },
        });
        assert.equal(out.toString(), '', 'and it stays silent on stdout');
    }
});

test('an unwritable log does not fail the turn either', () => {
    execFileSync('python3', [path.join(HERE, 'prompt-hook.py')], {
        input: JSON.stringify({ prompt: 'hello' }),
        env: { ...process.env, CODOC_STUDY_LOG: '/proc/nope/cannot/write.jsonl' },
    });
});
