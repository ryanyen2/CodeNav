// What the Codoc Tree editor would draw for a given workspace, without opening it.
//
// The webview is a projection: it consumes `.codoc/tree.doc.json` plus the sidecar
// and derives every decoration from them. So the same modules the editor runs can be
// pointed at a workspace on disk and asked what they would produce. That turns "does
// the demo look right" into something checkable per beat, instead of something only a
// person squinting at a screen recording can answer.
//
//   npx tsx scripts/ui-report.ts <path-to-.codoc> [label]
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { claimsFor, type Claim } from '../src/state/settlement';
import { buildStages, featureTextsFrom, stagedProposals } from '../src/state/settlement-stages';
import { buildSuggestions, parseDocFile } from '../src/state/suggestion-model';
import { nodeStatus, statusGlyphs } from '../src/state/node-status';
import { featureState, railState, stateBadge } from '../src/state/feature-state';
import { consequenceOf } from '../src/state/grammar';

const dir = process.argv[2];
const label = process.argv[3] ?? 'state';
if (!dir) { console.error('usage: ui-report.ts <.codoc dir> [label]'); process.exit(2); }

const readJson = (name: string): any => {
    try { return JSON.parse(readFileSync(join(dir, name), 'utf8')); }
    catch { return null; }
};

const rawDoc = readJson('tree.doc.json');
// tree.bindings.json, not tree.index.json: the latter is the cross-reference
// registry (registry-loader.ts), and it carries none of the decoration slices.
const sidecar = readJson('tree.bindings.json');
if (!rawDoc || !sidecar) { console.error('missing tree.doc.json or tree.bindings.json'); process.exit(1); }

const docFile = parseDocFile(rawDoc);
if (!docFile) { console.error('tree.doc.json did not parse as a doc'); process.exit(1); }
const doc = docFile.doc;

const texts = featureTextsFrom(doc as any);
const titleOf = (fid: string) => texts.get(fid)?.title ?? '';
const descOf = (fid: string) => (texts.get(fid)?.paras ?? []).join('\n\n');

const suggestions = buildSuggestions(sidecar, titleOf, descOf);
const staged = stagedProposals(suggestions as any);
const stages = buildStages(doc as any, staged, sidecar.auto_edits ?? {}, sidecar.hold_detail ?? {});

const CHANNEL_MEANING: Record<string, string> = {
    human: 'BLUE ink (yours)',
    plan: 'GRAY opacity (a plan)',
    code: 'GREEN/RED ground (the code)',
};

const rows: string[] = [];
let claimed = 0;
const now = Date.now();

const holdDetail: Record<string, any> = sidecar.hold_detail ?? {};
// Comment threads are store-authoritative (the `comments` sidecar slice), which is
// why they survive a closed tab at all.
// The slice is keyed by feature id, so flatten it and keep the key: a thread's own
// `featureId` is not what the sidecar indexes it under.
const threads: any[] = Object.entries(sidecar.comments ?? {}).flatMap(
    ([fid, list]) => (Array.isArray(list) ? list : [list]).map((t: any) => ({ ...t, featureId: fid })));

