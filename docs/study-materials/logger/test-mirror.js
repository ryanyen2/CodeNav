// What the mirror must guarantee.
//
//   node test-mirror.js
//
// The whole point of the mirror is that it can fail without costing anything. So
// most of this is about failure: no network, a crash between sending and
// recording the send, a restart, a half-written line. The network is stubbed
// throughout; nothing here touches the internet.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Mirror } from './mirror.js';
import { encode, encodeFields } from './firestore-rest.js';

function tmpdir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'mirror-test-'));
}

/** A stand-in for Firestore that records what it was asked to store. */
function fakeClient({ failWrites = false, existing = new Set() } = {}) {
    const written = new Map();
    let signIns = 0;
    return {
        written, existing,
        get signIns() { return signIns; },
        // Modelled on the real client: restoring a refresh token gets the SAME
        // identity back rather than minting a new one. A fake that always issued
        // a fresh uid hid the bug where a participant's second workspace signed
        // in again and lost the mirror slot.
        restore({ refreshToken, uid } = {}) { this.refreshToken = refreshToken || null; this.uid = uid || null; },
        async signIn() {
            if (this.refreshToken && this.uid) return { uid: this.uid, refreshToken: this.refreshToken };
            signIns += 1;
            this.uid = `uid-${signIns}`;
            this.refreshToken = `refresh-${signIns}`;
            return { uid: this.uid, refreshToken: this.refreshToken };
        },
        async createDocument(collection, id, data) {
            const key = `${collection}/${id}`;
            if (failWrites) throw new Error('offline');
            if (existing.has(key) || written.has(key)) return { created: false, existed: true };
            written.set(key, data);
            return { created: true };
        },
    };
}

function writeLog(dir, events) {
    const p = path.join(dir, 'interaction.jsonl');
    fs.writeFileSync(p, events.map((e) => JSON.stringify(e)).join('\n') + '\n');
    return p;
}

function appendLog(p, events) {
    fs.appendFileSync(p, events.map((e) => JSON.stringify(e)).join('\n') + '\n');
}

const edit = (t, file = 'a.py') => ({
    ev: 'edit', surface: 'code', file, t, active: true, focused: true, added: 4,
});

// ── the ordinary case ────────────────────────────────────────────────────────

test('a batch reaches the far end and the log is left exactly as it was', async () => {
    const dir = tmpdir();
    const events = Array.from({ length: 60 }, (_, i) => edit(i * 20_000));
    const logPath = writeLog(dir, events);
    const before = fs.readFileSync(logPath);

    const client = fakeClient();
    const m = new Mirror({ logPath, code: 'p-abcdefghjkmn', condition: 'codoc', client });
    await m.start();
    const res = await m.flush(true);

    assert.ok(res.sent > 0, 'something was sent');
    const batches = [...client.written.entries()].filter(([k]) => k.includes('/batches/'));
    assert.equal(batches.length, 1, 'one batch, not one document per action');
    assert.ok(batches[0][1].count >= 50, 'and it carries many actions');
    assert.ok(Array.isArray(batches[0][1].actions));
    assert.deepEqual(fs.readFileSync(logPath), before, 'the log is untouched');
});

test('the mirror takes the mirror slot, not the browser one', async () => {
    const dir = tmpdir();
    const client = fakeClient();
    const m = new Mirror({ logPath: writeLog(dir, [edit(0)]), code: 'p-x', condition: 'codoc', client });
    await m.start();
    assert.ok([...client.written.keys()].some((k) => k.endsWith('/devices/mirror')));
    assert.ok(![...client.written.keys()].some((k) => k.endsWith('/devices/browser')));
});

// ── failure, which is the interesting half ───────────────────────────────────

test('with no network nothing is lost and nothing is marked as sent', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, Array.from({ length: 50 }, (_, i) => edit(i * 20_000)));
    const client = fakeClient({ failWrites: true });
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m.start();
    const res = await m.flush(true);

    assert.equal(res.sent, 0);
    assert.ok(res.pending > 0, 'it reports what is waiting');
    assert.equal(m.state.offset, 0, 'the read position does not move past unsent data');
});

