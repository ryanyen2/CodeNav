// The 20-second snapshots, taken by the logger instead of by a person.
//
// Why this is here and not in a script. `session-log.sh` did this job and had to
// be started by hand, in its own terminal, at the start of each condition. It is
// the only part of the setup that leaves NO mark on the screen when it was never
// started: the session runs normally and looks fine, and the gap is discovered at
// collection, hours after the one moment it could have been fixed. That is what
// happened on the first pilot — neither condition has a replay. So the recorder
// now lives inside the extension that already starts on its own, in both
// conditions, and already knows the participant code and the workspace.
//
// What a snapshot is:
//
//   1. A commit of the whole working tree on a shadow ref, refs/study/<label>.
//   2. A copy of the description and codoc's control files, when they changed.
//
// The shadow ref is the important part. The old script ran `git checkout -b`,
// which moved the participant onto a study branch and made their own `git status`
// and `git log` part of the instrument. This writes the commit with plumbing —
// its own index file, `commit-tree`, `update-ref` — so HEAD, the branch, the
// index and the working tree are all untouched. `git log --all` still shows every
// snapshot, which is all the replay needs.
//
// EXCLUDE is not a performance tweak. The study workspaces have no .gitignore, so
// `git add -A` takes in the virtual environment (large, and rebuilt from source
// anyway) and — this is the one that matters — `.claude-study/api-key` and `.env`,
// the two keys. collect.sh excludes both from the zip by name, but a commit is
// inside .git, and .git travels with the workspace. Snapshotting them would have
// mailed the keys back inside the history while the exclusion above looked like it
// was working.
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const EVERY_MS = 20000;
/** Nobody else may hold the ref; a stale lock is one older than this. */
const LOCK_MS = 60000;

// Rebuildable, enormous, or secret. See the note above.
const EXCLUDE = [
    '.venv', 'node_modules', '_site', '__pycache__', '.pytest_cache', '*.egg-info',
    '.claude-study', '.env', 'api-key', 'api-key.sh',
];

// codoc's own state, which is not in git (`.codoc/` carries its own .gitignore),
// plus the baseline's description, which is. CLAUDE.md is copied even though the
// commits already hold it so that both arms leave their description history in
// the same place, and one query finds either.
const STATE_FILES = [
    'tree.codoc', 'tree.doc.json', 'tree.bindings.json', 'status.json',
    'activity.json', 'drift.json', 'edits.json', 'inbox.json', 'ask.json',
    'realize.md', 'realize.json', 'realized.jsonl',
];
const ROOT_FILES = ['CLAUDE.md'];

/**
 * `git add` needs a positive pathspec before it will accept an exclusion.
 *
 * The long `:(exclude)` form, not the short `:!` one: git reads the characters
 * after `:!` as more magic, so `:!_site` dies with "Unimplemented pathspec magic
 * '_'" and takes the whole snapshot with it.
 */
function pathspecs() {
    const out = ['.'];
    for (const e of EXCLUDE) { out.push(`:(exclude)${e}`, `:(exclude)${e}/**`); }
    return out;
}

