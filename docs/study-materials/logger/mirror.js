// Sends a copy of the session upward while it runs.
//
// The local file stays the source of truth. This reads what the logger has
// already written, maps it into the study's action vocabulary, and posts it in
// batches. Everything here is allowed to fail: if the network is gone, or the
// project is unreachable, or the code was never configured, the session carries
// on exactly as it would have without any of this, and the zip at the end still
// contains everything.
//
// What makes it exactly-once: a batch's document id is derived from the byte
// range of the log it covers. A resend after a crash therefore lands on the same
// id, the rules refuse to overwrite an existing batch, and "already exists" is
// treated as success. So a crash between sending and recording the send costs
// nothing and duplicates nothing.
import fs from 'node:fs';
import path from 'node:path';
import { FirestoreRest } from './firestore-rest.js';
import { toSequence } from './actions-vocab.js';

export const DEFAULTS = Object.freeze({
    flushMs: 10_000,      // send at least this often
    flushActions: 50,     // or this many actions, whichever comes first
});

export class Mirror {
    /**
     * @param {object} opts
     *   logPath     the logger's JSONL
     *   statePath   where the sign-in and the read offset are remembered
     *   config      { apiKey, projectId }
     *   code        the participant code
     *   condition   'codoc' | 'baseline'
     *   client      injectable, so the tests never touch the network
     *   onError     called with a message; the caller decides where it shows
     */
    constructor(opts) {
        this.logPath = opts.logPath;
        this.statePath = opts.statePath || `${opts.logPath}.mirror.json`;
        // Who this machine is, kept beside the logs rather than beside one log.
        //
        // A participant works in two workspaces, so the logger writes two log
        // files and therefore two state files. When identity lived in those, the
        // second condition signed in as a NEW anonymous user, could not claim the
        // one mirror slot the first had taken, and mirrored nothing. Half of every
        // participant's data, silently, with a message on an output channel
        // nobody has open. The read offset stays per log, because it is about
        // that log; the sign-in is about the machine.
        this.identityPath = opts.identityPath || defaultIdentityPath(opts.logPath);
        this.code = opts.code;
        this.condition = opts.condition || 'codoc';
        this.flushMs = opts.flushMs ?? DEFAULTS.flushMs;
        this.flushActions = opts.flushActions ?? DEFAULTS.flushActions;
        this.onError = opts.onError || (() => {});
        this.client = opts.client || new FirestoreRest(opts.config, opts.fetchImpl);
        this.state = this._loadState();
        this.registered = false;
        this.timer = null;
    }

    _loadState() {
        const read = (file, fallback) => {
            try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
        };
        const perLog = read(this.statePath, { offset: 0, seq: 0 });
        const identity = read(this.identityPath, {});
        // An older state file carried the identity itself. Honour it, so a
        // machine set up before this change does not sign in again and lose the
        // slot it already holds.
        return {
            offset: perLog.offset || 0,
            seq: perLog.seq || 0,
            uid: identity.uid || perLog.uid || null,
            refreshToken: identity.refreshToken || perLog.refreshToken || null,
        };
    }

    _saveState() {
        if (this.state.uid) {
            const itmp = `${this.identityPath}.tmp`;
            try {
                fs.writeFileSync(itmp, JSON.stringify({
                    uid: this.state.uid, refreshToken: this.state.refreshToken,
                }));
                fs.renameSync(itmp, this.identityPath);
            } catch { /* the per-log state below is what a restart needs most */ }
        }
        const tmp = `${this.statePath}.tmp`;
        fs.writeFileSync(tmp, JSON.stringify(this.state));
        fs.renameSync(tmp, this.statePath);   // never a half-written state file
    }

    /** Sign in and take the mirror slot, then start the timer. */
    async start() {
        if (!this.code) { this.onError('no participant code configured; not mirroring'); return false; }
        // The timer starts whether or not the first attempt worked. A session that
        // begins on a train must still mirror once the network appears, and every
        // flush retries whatever is not yet in place.
        this.timer = setInterval(() => { void this.flush(); }, this.flushMs);
        if (this.timer.unref) this.timer.unref();
        return this._ensureReady();
    }