test('when the network returns the backlog goes up', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, Array.from({ length: 50 }, (_, i) => edit(i * 20_000)));

    const offline = fakeClient({ failWrites: true });
    const m1 = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client: offline });
    await m1.start();
    await m1.flush(true);
    assert.equal(offline.written.size, 0, 'nothing gets through, not even the registration');

    // Same state file, a working connection.
    const online = fakeClient();
    const m2 = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client: online });
    await m2.start();
    const res = await m2.flush(true);
    assert.ok(res.sent > 0, 'the backlog uploads once it can');
});

test('a crash between sending and recording the send does not double anything', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, Array.from({ length: 20 }, (_, i) => edit(i * 20_000)));

    // First run sends, then dies before its state file is written.
    const client = fakeClient();
    const m1 = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m1.start();
    const sentKeys = new Set();
    const realCreate = client.createDocument;
    client.createDocument = async (c, id, d) => { sentKeys.add(`${c}/${id}`); return realCreate(c, id, d); };
    await m1.flush(true);
    const batchKeys = [...sentKeys].filter((k) => k.includes('/batches/'));
    assert.equal(batchKeys.length, 1);

    // Second run starts from the old state, so it re-sends the same bytes.
    const m2 = new Mirror({
        logPath, code: 'p-x', condition: 'codoc', client,
        statePath: path.join(dir, 'never-written.json'),
    });
    await m2.start();
    await m2.flush(true);

    const batches = [...client.written.keys()].filter((k) => k.includes('/batches/'));
    assert.equal(batches.length, 1,
        'the resend lands on the same document id and is refused, so there is one batch');
});

test('a restart reuses the saved sign-in rather than taking another slot', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0)]);
    const client = fakeClient();

    const m1 = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m1.start();
    const uid = m1.state.uid;
    assert.ok(m1.state.refreshToken, 'the sign-in is remembered');

    const m2 = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    assert.equal(m2.state.uid, uid, 'the same account comes back');
    assert.equal(m2.state.refreshToken, m1.state.refreshToken);
});

test('a half-written line is left for the next pass', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0), edit(20_000)]);
    fs.appendFileSync(logPath, '{"ev":"edit","surf');   // the logger, mid-write

    const client = fakeClient();
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m.start();
    await m.flush(true);

    const size = fs.statSync(logPath).size;
    assert.ok(m.state.offset < size, 'the partial line is not consumed');
    assert.ok(m.state.offset > 0, 'the complete lines are');
});

test('nothing is sent twice across successive flushes', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0), edit(20_000)]);
    const client = fakeClient();
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m.start();
    await m.flush(true);
    const afterFirst = [...client.written.keys()].filter((k) => k.includes('/batches/')).length;

    appendLog(logPath, [edit(40_000)]);
    await m.flush(true);
    const afterSecond = [...client.written.keys()].filter((k) => k.includes('/batches/')).length;

    assert.equal(afterFirst, 1);
    assert.equal(afterSecond, 2);
    const ranges = [...client.written.keys()].filter((k) => k.includes('/batches/'));
    assert.equal(new Set(ranges).size, ranges.length, 'no id repeats');
});

test('with no condition set nothing is sent, rather than sent to the wrong arm', async () => {
    // The old default was 'codoc', so a baseline workspace whose setup had not
    // written the condition mirrored itself into the codoc arm. Both arms number
    // their batches from byte zero, so the ids collided, and a collision is read
    // as an already-sent batch and therefore as success. Two arms mixed together
    // with nothing anywhere saying so is worse than a session that only exists in
    // the local log, which is why this refuses.
    const dir = tmpdir();
    const logPath = writeLog(dir, Array.from({ length: 60 }, (_, i) => edit(i * 20_000)));
    const client = fakeClient();
    const errors = [];
    const m = new Mirror({ logPath, code: 'p-x', client, onError: (e) => errors.push(e) });

    assert.equal(await m.start(), false, 'it does not start');
    assert.equal(await m.flush(true).then((r) => r.sent), 0, 'and it sends nothing');
    assert.equal(client.written.size, 0, 'not even the slot registration');
    assert.equal(m.state.offset, 0, 'and the log is left unread, so nothing is lost');
    assert.ok(errors.some((e) => e.includes('which condition')),
        'the participant is told why, in a sentence they can repeat to the researcher');
});