function stamp(now) {
    const d = new Date(now);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** A ref name has to be safe, and a label comes from a participant code. */
function safeLabel(s) {
    const cleaned = String(s || '').replace(/[^A-Za-z0-9._-]/g, '-').replace(/^[-.]+/, '');
    return cleaned || 'session';
}

class Snapshotter {
    constructor(opts) {
        this.repo = opts.repo;
        this.dir = opts.dir;
        this.label = safeLabel(opts.label);
        this.ref = `refs/study/${this.label}`;
        this.everyMs = opts.everyMs || EVERY_MS;
        this.log = opts.log || (() => {});
        this.onEvent = opts.onEvent || (() => {});
        this.index = path.join(this.dir, 'snapshot.index');
        this.states = path.join(this.dir, 'codoc-states');
        this.lock = path.join(this.dir, 'snapshot.lock');
        this.lastCopied = new Map();
        this.count = 0;
        this.copies = 0;
        this.failed = '';
        this.timer = null;
        this.busy = false;
        this.repoOk = false;
    }

    git(args, env) {
        return execFileSync('git', ['-C', this.repo].concat(args), {
            encoding: 'utf8',
            maxBuffer: 1 << 24,
            stdio: ['ignore', 'pipe', 'pipe'],
            env: Object.assign({}, process.env, {
                GIT_INDEX_FILE: this.index,
                // A study machine may have no git identity configured at all, and
                // commit-tree refuses to write without one.
                GIT_AUTHOR_NAME: 'codoc study', GIT_AUTHOR_EMAIL: 'study@codoc.local',
                GIT_COMMITTER_NAME: 'codoc study', GIT_COMMITTER_EMAIL: 'study@codoc.local',
            }, env || {}),
        }).trim();
    }

    /** The parent for the next snapshot: the last one, else where they started. */
    parent() {
        for (const r of [this.ref, 'HEAD']) {
            try {
                const sha = this.git(['rev-parse', '--verify', '--quiet', r]);
                if (sha) return sha;
            } catch (_e) { /* no such ref yet */ }
        }
        return '';
    }

    /** Copy the description and codoc's state, but only what changed. */
    copyState(at) {
        const sources = STATE_FILES.map((f) => [f, path.join(this.repo, '.codoc', f)])
            .concat(ROOT_FILES.map((f) => [f, path.join(this.repo, f)]));
        const changed = [];
        for (const [name, src] of sources) {
            let st;
            try { st = fs.statSync(src); } catch (_e) { continue; }
            const sig = `${st.size}:${st.mtimeMs}`;
            if (this.lastCopied.get(name) === sig) continue;
            changed.push([name, src, sig]);
        }
        if (!changed.length) return 0;
        const dest = path.join(this.states, at);
        try { fs.mkdirSync(dest, { recursive: true }); } catch (_e) { return 0; }
        let n = 0;
        for (const [name, src, sig] of changed) {
            try {
                fs.copyFileSync(src, path.join(dest, name));
                this.lastCopied.set(name, sig);
                n += 1;
            } catch (_e) { /* mid-write; the next pass catches it */ }
        }
        return n;
    }

    /** One snapshot. Never throws: a recorder must not interrupt a session. */
    once(now) {
        const at = stamp(now || Date.now());
        let committed = false;
        if (this.repoOk) {
            try {
                this.git(['add', '-A', '--'].concat(pathspecs()));
                const tree = this.git(['write-tree']);
                const args = ['commit-tree', tree, '-m', `snapshot ${at}`];
                const parent = this.parent();
                if (parent) { args.push('-p', parent); }
                const commit = this.git(args);
                this.git(['update-ref', this.ref, commit]);
                committed = true;
                this.count += 1;
            } catch (err) {
                const why = (err && err.message ? err.message : String(err)).split('\n')[0];
                if (this.failed !== why) {
                    this.failed = why;
                    this.log(`snapshot failed: ${why}`);
                    // The reason travels with the event. It used to go only to the
                    // output channel, which nobody opens, so a workspace that was
                    // never snapshotted said "nothing is snapshotting it" and gave
                    // no way to find out why. The interaction log is the one place
                    // that is always collected.
                    this.onEvent({ ev: 'snapshot', ok: false, why: 'git', detail: why });
                }
            }
        }
        const copied = this.copyState(at);
        this.copies += copied;
        // One positive marker, in the same stream as everything else, so a live
        // session can be checked without asking anybody to look at a terminal.
        if (committed && this.count === 1) {
            this.onEvent({ ev: 'snapshot', ok: true, every: Math.round(this.everyMs / 1000) });
        }
        return { committed, copied };
    }

    /** Another window on the same workspace is already recording it. */
    locked() {
        try {
            const st = fs.statSync(this.lock);
            if (Date.now() - st.mtimeMs > LOCK_MS) return false;
            const pid = Number(String(fs.readFileSync(this.lock, 'utf8')).trim());
            return Boolean(pid) && pid !== process.pid;
        } catch (_e) { return false; }
    }

    touchLock() {
        try { fs.writeFileSync(this.lock, `${process.pid}\n`); } catch (_e) { /* best effort */ }
    }

    start() {
        if (this.timer) return true;
        try {
            if (!fs.statSync(this.repo).isDirectory()) return false;
        } catch (_e) { return false; }        // not a real folder: nothing to record
        try { fs.mkdirSync(this.dir, { recursive: true }); } catch (_e) { return false; }
        if (this.locked()) {
            this.log('another window is already recording this workspace');
            return false;
        }
        this.touchLock();
        try {
            this.git(['rev-parse', '--git-dir']);
            this.repoOk = true;
        } catch (_e) {
            // Still worth running: the control-file copies do not need git, and a
            // workspace without a repo is a setup fault worth seeing in the log.
            this.log('no git repository here, so only the state files are copied');
        }
        try {
            fs.writeFileSync(path.join(this.dir, 'snapshot.meta'),
                `workspace: ${this.repo}\nlabel:     ${this.label}\n`
                + `ref:       ${this.ref}\nstarted:   ${new Date().toISOString()}\n`);
        } catch (_e) { /* best effort */ }
        const tick = () => {
            if (this.busy) return;
            this.busy = true;
            try { this.touchLock(); this.once(); } finally { this.busy = false; }
        };
        tick();
        this.timer = setInterval(tick, this.everyMs);
        // Recording is never a reason for a process to stay alive.
        if (this.timer.unref) this.timer.unref();
        this.log(`snapshots every ${Math.round(this.everyMs / 1000)}s to ${this.dir}`);
        return true;
    }

    stop() {
        if (this.timer) { clearInterval(this.timer); this.timer = null; }
        try { fs.unlinkSync(this.lock); } catch (_e) { /* already gone */ }
    }

    status() {
        return { dir: this.dir, ref: this.ref, snapshots: this.count,
                 copies: this.copies, failed: this.failed, running: Boolean(this.timer) };
    }
}

module.exports = { Snapshotter, EXCLUDE, STATE_FILES, ROOT_FILES, pathspecs, safeLabel, EVERY_MS };
