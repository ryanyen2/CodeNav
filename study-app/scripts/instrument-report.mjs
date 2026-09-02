// Which measures survive this cohort, and which decisions are still open.
//
//   node scripts/instrument-report.mjs --in ~/study/export
//   node scripts/instrument-report.mjs --in ~/study/export --json
//
// The study ran while the instrument was still being changed. That is not fatal
// and it is not unusual, but it means no measure can be averaged until somebody
// has checked which era each participant sat in. This does the checking.
//
// It reads the directory export-all.mjs writes, places every participant against
// shared/instrument-eras.js, and prints three things:
//
//   1. The cohort, in the order they ran, with the era each falls in.
//   2. Per measure, whether the cohort can be pooled on it, and why not.
//   3. The decisions that are the researcher's to make, with what each costs.
//
// It answers nothing on its own. A measure that cannot be pooled is not a
// measure that has to be dropped — it may be recoverable, or the split may be
// worth reporting as two halves. What this refuses to do is average across a
// break silently, which is the one outcome nobody can detect afterwards.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { BOUNDARIES, ERAS, MEASURES, eraFor, defectsFor, poolable } from '../shared/instrument-eras.js';
import { ranAt } from './export-all.mjs';

function args() {
    const a = process.argv.slice(2);
    const flag = (name, fallback = '') => {
        const i = a.indexOf(`--${name}`);
        return i >= 0 && a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : fallback;
    };
    return { dir: flag('in', './export'), json: a.includes('--json'), all: a.includes('--include-pilots') };
}

const iso = (ms) => (ms == null ? '—' : new Date(ms).toISOString().replace('T', ' ').slice(0, 16));

function load(dir) {
    const files = readdirSync(dir).filter((f) => f.startsWith('firestore-') && f.endsWith('.json'));
    if (!files.length) throw new Error(`no firestore-*.json in ${dir}. Run export-all.mjs first.`);
    return files.map((f) => JSON.parse(readFileSync(join(dir, f), 'utf8')));
}

/** One participant, placed. */
function place(data) {
    const ran = ranAt(data);
    const { era, unplaceable, near, why } = eraFor(ran.at);
    const docs = (data.answers || []).map((a) => a.id);
    return {
        code: data.code,
        pilot: data.pilot,
        analyse: data.analyse,
        ranAt: ran.at,
        ranAtFrom: ran.from,
        era: era ? era.id : null,
        unplaceable,
        near,
        why,
        defects: defectsFor(ran.at),
        docs: docs.sort(),
        // Which measures this participant actually HOLDS, which is a different
        // question from which era they sat in. An era says what they were asked;
        // this says what arrived. The two disagree whenever saving failed.
        has: Object.fromEntries(MEASURES.map((m) => {
            if (!m.doc) return [m.id, null];
            // The prefix is everything up to the first placeholder. Substituting
            // the placeholder away instead leaves `quiz--before`, which matches
            // no document ever written and reports the round as never collected
            // — the exact false negative this whole report exists to prevent.
            const prefix = m.doc.split('<')[0];
            const found = docs.filter((d) => d.startsWith(prefix));
            if (!found.length) return [m.id, false];
            if (!m.items) return [m.id, true];
            const answers = (data.answers || []).filter((a) => found.includes(a.id));
            return [m.id, m.items.some((it) => answers.some((a) => a[it] != null))];
        })),
    };
}