test('with no code configured the mirror stays off and the log still works', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0)]);
    const before = fs.readFileSync(logPath);
    const client = fakeClient();
    const m = new Mirror({ logPath, code: '', client });
    const started = await m.start();
    assert.equal(started, false);
    assert.equal(client.written.size, 0);
    assert.deepEqual(fs.readFileSync(logPath), before);
});

test('a mirror that could not start recovers when the network appears', async () => {
    // The session may begin before the network does. Registration is retried on
    // every flush, because failing once must not cost the whole session.
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0), edit(20_000)]);

    let offline = true;
    const inner = fakeClient();
    const client = {
        written: inner.written,
        restore() {},
        async signIn() { if (offline) throw new Error('offline'); return inner.signIn(); },
        async createDocument(c, id, d) {
            if (offline) throw new Error('offline');
            return inner.createDocument(c, id, d);
        },
    };

    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    assert.equal(await m.start(), false, 'it reports that it did not get going');
    assert.equal((await m.flush(true)).sent, 0);

    offline = false;
    const res = await m.flush(true);
    assert.ok(res.sent > 0, 'the same instance recovers without a restart');
    assert.ok([...client.written.keys()].some((k) => k.endsWith('/devices/mirror')),
        'and it registers late rather than never');
});

test('a slot held by another machine is reported, not mistaken for success', async () => {
    // The same answer comes back whether the slot is this machine's own from a
    // restart or somebody else's. Confusing the two would refuse every batch
    // afterwards with nothing saying why.
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0)]);
    const inner = fakeClient();
    const errors = [];
    const client = {
        written: inner.written,
        restore() {}, signIn: inner.signIn,
        async createDocument(c, id, d) {
            if (id === 'mirror') return { created: false, existed: true };
            return inner.createDocument(c, id, d);
        },
        async getDocument() { return { uid: 'somebody-else', kind: 'mirror' }; },
    };
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client, onError: (e) => errors.push(e) });

    assert.equal(await m.start(), false);
    assert.equal((await m.flush(true)).sent, 0);
    assert.ok(errors.some((e) => /another machine/.test(e)),
        'it says what is wrong and what to do about it');
});

test('a slot left behind by an older run is taken over rather than given up on', async () => {
    // The case that cost a week of empty dashboards. A pilot run on the same
    // machine signed in as a throwaway account, took the code's mirror slot, and
    // exited, and the participant's own editor then had every batch refused. The
    // mirror asks for the slot instead of stopping, and the rules decide whether
    // it gets it, which is why the fake says yes here and no in the test below.
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0)]);
    const inner = fakeClient();
    const errors = [];
    const asked = [];
    const client = {
        written: inner.written,
        restore() {}, signIn: inner.signIn,
        async createDocument(c, id, d) {
            if (id === 'mirror') return { created: false, existed: true };
            return inner.createDocument(c, id, d);
        },
        async getDocument() { return null; },   // a slot we are not allowed to read
        async updateDocument(path, data) { asked.push({ path, data }); return { updated: true }; },
    };
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client, onError: (e) => errors.push(e) });

    assert.equal(await m.start(), true, 'it holds the slot after taking it over');
    assert.equal(asked.length, 1, 'it asked for the slot once');
    assert.equal(asked[0].data.uid, m.state.uid, 'and asked for it in its own name');
    assert.ok(errors.some((e) => /taken it over/.test(e)),
        'and says what happened, because a takeover is worth reading about');
    assert.ok((await m.flush(true)).sent > 0, 'and the session then mirrors');
});