    /**
     * Signed in and holding the mirror slot. Called before every send, because
     * either can fail at startup and both must recover on their own. Without
     * this, a network that is down for the first ten seconds costs the whole
     * session.
     */
    async _ensureReady() {
        if (this.registered) return true;
        try {
            if (!this.client.idToken) {
                this.client.restore(this.state);
                const { uid, refreshToken } = await this.client.signIn();
                this.state.uid = uid;
                this.state.refreshToken = refreshToken;
                this._saveState();
            }
            const res = await this.client.createDocument(
                `participants/${this.code}/devices`, 'mirror',
                { uid: this.state.uid, kind: 'mirror', registeredAt: Date.now() },
            );
            // "Already exists" is the normal answer on a restart, when the slot is
            // this machine's own. It is also the answer when the slot belongs to
            // somebody else, and those two cases must not be confused: every
            // batch would then be refused, one permission error at a time, with
            // nothing saying why. So when the slot was already there, check whose
            // it is.
            if (res && res.existed && this.client.getDocument) {
                const held = await this.client.getDocument(
                    `participants/${this.code}/devices/mirror`);
                if (!held || held.uid !== this.state.uid) {
                    this.registered = false;
                    this.onError('this code\'s mirror slot belongs to another machine. '
                        + 'Ask the experimenter to release the code, then restart.');
                    return false;
                }
            }
            this.registered = true;
            return true;
        } catch (err) {
            this.onError(`not mirroring yet, will keep trying: ${err.message}`);
            this.registered = false;
            return false;
        }
    }

    /** Everything written to the log since the last successful send. */
    _readNew() {
        let text = '';
        let end = this.state.offset;
        try {
            const fd = fs.openSync(this.logPath, 'r');
            try {
                const size = fs.fstatSync(fd).size;
                if (size <= this.state.offset) return { events: [], end: this.state.offset };
                const len = size - this.state.offset;
                const buf = Buffer.alloc(len);
                fs.readSync(fd, buf, 0, len, this.state.offset);
                text = buf.toString('utf8');
                end = size;
            } finally { fs.closeSync(fd); }
        } catch {
            return { events: [], end: this.state.offset };
        }

        // A trailing partial line means the logger is mid-write. Leave it for the
        // next pass rather than sending half an event.
        const lastNewline = text.lastIndexOf('\n');
        if (lastNewline === -1) return { events: [], end: this.state.offset };
        const complete = text.slice(0, lastNewline + 1);
        end = this.state.offset + Buffer.byteLength(complete, 'utf8');

        const events = [];
        for (const line of complete.split('\n')) {
            if (!line.trim()) continue;
            try { events.push(JSON.parse(line)); } catch { /* skip a garbled line */ }
        }
        return { events, end };
    }

    /**
     * Map, batch, send. Safe to call at any time and safe to call concurrently
     * with the logger writing.
     */
    async flush(force = false) {
        if (!this.code) return { sent: 0 };
        const { events, end } = this._readNew();
        if (!events.length) return { sent: 0 };

        const actions = toSequence(events);
        if (!actions.length) { this.state.offset = end; this._saveState(); return { sent: 0 }; }
        if (!force && actions.length < this.flushActions
            && (Date.now() - (this.state.lastSentAt || 0)) < this.flushMs) {
            return { sent: 0 };   // not yet worth a write
        }
        if (!await this._ensureReady()) return { sent: 0, pending: actions.length };

        // The id names the byte range, so a resend is the same document.
        const id = `b-${String(this.state.offset).padStart(10, '0')}-${String(end).padStart(10, '0')}`;
        try {
            await this.client.createDocument(
                `participants/${this.code}/sessions/${this.condition}/batches`, id,
                {
                    seq: this.state.seq + 1,
                    fromByte: this.state.offset,
                    toByte: end,
                    count: actions.length,
                    sentAt: Date.now(),
                    actions,
                },
            );
        } catch (err) {
            // Keep the offset where it is. The same bytes go up next time, under
            // the same id, so nothing is lost and nothing is doubled.
            this.onError(`batch not sent, will retry: ${err.message}`);
            return { sent: 0, pending: actions.length };
        }

        this.state.offset = end;
        this.state.seq += 1;
        this.state.lastSentAt = Date.now();
        this._saveState();
        return { sent: actions.length };
    }

    async stop() {
        if (this.timer) { clearInterval(this.timer); this.timer = null; }
        await this.flush(true);
    }
}

/** Read the mirror's settings from the environment or a settings object. */
export function settingsFrom(cfg, env = process.env) {
    return {
        code: (cfg && cfg.code) || env.CODOC_STUDY_PARTICIPANT || '',
        condition: (cfg && cfg.condition) || env.CODOC_STUDY_CONDITION || 'codoc',
        enabled: !!((cfg && cfg.code) || env.CODOC_STUDY_PARTICIPANT),
    };
}

export const FIREBASE_CONFIG = Object.freeze({
    apiKey: 'AIzaSyCeIFBc8HhCmtw9-pXjUm1qT3CUyo5GbkY',
    projectId: 'codoc-11b10',
});

export function defaultStatePath(logPath) {
    return path.join(path.dirname(logPath), `${path.basename(logPath)}.mirror.json`);
}

/**
 * One identity per machine, shared by every workspace.
 *
 * Beside the logs rather than inside one, because a participant's two conditions
 * are two workspaces and the mirror slot is claimed once.
 */
export function defaultIdentityPath(logPath) {
    return path.join(path.dirname(logPath), 'mirror-identity.json');
}
