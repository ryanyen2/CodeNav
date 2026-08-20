// Spend one pilot slot on the whole chain, before spending a person on it.
//
//   node scripts/pilot-run.mjs p-pilot0000001
//
// What this actually exercises: raw logger events of exactly the shape the VS
// Code extension writes, through the REAL classifier and sequence builder, out
// through the REAL mirror to production Firestore, and back through the export.
// Nothing here reimplements a step it is testing.
//
// What it does not exercise is VS Code itself, which cannot be driven headlessly.
// So the raw events are written by hand rather than captured from an editor, and
// the honest claim is about the recording and analysis chain rather than about
// whether the extension emits these events at the right moments. The extension
// has its own tests for that.
import { writeFileSync, mkdtempSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { toSequence } from '../../docs/study-materials/logger/actions-vocab.js';
import { Mirror } from '../../docs/study-materials/logger/mirror.js';

const CONFIG = { apiKey: 'AIzaSyCeIFBc8HhCmtw9-pXjUm1qT3CUyo5GbkY', projectId: 'codoc-11b10' };

/**
 * A session as raw logger events.
 *
 * Written to be plausible rather than flattering. The codoc arm reads and writes
 * the description and accepts proposals; the baseline arm mostly does not touch
 * CLAUDE.md, which is what actually happens when nothing prompts you to. If the
 * study finds the opposite, that is the finding.
 */
function rawSession(condition, startedAt) {
    const doc = condition === 'codoc' ? '.codoc/tree.codoc' : 'CLAUDE.md';
    const ev = [];
    let t = startedAt;
    const at = (ms) => { const now = t; t += ms; return now; };

    const view = (file, surface, ms) => {
        const started = at(ms);
        // A view is logged when the file LEAVES the screen, carrying how long it
        // was up. mapEvent winds it back; that is the behaviour under test.
        ev.push({ t: started + ms, p: 'pilot', ws: 'hearth', ev: 'view', surface, file, ms });
    };
    const edit = (file, surface, human, added) => ev.push({
        t: at(4000), p: 'pilot', ws: 'hearth', ev: 'edit', surface, file,
        active: human, focused: human, added, removed: 0,
    });
    const run = (cmd) => ev.push({ t: at(9000), p: 'pilot', ws: 'hearth', ev: 'agent', cmd });
    const prompt = (chars) => ev.push({ t: at(25000), p: 'pilot', ws: 'hearth', ev: 'prompt', chars });
    const verdict = (kind) => ev.push({ t: at(3000), p: 'pilot', ws: 'hearth', ev: 'verdict', kind });

    // Orienting.
    view(doc, 'document', 95_000);
    view('hearth/build.py', 'code', 60_000);
    view('hearth/post.py', 'code', 48_000);
    view('tests/test_build.py', 'test', 30_000);

    for (let round = 0; round < 5; round += 1) {
        if (condition === 'codoc') {
            view(doc, 'document', 40_000);
            edit(doc, 'document', true, 120);       // wrote intent down
        }
        prompt(180 + round * 40);
        edit('hearth/post.py', 'code', false, 40);  // the agent
        edit('hearth/build.py', 'code', false, 25);
        if (condition === 'codoc') {
            edit(doc, 'document', false, 60);       // codoc wrote back
            view(doc, 'document', 34_000);          // and they looked
            verdict(round % 4 === 3 ? 'reject' : 'accept');
        }
        view('hearth/post.py', 'code', 28_000);
        run('pytest');
        if (round === 2) {
            // A gap long enough to become IDLE, because real sessions have them
            // and the analysis must not stitch across one.
            t += 5 * 60_000;
        }
        if (round === 4) run('hearth');
    }

    // Checking at the end.
    view('tests/test_build.py', 'test', 40_000);
    edit('tests/test_build.py', 'test', true, 30);
    run('pytest');
    view(doc, 'document', condition === 'codoc' ? 55_000 : 12_000);
    return ev;
}

// Who this machine is, in a directory that survives between runs.
//
// It used to be a fresh temporary directory per run, which looked tidy and was
// the bug that cost us a week of empty dashboards. A fresh directory means a
// fresh anonymous sign-in, and a fresh sign-in claims the participant's
// claim-once mirror slot as a different account, so the real editor on this
// machine was then refused every batch it tried to send under that code. Keeping
// the identity in one place makes a pilot run look like what it is, which is one
// more program on the same machine as the editor.
const IDENTITY_DIR = process.env.PILOT_MACHINE_DIR
    || join(tmpdir(), 'codoc-pilot-machine');
mkdirSync(IDENTITY_DIR, { recursive: true });
const IDENTITY = join(IDENTITY_DIR, 'mirror-identity.json');

// The logs themselves are per run, because each run writes its own session and
// the read offset has to start at zero for the bytes to be sent at all.
const LOGS = mkdtempSync(join(tmpdir(), 'pilot-logs-'));

async function mirrorOne(code, condition, raw) {
    const logPath = join(LOGS, `interaction-${condition}.jsonl`);
    writeFileSync(logPath, `${raw.map((r) => JSON.stringify(r)).join('\n')}\n`);

    const problems = [];
    const mirror = new Mirror({
        // State per log and identity per machine, which is the real layout. Using
        // a fresh identity per condition is what surfaced the original bug, so it
        // is modelled correctly here rather than worked around.
        logPath, identityPath: IDENTITY, config: CONFIG,
        code, condition, onError: (m) => problems.push(m),
    });
    await mirror.start();
    await mirror.flush(true);
    await mirror.stop();
    return { logPath, problems, dir };
}

async function main() {
    const code = process.argv[2];
    if (!code) { console.error('usage: node scripts/pilot-run.mjs <code>'); process.exit(2); }

    console.error(`pilot machine identity: ${IDENTITY}`);
    console.error(`This run signs in as that account and claims the mirror slot on `
        + `${code}. Use a code you are willing to spend, and do not use a code a `
        + `participant is going to sit down with, because their editor would then `
        + `find the slot taken. Re-running against the same code sends the same `
        + `byte ranges, which Firestore refuses as already written, so a second run `
        + `reports no problems and adds no data.`);

    const t0 = Date.parse('2026-08-15T14:00:00Z');
    const report = {};

    for (const condition of ['codoc', 'baseline']) {
        const raw = rawSession(condition, condition === 'codoc' ? t0 : t0 + 90 * 60_000);
        const seq = toSequence(raw);
        const { problems } = await mirrorOne(code, condition, raw);

        const counts = {};
        for (const a of seq) counts[a.a] = (counts[a.a] || 0) + 1;
        report[condition] = {
            rawEvents: raw.length,
            actions: seq.length,
            minutes: Math.round((seq[seq.length - 1].t - seq[0].t) / 60000),
            counts,
            problems,
        };
    }

    console.log(JSON.stringify(report, null, 2));
}

void main();