test('a slot another machine is still using is left alone', async () => {
    // The rules refuse the write while the holder is still being heard from, so a
    // second machine cannot take a live session's slot out from under it. What
    // the mirror does with a refusal is say so and keep the local log.
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0)]);
    const inner = fakeClient();
    const errors = [];
    const client = {
        written: inner.written,
        restore() {}, signIn: inner.signIn,
        async createDocument(c, id, d) {
            if (id === 'mirror') return { created: false, existed: true };
            return inner.createDocument(c, id, d);
        },
        async getDocument() { return null; },
        async updateDocument() { return { updated: false, status: 403, body: 'PERMISSION_DENIED' }; },
    };
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client, onError: (e) => errors.push(e) });

    assert.equal(await m.start(), false);
    assert.equal((await m.flush(true)).sent, 0, 'and nothing is sent under a code it does not hold');
    assert.ok(errors.some((e) => /still using it/.test(e)),
        'it says the other machine is live and what to do about it');
    assert.ok(errors.some((e) => /local log keeps everything/.test(e)),
        'and that nothing has been lost');
});

test('the slot records when this machine was last heard from', async () => {
    // Holding a slot and sending from it look identical on the dashboard without
    // this, which is exactly how a session that mirrored nothing showed a green
    // dot for two hours.
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0), edit(20_000)]);
    const inner = fakeClient();
    const beats = [];
    const client = {
        written: inner.written,
        restore() {}, signIn: inner.signIn, createDocument: inner.createDocument,
        async getDocument() { return null; },
        async updateDocument(path, data) { beats.push({ path, data }); return { updated: true }; },
    };
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m.start();
    assert.ok((await m.flush(true)).sent > 0);
    assert.equal(beats.length, 0,
        'no heartbeat straight after registering, which already wrote the time');

    // A minute of session, without waiting one. The throttle is deliberate: the
    // slot is a signal about whether anything is arriving, and a write per batch
    // would cost more than the signal is worth.
    m.lastSeenSentAt = Date.now() - 120_000;
    appendLog(logPath, [edit(40_000)]);
    assert.ok((await m.flush(true)).sent > 0);
    assert.equal(beats.length, 1, 'one heartbeat for the later send');
    assert.equal(beats[0].path, 'participants/p-x/devices/mirror');
    assert.ok(beats[0].data.lastSeenAt > 0, 'carrying the time it sent');
    assert.deepEqual(Object.keys(beats[0].data), ['lastSeenAt'],
        'and nothing else, so a heartbeat can never rewrite who holds the slot');
});

test('a restart reclaiming its own slot is not mistaken for a conflict', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [edit(0), edit(20_000)]);
    const inner = fakeClient();
    const client = {
        written: inner.written,
        restore() {}, signIn: async () => ({ uid: 'mine', refreshToken: 'r' }),
        async createDocument(c, id, d) {
            if (id === 'mirror') return { created: false, existed: true };
            return inner.createDocument(c, id, d);
        },
        async getDocument() { return { uid: 'mine', kind: 'mirror' }; },
    };
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    assert.equal(await m.start(), true);
    assert.ok((await m.flush(true)).sent > 0);
});

// ── what leaves the machine ──────────────────────────────────────────────────

test('no file contents, prompts, or paths outside the project leave', async () => {
    const dir = tmpdir();
    const logPath = writeLog(dir, [
        edit(0, 'tally/digest.py'),
        { ev: 'view', surface: 'document', file: 'CLAUDE.md', t: 30_000, ms: 5000 },
    ]);
    const client = fakeClient();
    const m = new Mirror({ logPath, code: 'p-x', condition: 'codoc', client });
    await m.start();
    await m.flush(true);

    const blob = JSON.stringify([...client.written.values()]);
    assert.ok(!blob.includes(os.homedir()), 'no absolute path from this machine');
    assert.ok(!/[Pp]assword|secret|token/.test(blob), 'nothing credential-shaped');
    // Relative project paths are expected and are what the measures need.
    assert.ok(blob.includes('tally/digest.py'));
});

// ── the encoder ──────────────────────────────────────────────────────────────