// Every feature on the page, not just the ones with claims. A STEER hold produces no
// prose claim at all — a comment displaces no words, so `buildStages` never gives it a
// baseline and it never enters `stages` — but it still draws a margin card and a node
// marker. Iterating the stages alone reported that feature as untouched.
const allFids = [...texts.keys()];
for (const fid of allFids) {
    const st = stages[fid] ?? { projected: texts.get(fid)! };
    // The two layers the EDITOR owns rather than the daemon. `live` is the document
    // as it stands in the buffer: the projection with any plan materialized into it,
    // plus this host's own typing — and a report of a workspace nobody is typing in
    // has none of the latter. `committed` is true once the author pressed ⌘S, which
    // is exactly the state a held draft records: the daemon only ever hears about an
    // edit that was sent.
    //
    // So this reports what a reader sees on OPENING the document. The unsent state —
    // blue pulsing ink between typing and ⌘S — lives only in the editor's buffer and
    // reaches no file, so it cannot be checked from here.
    const withLive = {
        ...st,
        live: (st as any).planned ?? st.projected,
        committed: Boolean(holdDetail[fid]) && holdDetail[fid]?.origin !== 'plan',
    };
    const claims: Claim[] = claimsFor(withLive as any);
    const title = titleOf(fid) || fid;
    const held = Boolean(holdDetail[fid]);
    const sug = suggestions.filter(s => s.featureId === fid);

    const status = nodeStatus(claims, [], now);
    const glyphs = statusGlyphs(status).map(g => g.kind ?? JSON.stringify(g));
    const op = sug.find(s => ['amend', 'retire', 'add', 'move'].includes(s.kind));
    const signals = {
        proposalOp: (op?.kind as any) ?? null,
        proposalBuilds: op ? consequenceOf(op.writesCode, op.tag) === 'build' : undefined,
        autoEdit: Boolean((sidecar.auto_edits ?? {})[fid]),
        sent: held,
        holdOrigin: holdDetail[fid]?.origin === 'plan' ? 'plan' : 'human',
        queuedIntent: holdDetail[fid]?.intent,
        realized: sidecar.features?.[fid]?.realized,
    } as any;
    const state = featureState(signals);
    const rail = railState(signals);
    const badge = stateBadge(state, signals);

    if (!claims.length && !sug.length && !held) continue;
    claimed++;

    rows.push(`\n  ${title}  (${fid})`);
    if (sug.length) {
        for (const s of sug) {
            rows.push(`    proposal   ${s.kind.toUpperCase()} by ${s.originRole}` +
                      `${s.verdictPending ? ' [verdict pending — no buttons]' : ' [Accept / Reject offered]'}`);
        }
    }
    if (held) rows.push(`    hold       held draft — queued: "${holdDetail[fid]?.intent ?? ''}"`);
    for (const t of threads.filter((t: any) => t.featureId === fid)) {
        // The margin card. `inMargin` is what decides whether it still hangs beside the
        // prose: a resolved thread lingers about an hour and then leaves, so a report
        // taken later than that will correctly show the record without the card.
        // The sidecar is snake_case; the extension's CommentThread is camel. Read both
        // so the report cannot quietly show an empty anchor for a thread that has one.
        const anchor = t.anchor_text ?? t.anchorText ?? '';
        const refs = t.code_refs ?? t.codeRefs ?? [];
        rows.push(`    comment    ${String(t.status).toUpperCase()} "${(t.body ?? '').slice(0, 46)}"` +
                  `  scope=${t.scope ?? 'code'}`);
        rows.push(`               on "${anchor.slice(0, 52)}"` +
                  ` [${t.anchor_start ?? '?'}..${t.anchor_end ?? '?'}]`);
        rows.push(`               edit-only=${refs.length ? refs.join(', ') : 'whole feature'}` +
                  `  produced=${t.directive_id ?? 'nothing yet'}`);
    }
    const byChannel = new Map<string, string[]>();
    for (const c of claims) {
        const k = `${c.channel}:${c.channel === "code" ? `edit=${c.edit}` : `stage=${c.stage}`}`;
        byChannel.set(k, [...(byChannel.get(k) ?? []), "x"]);
    }
    for (const [k, v] of byChannel) {
        const [ch, where] = k.split(":");
        rows.push(`    claim      ${CHANNEL_MEANING[ch] ?? ch}  ${where}  (${v.length} span${v.length > 1 ? "s" : ""})`);
    }
    rows.push(`    marker     human=${status.human} plan=${status.plan} diff=${status.diff}` +
              `${glyphs.length ? `  glyphs=[${glyphs.join(', ')}]` : '  (no glyphs)'}`);
    rows.push(`    row/rail   state=${state} rail=${rail}` +
              `${badge ? ` badge="${badge.title}"` : " (no badge)"}`);
}

console.log(`\n=== UI report: ${label} ===`);
console.log(`features in doc: ${texts.size} · suggestions: ${suggestions.length} · ` +
            `auto_edits: ${Object.keys(sidecar.auto_edits ?? {}).length} · ` +
            `hold_detail: ${Object.keys(sidecar.hold_detail ?? {}).length}`);
if (!claimed) console.log('\n  (nothing decorated — every feature reads as settled prose)');
else console.log(rows.join('\n'));
console.log('');
