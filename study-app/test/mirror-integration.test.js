// The mirror against the real rules.
//
// The stubbed tests in the logger folder check the mirror's own logic. This
// checks the thing they cannot: that what the mirror sends is what the rules
// accept. A stub always agrees with the code that calls it, which is exactly why
// it cannot answer this question.
//
//   npm run test:mirror
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Mirror } from '../../docs/study-materials/logger/mirror.js';
import { FirestoreRest, emulatorHosts } from '../../docs/study-materials/logger/firestore-rest.js';

const PROJECT = 'codoc-study-rules-test';
const CODE = 'p-abcdefghjkmn';
const CODE2 = 'p-nmkjhgfedcba';
const CODE3 = 'p-qrstuvwxyz23';
const CODE4 = 'p-23456789abcd';
const hosts = emulatorHosts();
const cfg = { apiKey: 'emulator-key', projectId: PROJECT, hosts };

// The emulator treats the literal token "owner" as full access, which is how the
// experimenter's own writes are stood in for here. Rules still apply to everyone
// else, including the mirror under test, which is the point.
const ADMIN = { authorization: 'Bearer owner' };

async function adminWrite(pathname, fields) {
    const url = `${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/${pathname}`;
    const r = await fetch(url, {
        method: 'POST', headers: { 'content-type': 'application/json', ...ADMIN },
        body: JSON.stringify({ fields }),
    });
    if (!r.ok) throw new Error(`seed failed ${r.status} ${await r.text()}`);
}

async function adminList(pathname) {
    const url = `${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/${pathname}`;
    const r = await fetch(url, { headers: ADMIN });
    if (!r.ok) return { documents: [] };
    return r.json();
}

function makeLog(events) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mirror-int-'));
    const p = path.join(dir, 'interaction.jsonl');
    fs.writeFileSync(p, events.map((e) => JSON.stringify(e)).join('\n') + '\n');
    return p;
}

const edit = (t) => ({ ev: 'edit', surface: 'code', file: 'a.py', t, active: true, focused: true, added: 4 });

before(async () => {
    // The experimenter creates the participant in advance. Here that is done with
    // an unauthenticated admin write, which the emulator allows.
    for (const code of [CODE, CODE2, CODE3, CODE4]) {
        await adminWrite(`participants?documentId=${code}`, {
            createdAt: { integerValue: String(Date.now()) },
            released: { booleanValue: false },
        });
    }
});

test('a real mirror registers and lands a batch that the rules accept', async () => {
    const logPath = makeLog(Array.from({ length: 30 }, (_, i) => edit(i * 20_000)));
    const errors = [];
    const m = new Mirror({
        logPath, code: CODE, condition: 'codoc',
        client: new FirestoreRest(cfg),
        onError: (e) => errors.push(e),
    });

    assert.equal(await m.start(), true, `start failed: ${errors.join('; ')}`);
    const res = await m.flush(true);
    assert.ok(res.sent > 0, `nothing sent: ${errors.join('; ')}`);

    const batches = await adminList(`participants/${CODE}/sessions/codoc/batches`);
    assert.equal((batches.documents || []).length, 1);
    const fields = batches.documents[0].fields;
    assert.ok(fields.actions.arrayValue.values.length > 0, 'the actions arrived');
    assert.equal(fields.actions.arrayValue.values[0].mapValue.fields.a.stringValue, 'EDIT_CODE');
});

test('a resend of the same bytes does not create a second batch', async () => {
    // The rules refuse to overwrite a batch, so the id derived from the byte range
    // is what makes a retry after a crash harmless.
    const logPath = makeLog([edit(0), edit(20_000)]);
    const client = new FirestoreRest(cfg);
    const m1 = new Mirror({ logPath, code: CODE2, condition: 'baseline', client });
    await m1.start();
    await m1.flush(true);

    const m2 = new Mirror({
        logPath, code: CODE2, condition: 'baseline', client,
        statePath: `${logPath}.forgotten.json`,   // as if the state was never saved
    });
    await m2.start();
    await m2.flush(true);

    const batches = await adminList(`participants/${CODE2}/sessions/baseline/batches`);
    assert.equal((batches.documents || []).length, 1, 'one batch survives the double send');
});

test('both of one machine\'s workspaces mirror under the same code', async () => {
    // A participant works in two folders, so the logger writes two logs and two
    // state files. Identity is per MACHINE, in a file beside the logs, exactly so
    // the second workspace is the same signed-in user and can hold the one slot
    // the first claimed. When identity lived in the per-log state, the second
    // condition signed in as a new user, could not claim the slot, and mirrored
    // nothing — half of every participant's data, silently.
    const logPath = makeLog([edit(0)]);
    const first = new Mirror({ logPath, code: CODE3, condition: 'codoc', client: new FirestoreRest(cfg) });
    assert.equal(await first.start(), true);

    const errors = [];
    const secondWorkspace = new Mirror({
        logPath, code: CODE3, condition: 'baseline',
        client: new FirestoreRest(cfg),
        statePath: `${logPath}.baseline.json`,     // its own read offset…
        onError: (e) => errors.push(e),            // …and the machine's identity
    });
    assert.equal(await secondWorkspace.start(), true, 'the same machine gets in again');
    assert.deepEqual(errors, [], errors.join('; '));
});

test('a second machine on one code is told the slot is taken', async () => {
    // Realistic: the participant opens the project on a laptop and a desktop, or
    // an old sign-in is gone. The answer must name the problem, because every
    // batch afterwards would otherwise be refused with nothing saying why.
    //
    // A different machine is a different identity file, which is what this now
    // has to say out loud. It used to say it by giving a different state path,
    // which stopped meaning "another machine" the day identity moved out of the
    // state file — so the test passed a mirror its own identity back and checked
    // that it was refused.
    const logPath = makeLog([edit(0)]);
    const first = new Mirror({
        logPath, code: CODE4, condition: 'codoc', client: new FirestoreRest(cfg),
    });
    assert.equal(await first.start(), true);

    const errors = [];
    const second = new Mirror({
        logPath, code: CODE4, condition: 'codoc',
        client: new FirestoreRest(cfg),
        statePath: `${logPath}.second.json`,
        identityPath: `${logPath}.second-machine.json`,
        onError: (e) => errors.push(e),
    });
    assert.equal(await second.start(), false, 'the second machine does not think it registered');
    assert.ok(errors.some((e) => /another machine/.test(e)), errors.join('; '));
});

test('the rules refuse a batch for a code that was never created', async () => {
    const logPath = makeLog([edit(0)]);
    const errors = [];
    const m = new Mirror({
        logPath, code: 'p-zzzzzzzzzzzz', condition: 'codoc',
        client: new FirestoreRest(cfg),
        onError: (e) => errors.push(e),
    });
    assert.equal(await m.start(), false, 'registration is refused');
    assert.equal((await m.flush(true)).sent, 0);
    assert.ok(errors.length > 0, 'and it says so rather than failing silently');
});
