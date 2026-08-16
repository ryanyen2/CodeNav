// Exporting a session and checking it against the local copy.
//
//   npm run test:export      (needs the emulator)
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { exportSession, decodeFields } from '../scripts/export-session.mjs';
import { Mirror } from '../../docs/study-materials/logger/mirror.js';
import { FirestoreRest, emulatorHosts } from '../../docs/study-materials/logger/firestore-rest.js';

const PROJECT = 'codoc-study-rules-test';
const CODE = 'p-exporttestxx';
const hosts = emulatorHosts();
const ADMIN = { authorization: 'Bearer owner', 'content-type': 'application/json' };

let logPath;

before(async () => {
    await fetch(`${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/participants?documentId=${CODE}`, {
        method: 'POST', headers: ADMIN,
        body: JSON.stringify({ fields: {
            createdAt: { integerValue: String(Date.now()) },
            order: { stringValue: 'codoc-first' },
            released: { booleanValue: false },
        } }),
    });
    // An assessment, as the dashboard would leave one.
    await fetch(`${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/participants/${CODE}/assessments?documentId=codoc`, {
        method: 'POST', headers: ADMIN,
        body: JSON.stringify({ fields: {
            signoffConfidence: { integerValue: '4' },
            signoffVerbatim: { stringValue: 'The tests pass and I read the diff.' },
        } }),
    });

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'exp-'));
    logPath = path.join(dir, 'interaction.jsonl');
    const ev = Array.from({ length: 30 }, (_, i) => ({
        ev: 'edit', surface: 'code', file: 'a.py', t: i * 20_000, active: true, focused: true, added: 4,
    }));
    fs.writeFileSync(logPath, ev.map((e) => JSON.stringify(e)).join('\n') + '\n');

    const m = new Mirror({
        logPath, code: CODE, condition: 'codoc',
        client: new FirestoreRest({ apiKey: 'emu', projectId: PROJECT, hosts }),
    });
    if (!await m.start()) throw new Error('the mirror could not start');
    await m.flush(true);
});

test('a session exports with its actions, answers and assessment', async () => {
    const data = await exportSession({ code: CODE, project: PROJECT, token: 'owner', emulator: true });
    assert.equal(data.code, CODE);
    assert.ok(data.sessions.codoc, 'the condition that ran is there');
    assert.ok(data.sessions.codoc.actions.length > 0);
    assert.equal(data.assessments.length, 1);
    assert.equal(data.assessments[0].signoffConfidence, 4);
    assert.ok(!('baseline' in data.sessions), 'a condition that never ran is left out');
});

test('the export records which bytes of the log each batch covered', async () => {
    // This is how a gap between the live copy and the local file is spotted
    // rather than guessed at.
    const data = await exportSession({ code: CODE, project: PROJECT, token: 'owner', emulator: true });
    const covered = data.sessions.codoc.covered;
    assert.ok(covered.length > 0);
    for (const [from, to] of covered) {
        assert.equal(typeof from, 'number');
        assert.ok(to > from);
    }
});

test('the checker sees the two halves agree', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sess-'));
    const session = path.join(dir, `codoc-study-${CODE}`);
    fs.mkdirSync(path.join(session, 'session-logs'), { recursive: true });
    fs.copyFileSync(logPath, path.join(session, 'session-logs', 'interaction-hearth.jsonl'));
    execFileSync('node', ['scripts/export-session.mjs', CODE, '--project', PROJECT,
        '--emulator', '--token', 'owner', '--out', session], { encoding: 'utf8' });

    // The checker exits non-zero whenever anything is missing, and this folder
    // deliberately holds only the two halves being compared, so read its output
    // rather than its status.
    let out = '';
    try {
        out = execFileSync('python3',
            ['../docs/study-materials/scoring/check-session-complete.py', session],
            { encoding: 'utf8' });
    } catch (err) {
        out = err.stdout || '';
    }
    assert.match(out, /The live copy/);
    assert.match(out, /actions arrived, with no gap between batches/);
    assert.match(out, /nothing identifying was stored/);
    assert.match(out, /your notes and scores/);
});

test('the checker says so when the mirror never sent anything', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sess2-'));
    const session = path.join(dir, 'codoc-study-p99');
    fs.mkdirSync(path.join(session, 'session-logs'), { recursive: true });
    fs.copyFileSync(logPath, path.join(session, 'session-logs', 'interaction-hearth.jsonl'));
    fs.writeFileSync(path.join(session, 'firestore-p99.json'), JSON.stringify({
        code: 'p99', participant: {}, answers: [], assessments: [], sessions: {},
    }));

    let out = '';
    try {
        out = execFileSync('python3',
            ['../docs/study-materials/scoring/check-session-complete.py', session],
            { encoding: 'utf8' });
    } catch (err) {
        out = err.stdout || '';       // it exits non-zero when something is missing
    }
    assert.match(out, /the mirror never sent anything/);
    assert.match(out, /source of truth/, 'and says the local file still has everything');
});

test('an export that is refused says so rather than coming back empty', async () => {
    // Without this, a researcher whose account is not on the allowlist gets a
    // file full of nothing and no reason why.
    await assert.rejects(
        () => exportSession({ code: CODE, project: PROJECT, token: '', emulator: true }),
        /not allowed to read|40[13]/,
    );
});

test('the decoder turns Firestore values back into ordinary ones', () => {
    assert.deepEqual(decodeFields({
        n: { integerValue: '5' },
        s: { stringValue: 'x' },
        b: { booleanValue: true },
        a: { arrayValue: { values: [{ integerValue: '1' }] } },
        m: { mapValue: { fields: { k: { stringValue: 'v' } } } },
    }), { n: 5, s: 'x', b: true, a: [1], m: { k: 'v' } });
});
