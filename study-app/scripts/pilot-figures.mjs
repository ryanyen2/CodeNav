// Draw every figure from an exported session, so the pilot is read rather than
// assumed.
//
//   node scripts/pilot-figures.mjs <exported.json> <outdir>
//
// This is the loop the study depends on closing: logger → mirror → Firestore →
// export → figure. If a figure comes out empty here, it would have come out
// empty after twelve people.
import { JSDOM } from 'jsdom';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

const [file, outDir] = process.argv.slice(2);
if (!file) { console.error('usage: node scripts/pilot-figures.mjs <exported.json> [outdir]'); process.exit(2); }

const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
global.XMLSerializer = dom.window.XMLSerializer;

const { timeShare, timeProfile } = await import('../figures/timeprofile.js');
const { authorship, provenance } = await import('../figures/provenance.js');
const { transitionLift, mediation } = await import('../figures/mediation.js');
const { serialize } = await import('../figures/export.js');

const data = JSON.parse(readFileSync(file, 'utf8'));
const sessions = Object.entries(data.sessions || {}).map(([condition, s]) => ({
    code: data.code, condition, actions: s.actions || [],
}));

const out = outDir || '.';
mkdirSync(out, { recursive: true });

const byCondition = {};
for (const c of ['codoc', 'baseline']) {
    byCondition[c] = transitionLift(sessions.filter((s) => s.condition === c));
}
const minutes = (s) => Math.round((s.actions[s.actions.length - 1].t - s.actions[0].t) / 60000);

const figs = {
    timeprofile: timeProfile(['codoc', 'baseline'].map((c) => {
        const of = sessions.filter((s) => s.condition === c);
        return {
            condition: c, n: of.length,
            medianMinutes: of.length ? minutes(of[0]) : null,
            profile: timeShare(of),
        };
    })),
    provenance: provenance(authorship(sessions)),
    mediation: mediation(byCondition),
};

for (const [name, node] of Object.entries(figs)) {
    writeFileSync(join(out, `${name}.svg`), serialize(node));
}

// What the figures are made of, said in words, because an SVG that renders is
// not the same as an SVG that means anything.
const rows = authorship(sessions);
console.log('\nWho wrote what');
for (const r of rows) {
    console.log(`  ${r.condition.padEnd(9)} description: ${r.humanDoc} by them, `
        + `${r.agentDoc} by the agent`
        + `${r.humanShareOfDoc == null ? '  (nobody wrote to it)'
            : `  (${Math.round(r.humanShareOfDoc * 100)}% theirs)`}`);
    console.log(`  ${''.padEnd(9)} code:        ${r.humanCode} by them, ${r.agentCode} by the agent`);
}

console.log('\nWhich moves follow which  (log2 observed / expected)');
for (let i = 0; i < byCondition.codoc.length; i += 1) {
    const a = byCondition.codoc[i];
    const b = byCondition.baseline[i];
    const fmt = (r) => (r && r.lift != null ? r.lift.toFixed(2).padStart(6) : '     —');
    console.log(`  ${fmt(a)}  ${fmt(b)}   ${a.label}`);
}
console.log('\n  (first column codoc, second CLAUDE.md; — means the pair could not occur)');

const empty = Object.entries(figs).filter(([, n]) => n.querySelectorAll('rect,path,circle').length < 3);
console.log(`\nFigures written to ${out}`);
console.log(empty.length ? `  EMPTY: ${empty.map(([n]) => n).join(', ')}` : '  all three have marks');
