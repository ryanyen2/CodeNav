/**
 * recorded-frames.test.ts — the recorded study session, read by the extension.
 *
 * The study replays a recorded agent session into a participant's workspace, and
 * everything the participant then sees is the extension's projection of the
 * `.codoc/` files in those frames. The unit tests prove the projection is correct
 * for documents this repo wrote. This proves it is correct for the documents the
 * DAEMON wrote during the recording, which is what participants actually get.
 *
 * Two things are checked per frame that carries a description.
 *
 * The extension's render of the daemon's `tree.doc.json` has to reproduce the
 * daemon's own `tree.codoc` byte for byte. A mismatch means the webview would
 * open on text that differs from the export, so the first settle after opening
 * would emit commands nobody typed, and the participant would be recorded as
 * having edited a tree they had only looked at.
 *
 * Every proposal in the frame has to resolve to a suggestion. A proposal that
 * does not is a change the participant is never offered a verdict on, which in a
 * study about reviewing an agent's work is the whole measurement going missing.
 *
 * The test skips when no recording is present, because frames are built by
 * `replay/record-session.sh` and are not in the repository.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync, readdirSync } from 'fs';
import { resolve, join } from 'path';
import { parseDocFile, codeAheadSuggestions } from '../state/suggestion-model';
import { descriptionBlocksForFid, blocksToDescriptionText } from '../state/pm-doc';
import { renderTreeFromDoc } from '../state/doc-serialize';
import type { PMNode } from '../state/pm-doc';

// `CODOC_FRAMES` points this at a recording somewhere else, which is how a
// recording is checked while it is being made rather than after it ships.
const FRAMES = process.env.CODOC_FRAMES
    ? resolve(process.env.CODOC_FRAMES)
    : resolve(__dirname, '../../../docs/study-materials/replay/frames');

type Frame = { dir: string; docJson: string; treeCodoc: string | null; sidecar: string | null };

/** Every frame of every recording that carries a description, newest state last. */
function recordedFrames(): { name: string; frames: Frame[] }[] {
    if (!existsSync(FRAMES)) return [];
    const out: { name: string; frames: Frame[] }[] = [];
    for (const project of readdirSync(FRAMES)) {
        for (const arm of readdirSync(join(FRAMES, project))) {
            const root = join(FRAMES, project, arm);
            if (!existsSync(join(root, 'manifest.json'))) continue;
            const frames: Frame[] = [];
            for (const entry of readdirSync(root).sort()) {
                if (!/^\d+$/.test(entry) && entry !== 'base') continue;
                const dir = join(root, entry);
                const docJson = join(dir, '.codoc', 'tree.doc.json');
                if (!existsSync(docJson)) continue;
                const treeCodoc = join(dir, '.codoc', 'tree.codoc');
                const sidecar = join(dir, '.codoc', 'tree.index.json');
                frames.push({
                    dir: `${project}/${arm}/${entry}`,
                    docJson,
                    treeCodoc: existsSync(treeCodoc) ? treeCodoc : null,
                    sidecar: existsSync(sidecar) ? sidecar : null,
                });
            }
            if (frames.length) out.push({ name: `${project}/${arm}`, frames });
        }
    }
    return out;
}

/** Feature id to its heading title, the settled text an amend diff is against. */
function headingsOf(doc: PMNode): Map<string, string> {
    const out = new Map<string, string>();
    for (const node of doc.content ?? []) {
        if (node.type !== 'featureHeading') continue;
        const fid = String((node.attrs ?? {}).fid ?? '');
        if (fid) out.set(fid, String((node.attrs ?? {}).title ?? ''));
    }
    return out;
}

const RECORDINGS = recordedFrames();

describe.skipIf(RECORDINGS.length === 0)('the recorded session, as the extension reads it', () => {
    for (const recording of RECORDINGS) {
        it(`${recording.name}: every frame's document parses`, () => {
            for (const frame of recording.frames) {
                const parsed = parseDocFile(JSON.parse(readFileSync(frame.docJson, 'utf8')));
                expect(parsed, `${frame.dir} did not parse`).not.toBeNull();
                expect(parsed!.doc.type).toBe('doc');
                expect((parsed!.doc.content ?? []).length,
                    `${frame.dir} projected an empty tree`).toBeGreaterThan(0);
            }
        });

        it(`${recording.name}: the extension's render matches the daemon's export`, () => {
            for (const frame of recording.frames) {
                if (!frame.treeCodoc) continue;
                const parsed = parseDocFile(JSON.parse(readFileSync(frame.docJson, 'utf8')))!;
                const ours = renderTreeFromDoc(parsed.doc);
                const theirs = readFileSync(frame.treeCodoc, 'utf8');
                expect(ours.trimEnd(), `${frame.dir} renders differently from the daemon`)
                    .toBe(theirs.trimEnd());
            }
        });

        it(`${recording.name}: every proposal the daemon left resolves to a verdict`, () => {
            let seen = 0;
            for (const frame of recording.frames) {
                if (!frame.sidecar) continue;
                const parsed = parseDocFile(JSON.parse(readFileSync(frame.docJson, 'utf8')))!;
                const sidecar = JSON.parse(readFileSync(frame.sidecar, 'utf8'));
                const byFeature = sidecar?.proposals?.by_feature ?? {};
                const count = Object.keys(byFeature).length
                    + (sidecar?.proposals?.adds ?? []).length;
                if (!count) continue;
                const headings = headingsOf(parsed.doc);
                const titleOf = (fid: string) => headings.get(fid) ?? '';
                const descOf = (fid: string) =>
                    blocksToDescriptionText(descriptionBlocksForFid(parsed.doc, fid) ?? []);
                const suggestions = codeAheadSuggestions(sidecar, titleOf, descOf);
                expect(suggestions.length,
                    `${frame.dir} has ${count} proposal(s) and offers no verdict`)
                    .toBeGreaterThan(0);
                for (const s of suggestions) {
                    expect(s.eventId, `${frame.dir} has a proposal with no event id`).toBeTruthy();
                    expect(['amend', 'add', 'move', 'retire']).toContain(s.kind);
                }
                seen += count;
            }
            // Not an assertion that proposals exist. A recording where Loop A
            // raised none is a finding about codoc, and the study reports it
            // rather than the test refusing it.
            if (seen === 0) console.log(`  ${recording.name}: the daemon raised no proposals`);
        });
    }
});
