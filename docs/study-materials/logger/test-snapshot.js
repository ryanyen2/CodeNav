// The snapshot recorder: what it captures, and what it must not touch.
//
//   node test-snapshot.js
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Snapshotter } = require('./snapshot');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'snap-test-'));
const repo = path.join(tmp, 'scribe');
const logs = path.join(tmp, 'session-logs', 'snapshots', 'p04', 'scribe');

const git = (args, cwd = repo) =>
    execFileSync('git', ['-C', cwd].concat(args), { encoding: 'utf8' }).trim();

// ── a workspace shaped like the study's ─────────────────────────────────────
fs.mkdirSync(path.join(repo, 'scribe'), { recursive: true });
fs.mkdirSync(path.join(repo, '.codoc'), { recursive: true });
fs.mkdirSync(path.join(repo, '.venv', 'lib'), { recursive: true });
fs.mkdirSync(path.join(repo, '.claude-study'), { recursive: true });
fs.writeFileSync(path.join(repo, 'scribe', 'blocks.py'), 'def render():\n    pass\n');
fs.writeFileSync(path.join(repo, 'CLAUDE.md'), '# Codebase feature guide\n');
fs.writeFileSync(path.join(repo, '.codoc', 'tree.codoc'), '# scribe\n');
fs.writeFileSync(path.join(repo, '.codoc', 'status.json'), '{"features":25}');
fs.writeFileSync(path.join(repo, '.venv', 'lib', 'big.bin'), 'x'.repeat(4096));
fs.writeFileSync(path.join(repo, '.claude-study', 'api-key'), 'sk-do-not-collect-me');
fs.writeFileSync(path.join(repo, '.env'), 'OPENAI_API_KEY=sk-nor-this-one');

git(['init', '-q', '-b', 'main', '.']);
git(['add', 'scribe', 'CLAUDE.md']);
git(['-c', 'user.email=a@b', '-c', 'user.name=a', 'commit', '-qm', 'first']);

const branchBefore = git(['rev-parse', '--abbrev-ref', 'HEAD']);
const headBefore = git(['rev-parse', 'HEAD']);
const statusBefore = git(['status', '--porcelain']);

// ── record ──────────────────────────────────────────────────────────────────
const events = [];
const snap = new Snapshotter({
    repo, dir: logs, label: 'p04-scribe', everyMs: 60_000,
    log: () => {}, onEvent: (e) => events.push(e),
});
assert.strictEqual(snap.start(), true, 'the recorder starts on a real workspace');
assert.ok(snap.count >= 1, 'and takes the first snapshot immediately, not in 20 seconds');
assert.deepStrictEqual(events.map((e) => e.ok), [true],
    'exactly one liveness marker, so a live session can be checked without a terminal');

// ── the participant's own git is untouched ──────────────────────────────────
// The old script ran `git checkout -b`, which made their branch part of the
// instrument. A snapshot has to be invisible from where they are sitting.
assert.strictEqual(git(['rev-parse', '--abbrev-ref', 'HEAD']), branchBefore,
    'their branch does not move');
assert.strictEqual(git(['rev-parse', 'HEAD']), headBefore, 'HEAD does not move');
assert.strictEqual(git(['status', '--porcelain']), statusBefore,
    'nothing is staged and nothing is stashed');

// ── but the snapshot is there, and replayable ───────────────────────────────
const ref = 'refs/study/p04-scribe';
assert.ok(git(['rev-parse', '--verify', ref]), 'the shadow ref exists');
assert.ok(git(['log', '--oneline', '--all']).includes('snapshot'),
    'git log --all finds the snapshots, which is how the session is replayed');

const listed = git(['ls-tree', '-r', '--name-only', ref]).split('\n');
assert.ok(listed.includes('scribe/blocks.py'), 'the code is in the snapshot');
assert.ok(listed.includes('CLAUDE.md'), 'and the description');

// The one that would have mailed the keys home. collect.sh excludes api-key and
// .env by name, but a commit lives inside .git, and .git travels with the folder.
for (const secret of ['.claude-study/api-key', '.env']) {
    assert.ok(!listed.includes(secret), `${secret} is never committed to the shadow ref`);
}
assert.ok(!listed.some((f) => f.startsWith('.venv/')),
    'and the virtual environment is not snapshotted every 20 seconds');

// ── the description's history ───────────────────────────────────────────────
const states = () => fs.readdirSync(path.join(logs, 'codoc-states')).sort();
assert.strictEqual(states().length, 1, 'the first pass copies the starting state');
const first = path.join(logs, 'codoc-states', states()[0]);
assert.ok(fs.existsSync(path.join(first, 'tree.codoc')), 'codoc state is copied');
assert.ok(fs.existsSync(path.join(first, 'CLAUDE.md')),
    'and so is the baseline description, so one query finds either arm');

// Nothing changed, so nothing is written: what is on disk is the history of
// changes, which is the measure ("what kind of edits people make").
snap.once(Date.parse('2026-08-18T10:00:20Z'));
assert.strictEqual(states().length, 1, 'an idle pass writes no new state folder');

fs.writeFileSync(path.join(repo, '.codoc', 'tree.codoc'), '# scribe\n\nBlock quotes.\n');
snap.once(Date.parse('2026-08-18T10:00:40Z'));
assert.strictEqual(states().length, 2, 'an edit to the description is captured');

// ── the code they wrote arrives too ─────────────────────────────────────────
fs.writeFileSync(path.join(repo, 'scribe', 'blocks.py'), 'def render():\n    return ">"\n');
snap.once(Date.parse('2026-08-18T10:01:00Z'));
assert.ok(git(['show', `${ref}:scribe/blocks.py`]).includes('">"'),
    'a change made between snapshots is in the next one');
assert.ok(snap.count >= 4, 'every pass commits, so the timeline has no gaps');

// ── two windows on one workspace ────────────────────────────────────────────
fs.writeFileSync(path.join(logs, 'snapshot.lock'), '999999\n');
const second = new Snapshotter({ repo, dir: logs, label: 'p04-scribe', log: () => {} });
assert.strictEqual(second.start(), false,
    'a second window does not race the first for the same ref');
snap.stop();

// ── it must never interrupt a session ───────────────────────────────────────
const noRepo = path.join(tmp, 'plain');
fs.mkdirSync(path.join(noRepo, '.codoc'), { recursive: true });
fs.writeFileSync(path.join(noRepo, '.codoc', 'tree.codoc'), '# no git here\n');
const bare = new Snapshotter({
    repo: noRepo, dir: path.join(tmp, 'logs2'), label: 'p04-plain', log: () => {},
});
assert.strictEqual(bare.start(), true, 'a workspace with no repo still records its state');
assert.strictEqual(bare.count, 0, 'it just has nothing to commit');
assert.ok(fs.existsSync(path.join(tmp, 'logs2', 'codoc-states')), 'the copies happen anyway');
bare.stop();

const gone = new Snapshotter({ repo: path.join(tmp, 'nope'), dir: path.join(tmp, 'logs3'),
                               label: 'x', log: () => {} });
assert.strictEqual(gone.start(), false, 'a folder that is not there is not an error');

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`study logger: snapshots — ${snap.count} commits, `
    + 'branch untouched, keys excluded, all assertions pass');