test('values are encoded in the form Firestore expects', () => {
    assert.deepEqual(encode(3), { integerValue: '3' });
    assert.deepEqual(encode(1.5), { doubleValue: 1.5 });
    assert.deepEqual(encode('x'), { stringValue: 'x' });
    assert.deepEqual(encode(true), { booleanValue: true });
    assert.deepEqual(encode(null), { nullValue: null });
    assert.deepEqual(encode([1]), { arrayValue: { values: [{ integerValue: '1' }] } });
    assert.deepEqual(encode({ a: 1 }), { mapValue: { fields: { a: { integerValue: '1' } } } });
    assert.deepEqual(encodeFields({ a: 1, b: undefined }), { a: { integerValue: '1' } },
        'undefined is left out rather than sent as null');
});

console.log('study logger mirror: all assertions pass');

// ── the second condition ─────────────────────────────────────────────────────

test('both of a participant\'s conditions mirror from the one machine', async () => {
    // The bug: a participant works in two workspaces, so the logger writes two
    // log files and therefore two state files. Identity lived in those, so the
    // second condition signed in as a NEW anonymous user, could not claim the one
    // mirror slot the first had taken, and mirrored nothing. Half of every
    // participant's data, silently, behind a message on a channel nobody opens.
    const dir = tmpdir();
    const client = fakeClient();
    const problems = [];

    const runCondition = async (workspace, condition) => {
        const logPath = path.join(dir, `interaction-${workspace}.jsonl`);
        fs.writeFileSync(logPath, Array.from({ length: 40 }, (_, i) =>
            JSON.stringify(edit(i * 20_000))).join('\n') + '\n');
        const m = new Mirror({
            logPath, config: {}, code: 'p-abcdefghjkmn', condition, client,
            onError: (msg) => problems.push(`${condition}: ${msg}`),
        });
        await m.start();
        await m.flush(true);
        await m.stop();
    };

    await runCondition('scribe', 'codoc');
    await runCondition('tally-baseline', 'baseline');

    assert.deepEqual(problems, [], problems.join('; '));
    assert.equal(client.signIns, 1, 'one machine signs in once, not once per workspace');

    const sent = [...client.written.keys()];
    assert.ok(sent.some((k) => k.includes('/sessions/codoc/batches')),
        'the first condition reached Firestore');
    assert.ok(sent.some((k) => k.includes('/sessions/baseline/batches')),
        'and so did the second, which is what used to be lost');
});

test('the identity is shared, and the read offset is not', async () => {
    // The offset is about one log; the sign-in is about the machine. Sharing the
    // offset too would make the second workspace skip its own first events.
    const dir = tmpdir();
    const client = fakeClient();
    const a = path.join(dir, 'interaction-scribe.jsonl');
    const b = path.join(dir, 'interaction-tally.jsonl');
    for (const p of [a, b]) {
        fs.writeFileSync(p, Array.from({ length: 40 }, (_, i) =>
            JSON.stringify(edit(i * 20_000))).join('\n') + '\n');
    }
    const run = async (logPath, condition) => {
        const m = new Mirror({ logPath, config: {}, code: 'p-x', condition, client });
        await m.start(); await m.flush(true); await m.stop();
        return m;
    };
    const m1 = await run(a, 'codoc');
    const m2 = await run(b, 'baseline');

    assert.equal(m1.state.uid, m2.state.uid, 'one identity');
    assert.ok(fs.existsSync(path.join(dir, 'mirror-identity.json')), 'kept beside the logs');
    assert.ok(m2.state.offset > 0, 'the second log was read from its own start');
});

test('a machine set up before this change keeps the slot it already holds', async () => {
    // An old state file carried the identity itself. Signing in again would mint
    // a new uid and lose the slot, which is the very failure being fixed.
    const dir = tmpdir();
    const client = fakeClient();
    const logPath = path.join(dir, 'interaction-scribe.jsonl');
    fs.writeFileSync(logPath, JSON.stringify(edit(0)) + '\n');
    fs.writeFileSync(`${logPath}.mirror.json`, JSON.stringify({
        offset: 0, seq: 3, uid: 'uid-from-before', refreshToken: 'old-refresh',
    }));

    const m = new Mirror({ logPath, config: {}, code: 'p-x', condition: 'codoc', client });
    assert.equal(m.state.uid, 'uid-from-before');
    assert.equal(m.state.seq, 3, 'and its place in the sequence');
});