function main() {
    const opts = args();
    const people = load(opts.dir).map(place)
        .filter((p) => (opts.all ? true : p.analyse))
        .sort((a, b) => (a.ranAt || 0) - (b.ranAt || 0));

    const eraIds = people.map((p) => p.era).filter(Boolean);
    const spread = [...new Set(eraIds)];
    // Which measures are put at risk by a defect somebody in this cohort ran
    // under. A defect does not break pooling, so `poolable` says nothing about
    // it; without this the table would print "yes" beside a measure that half
    // the cohort has no recording of.
    const atRisk = new Set(
        BOUNDARIES.filter((b) => b.direction === 'window')
            .filter((b) => people.some((p) => p.defects.includes(b.id)))
            .flatMap((b) => b.breaks));

    const rows = MEASURES.map((m) => {
        const verdict = poolable(m.id, eraIds);
        const held = people.filter((p) => p.has[m.id] === true).length;
        return { ...m, verdict, held, of: people.length, atRisk: atRisk.has(m.id) };
    });

    if (opts.json) {
        console.log(JSON.stringify({ people, eras: spread, measures: rows }, null, 2));
        return;
    }

    console.log('\n=== the cohort, in the order they ran ===\n');
    console.log('  code              ran (UTC)          era      placed by');
    for (const p of people) {
        const flags = [
            p.unplaceable ? `UNPLACEABLE (${p.near})` : '',
            p.defects.length ? `DEFECT: ${p.defects.join(',')}` : '',
            p.pilot ? 'pilot' : '',
        ].filter(Boolean).join('  ');
        console.log(`  ${p.code.padEnd(16)}  ${iso(p.ranAt).padEnd(17)}  ${(p.era || '—').padEnd(7)}  ${p.ranAtFrom}`);
        if (flags) console.log(`  ${''.padEnd(16)}  ↳ ${flags}`);
    }
    console.log(`\n  ${people.length} participants across ${spread.length} era(s): ${spread.join(', ') || '—'}`);

    console.log('\n=== the boundaries they straddle ===\n');
    const order = ERAS.map((e) => e.id);
    const lo = Math.min(...eraIds.map((i) => order.indexOf(i)).filter((i) => i >= 0));
    const hi = Math.max(...eraIds.map((i) => order.indexOf(i)).filter((i) => i >= 0));
    // Only the cuts, in the same order shared/instrument-eras.js builds the eras
    // from, so an era index and a boundary index mean the same thing. The defect
    // window is not a cut and must not shift that alignment by one.
    const crossed = BOUNDARIES.filter((b) => !b.direction)
        .filter((b, i) => i >= lo && i < hi);
    if (!crossed.length) console.log('  none — every session sat in one era, and nothing has to be decided.');
    for (const b of crossed) {
        console.log(`  ${b.id}   ${iso(b.at)}   ${b.commit}`);
        console.log(`    ${b.title}`);
        for (const c of b.changes) console.log(`      · ${c}`);
        if (b.contested) {
            console.log(`    ⚠ CONTESTED DATE — analysis-plan.md says ${b.contested.planSays}, `
                + `the code says ${b.contested.codeSays}.`);
            console.log(`      Settle with: ${b.contested.settleWith}`);
        }
        console.log('');
    }

    console.log('=== per measure ===\n');
    console.log('  measure              held    pooled   why');
    for (const r of rows) {
        // A measure nobody holds is not a measure that pools. Printing "yes"
        // beside 0/N reads as a green light on something that was never
        // collected, which is worse than printing nothing.
        const absent = r.doc && r.held === 0;
        const mark = absent ? 'ABSENT '
            : r.verdict.ok ? 'yes   '
                : (r.verdict.recoverable ? 'RECOVER' : 'NO    ');
        const star = r.confirmatory ? ' *' : (r.atRisk ? ' !' : '  ');
        const held = r.doc ? `${r.held}/${r.of}` : ' —  ';
        const why = absent ? 'no participant in this cohort holds it' : r.verdict.why;
        console.log(`  ${r.id.padEnd(20)} ${held.padEnd(7)} ${mark}${star} ${why}`);
    }
    console.log('\n  * pre-registered confirmatory prediction. A pooling decision on one of');
    console.log('    these is not a private analysis choice; it changes what the paper claims.');
    if (rows.some((r) => r.atRisk)) {
        console.log('  ! at risk from a defect somebody in this cohort ran under, which is a');
        console.log('    separate question from pooling: the measure lines up across the eras,');
        console.log('    and it may simply be missing for the sessions flagged above.');
    }

    console.log('\n=== open decisions ===\n');
    const broken = rows.filter((r) => !r.verdict.ok && !(r.doc && r.held === 0 && r.gone));
    if (!broken.length) {
        console.log('  none. Every measure the cohort holds survives the eras it spans.');
    }
    for (const r of broken) {
        console.log(`  ${r.id} — ${r.verdict.why}`);
        if (r.id === 'ctl4') {
            console.log('    Recoverable. Earlier answers reflect onto today\'s footing with 8 - v.');
            console.log('    Options: (a) flip and pool, reporting the transform; (b) drop ctl4 and');
            console.log('    report the control construct on its other four items; (c) report the two');
            console.log('    wordings separately. (b) is the cheapest and loses least, because the');
            console.log('    construct mean is already computed without it.');
        } else if (r.gone) {
            console.log('    Nothing to recover: no answers were collected under the current version.');
            console.log('    Report as collected from part of the cohort only, or not at all.');
        } else if (r.confirmatory) {
            console.log('    PRE-REGISTERED. Split the cohort at the boundary and report the halves,');
            console.log('    or report only the side of the boundary the prediction still holds on.');
            console.log('    Pooling here without saying so is the one thing that cannot be undone.');
        }
        console.log('');
    }

    const unplaceable = people.filter((p) => p.unplaceable);
    if (unplaceable.length) {
        console.log('=== unplaceable, and they have to be settled by hand ===\n');
        for (const p of unplaceable) console.log(`  ${p.code}  ran ${iso(p.ranAt)}  ${p.why}`);
        console.log('\n  These ran inside a deploy window, so the commit time cannot say which');
        console.log('  version they saw. Firebase console → Hosting → Release history for');
        console.log('  codoc-11b10 gives the instant each build went live; that settles them.\n');
    }

    const weak = people.filter((p) => /weak/.test(p.ranAtFrom));
    if (weak.length) {
        console.log('=== placed on a weak timestamp ===\n');
        for (const p of weak) console.log(`  ${p.code}  by ${p.ranAtFrom}`);
        console.log('\n  No task clock arrived for these, so they are placed by when something was');
        console.log('  last written rather than by when somebody sat down. Check against the');
        console.log('  session zip or the researcher\'s schedule before trusting the era.\n');
    }
}

if (import.meta.url === `file://${process.argv[1]}`) {
    try { main(); } catch (err) { console.error(err.message); process.exit(1); }
}
