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
import { writeFileSync, mkdtempSync, readFileSync } from 'node:fs';
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

const MACHINE = mkdtempSync(join(tmpdir(), 'pilot-machine-'));

async function mirrorOne(code, condition, raw) {
    // One directory for both conditions, because a participant has one machine.
    // Using a fresh one per condition is what surfaced the identity bug, and
    // modelling it correctly is what proves the fix.
    const dir = MACHINE;
    const logPath = join(dir, `interaction-${condition}.jsonl`);
    writeFileSync(logPath, `${raw.map((r) => JSON.stringify(r)).join('\n')}\n`);

    const problems = [];
    const mirror = new Mirror({
        logPath, config: CONFIG,   // statePath per log, identity shared: the real layout
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
