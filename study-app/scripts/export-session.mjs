// Pull one participant out of Firestore, to sit beside their zip.
//
//   node scripts/export-session.mjs p-abcdefghjkmn --out ~/study/p04
//   node scripts/export-session.mjs p-… --emulator
//
// Reading needs an allowlisted account, so this asks for a token rather than
// holding one. The simplest way is `firebase login:ci`, or paste an ID token from
// the dashboard's console. On the emulator no token is needed.
//
// What comes out is plain JSON files, deliberately: they sit next to the zip, get
// read by check-session-complete.py, and outlive whatever this project is called
// in two years.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const LIVE = 'https://firestore.googleapis.com/v1';
const EMULATOR = 'http://127.0.0.1:8080/v1';

function args() {
    const a = process.argv.slice(2);
    const code = a.find((x) => !x.startsWith('--'));
    const flag = (name, fallback = '') => {
        const i = a.indexOf(`--${name}`);
        return i >= 0 && a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : fallback;
    };
    return {
        code,
        out: flag('out', code ? `./${code}` : ''),
        project: flag('project', 'codoc-11b10'),
        token: flag('token', process.env.CODOC_STUDY_TOKEN || ''),
        emulator: a.includes('--emulator'),
    };
}

/** Firestore's typed values back to ordinary ones. */
export function decode(v) {
    if (!v || typeof v !== 'object') return v;
    if ('stringValue' in v) return v.stringValue;
    if ('integerValue' in v) return Number(v.integerValue);
    if ('doubleValue' in v) return v.doubleValue;
    if ('booleanValue' in v) return v.booleanValue;
    if ('timestampValue' in v) return v.timestampValue;
    if ('nullValue' in v) return null;
    if ('arrayValue' in v) return (v.arrayValue.values || []).map(decode);
    if ('mapValue' in v) return decodeFields(v.mapValue.fields);
    return null;
}

export function decodeFields(fields) {
    const out = {};
    for (const [k, v] of Object.entries(fields || {})) out[k] = decode(v);
    return out;
}

async function get(base, project, token, path) {
    const url = `${base}/projects/${project}/databases/(default)/documents/${path}`;
    const headers = token ? { authorization: `Bearer ${token}` } : {};
    const r = await fetch(url, { headers });
    if (!r.ok) throw new Error(`${r.status} reading ${path}`);
    return r.json();
}

/** Every document in a collection, following pages. */
async function list(base, project, token, path) {
    const docs = [];
    let pageToken = '';
    do {
        const suffix = pageToken ? `?pageToken=${encodeURIComponent(pageToken)}&pageSize=300` : '?pageSize=300';
        const url = `${base}/projects/${project}/databases/(default)/documents/${path}${suffix}`;
        const headers = token ? { authorization: `Bearer ${token}` } : {};
        const r = await fetch(url, { headers });
        // A collection that is empty and one we are not allowed to read look the
        // same from here unless the status is checked, and an export that came
        // back blank because of permissions would be mistaken for a session that
        // never happened.
        if (r.status === 401 || r.status === 403) {
            throw new Error(`not allowed to read ${path}. Is this account on the allowlist?`);
        }
        if (!r.ok) return docs;             // absent is not an error
        const body = await r.json();
        for (const d of body.documents || []) {
            docs.push({ id: d.name.split('/').pop(), ...decodeFields(d.fields) });
        }
        pageToken = body.nextPageToken || '';
    } while (pageToken);
    return docs;
}

export async function exportSession({ code, project, token, emulator }) {
    const base = emulator ? EMULATOR : LIVE;
    const p = `participants/${code}`;

    const participant = await get(base, project, token, p).then(
        (d) => decodeFields(d.fields),
        (err) => {
            if (/40[13]/.test(err.message)) throw err;
            return null;      // a participant document we cannot see is not fatal
        });

    const answers = await list(base, project, token, `${p}/answers`);
    const assessments = await list(base, project, token, `${p}/assessments`);

    const sessions = {};
    for (const condition of ['codoc', 'baseline']) {
        const batches = await list(base, project, token, `${p}/sessions/${condition}/batches`);
        batches.sort((a, b) => (a.seq || 0) - (b.seq || 0));
        const actions = batches.flatMap((b) => b.actions || []).sort((a, b) => a.t - b.t);
        if (!batches.length && !actions.length) continue;
        sessions[condition] = {
            batches: batches.length,
            actions,
            // The byte ranges each batch covered, which is how a gap between the
            // live copy and the local log is spotted rather than guessed at.
            covered: batches.map((b) => [b.fromByte ?? null, b.toByte ?? null]),
        };
    }

    return { code, exportedAt: new Date().toISOString(), participant, answers, assessments, sessions };
}

function main() {
    const opts = args();
    if (!opts.code) {
        console.error('usage: node scripts/export-session.mjs <code> [--out DIR] [--emulator]');
        process.exit(2);
    }
    exportSession(opts).then((data) => {
        mkdirSync(opts.out, { recursive: true });
        const dest = join(opts.out, `firestore-${opts.code}.json`);
        writeFileSync(dest, `${JSON.stringify(data, null, 2)}\n`);
        const counts = Object.entries(data.sessions)
            .map(([c, s]) => `${c}: ${s.actions.length} actions`).join(', ') || 'no sessions';
        console.log(`wrote ${dest}`);
        console.log(`  ${counts}`);
        console.log(`  ${data.answers.length} answer sets, ${data.assessments.length} assessments`);
    }).catch((err) => {
        console.error(`could not export: ${err.message}`);
        console.error('Reading needs an allowlisted account. Pass --token, or set CODOC_STUDY_TOKEN.');
        process.exit(1);
    });
}

if (import.meta.url === `file://${process.argv[1]}`) main();
