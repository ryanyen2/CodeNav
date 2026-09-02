// Pull EVERY participant out of Firestore in one go, for handing to an analyst.
//
//   node scripts/export-all.mjs --out ~/study/export
//   node scripts/export-all.mjs --out ./export --emulator
//
// export-session.mjs does one participant and is the right thing during a study,
// when a session has just finished and you want to see whether it arrived. This
// is the end-of-study one: it lists the roster and exports all of it, so an
// analyst gets a directory rather than a list of codes to type.
//
// What it writes:
//   <out>/firestore-<code>.json    one per participant, same shape as the single export
//   <out>/index.json               the roster, with what each participant holds
//
// It does NOT read `contacts`, which is the one collection carrying a
// participant's name. That is deliberate and it is the reason this export can be
// handed to somebody who is not on the ethics protocol: nothing in here
// identifies anybody. Do not add it.
//
// Reading needs an allowlisted account, so this asks for a token rather than
// holding one:
//
//   1. Open https://codoc-11b10.web.app/experimenter/ and sign in.
//   2. In the browser console:  await firebase.auth().currentUser.getIdToken()
//   3. export CODOC_STUDY_TOKEN='<the token>'
//
// The token lasts an hour, which is longer than this takes.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { exportSession, decodeFields } from './export-session.mjs';

const LIVE = 'https://firestore.googleapis.com/v1';
const EMULATOR = 'http://127.0.0.1:8080/v1';

function args() {
    const a = process.argv.slice(2);
    const flag = (name, fallback = '') => {
        const i = a.indexOf(`--${name}`);
        return i >= 0 && a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : fallback;
    };
    return {
        out: flag('out', './export'),
        project: flag('project', 'codoc-11b10'),
        token: flag('token', process.env.CODOC_STUDY_TOKEN || ''),
        emulator: a.includes('--emulator'),
        // Pilots are exported by default and marked, never skipped. A pilot that
        // is missing from the directory is a pilot somebody re-runs the export
        // to find; a pilot that is present and labelled is one nobody analyses
        // by accident, which is the actual risk.
        includePilots: !a.includes('--no-pilots'),
    };
}

/** Every participant code on the roster. */
async function roster(base, project, token) {
    const docs = [];
    let pageToken = '';
    do {
        const q = pageToken ? `?pageToken=${encodeURIComponent(pageToken)}&pageSize=300` : '?pageSize=300';
        const url = `${base}/projects/${project}/databases/(default)/documents/participants${q}`;
        const r = await fetch(url, { headers: token ? { authorization: `Bearer ${token}` } : {} });
        // An empty roster and a roster we are not allowed to read look identical
        // from here. Telling them apart is the whole reason this check exists:
        // an export that came back with nothing because of permissions would be
        // filed as a study in which nobody took part.
        if (r.status === 401 || r.status === 403) {
            throw new Error('not allowed to list participants. Is this account on the allowlist, '
                + 'and is CODOC_STUDY_TOKEN a current ID token rather than an expired one?');
        }
        if (!r.ok) throw new Error(`${r.status} listing participants`);
        const body = await r.json();
        for (const d of body.documents || []) {
            docs.push({ code: d.name.split('/').pop(), ...decodeFields(d.fields) });
        }
        pageToken = body.nextPageToken || '';
    } while (pageToken);
    return docs;
}

/**
 * The instant a participant's session actually ran.
 *
 * Not `updatedAt`, which is the last time anything was written and therefore
 * lies for exactly the sessions that matter: the ones whose answers sat in
 * localStorage while saving was broken and were flushed days later. `startedAt`
 * on the task document is stamped once, by the page, on the participant's own
 * machine, and never rewritten.
 *
 * The fallbacks are ordered by how much they can be trusted, and which one was
 * used is recorded, because a session placed by its creation date is placed by
 * when the researcher made the code and not by when anybody sat down.
 */
export function ranAt(data) {
    const answers = data.answers || [];
    const task = answers.filter((a) => a.id && a.id.startsWith('task-'));
    const starts = task.map((a) => a.startedAt).filter((v) => typeof v === 'number');
    if (starts.length) return { at: Math.min(...starts), from: 'task.startedAt' };

    const actions = Object.values(data.sessions || {}).flatMap((s) => s.actions || []);
    const ts = actions.map((a) => a.t).filter((v) => typeof v === 'number');
    if (ts.length) return { at: Math.min(...ts), from: 'first logged action' };

    const updates = answers.map((a) => a.updatedAt).filter((v) => typeof v === 'number');
    if (updates.length) return { at: Math.min(...updates), from: 'earliest answers.updatedAt (weak)' };

    const created = data.participant && data.participant.createdAt;
    if (typeof created === 'number') return { at: created, from: 'participant.createdAt (weak)' };
    return { at: null, from: 'nothing' };
}

async function main() {
    const opts = args();
    const base = opts.emulator ? EMULATOR : LIVE;

    const people = await roster(base, opts.project, opts.token);
    if (!people.length) {
        console.error('the roster is empty. That is a real answer only if no participant was ever created.');
        process.exit(1);
    }
    mkdirSync(opts.out, { recursive: true });

    const index = [];
    for (const p of people) {
        const isPilot = p.code.startsWith('pilot-') || !!p.pilot;
        if (isPilot && !opts.includePilots) continue;
        let data;
        try {
            data = await exportSession({ ...opts, code: p.code });
        } catch (err) {
            console.error(`  ${p.code}: FAILED — ${err.message}`);
            index.push({ code: p.code, failed: err.message });
            continue;
        }
        writeFileSync(join(opts.out, `firestore-${p.code}.json`),
            `${JSON.stringify(data, null, 2)}\n`);

        const ran = ranAt(data);
        const row = {
            code: p.code,
            pilot: data.pilot,
            analyse: data.analyse,
            excluded: !!p.excluded,
            order: p.order || null,
            ranAt: ran.at,
            ranAtISO: ran.at ? new Date(ran.at).toISOString() : null,
            ranAtFrom: ran.from,
            answerDocs: (data.answers || []).map((a) => a.id).sort(),
            conditions: Object.keys(data.sessions || {}),
            actions: Object.fromEntries(
                Object.entries(data.sessions || {}).map(([c, s]) => [c, (s.actions || []).length])),
            assessments: (data.assessments || []).map((a) => a.id).sort(),
        };
        index.push(row);
        const tag = data.pilot ? 'PILOT' : (p.excluded ? 'EXCLUDED' : 'analyse');
        console.log(`  ${p.code}  ${tag}  ${row.ranAtISO || 'no timestamp'}  `
            + `(${row.ranAtFrom})  ${row.answerDocs.length} answer docs`);
    }

    index.sort((a, b) => (a.ranAt || 0) - (b.ranAt || 0));
    writeFileSync(join(opts.out, 'index.json'),
        `${JSON.stringify({ exportedAt: new Date().toISOString(), project: opts.project, participants: index }, null, 2)}\n`);

    const analysable = index.filter((r) => r.analyse && !r.excluded && !r.failed);
    console.log(`\nwrote ${index.length} participants to ${opts.out}`);
    console.log(`  ${analysable.length} for analysis, `
        + `${index.filter((r) => r.pilot).length} pilots, `
        + `${index.filter((r) => r.excluded).length} excluded, `
        + `${index.filter((r) => r.failed).length} failed`);
    console.log('\nNext: node scripts/instrument-report.mjs --in ' + opts.out);
}

if (import.meta.url === `file://${process.argv[1]}`) {
    main().catch((err) => {
        console.error(`could not export: ${err.message}`);
        process.exit(1);
    });
}
