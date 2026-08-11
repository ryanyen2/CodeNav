/**
 * codoc-config.test.ts — what the toolbar believes the tree's language is.
 *
 * This is the read the switcher's entire behaviour rests on. A switch that writes the
 * right value but keeps REPORTING the old one is indistinguishable, from the author's
 * seat, from a switch that did nothing — which is exactly how this shipped broken
 * once (the value was sourced from the daemon-written sidecar instead of the config
 * file, so it only caught up on the next render pass, and never at all without a
 * daemon). Switching back to English is covered explicitly, because 'en' is the value
 * most likely to be mistaken for "unset" by a falsy check somewhere.
 */
import { mkdtempSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { readDocLanguage } from '../state/codoc-config';

let dir: string;

beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'codoc-cfg-')); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

const write = (body: string) => writeFileSync(join(dir, 'config.json'), body, 'utf-8');

describe('readDocLanguage', () => {
    it('reads the configured language', () => {
        write(JSON.stringify({ doc_language: 'zh-Hans' }));
        expect(readDocLanguage(dir)).toEqual({
            code: 'zh-Hans', name: 'Simplified Chinese / 简体中文',
        });
    });

    it('reads English back as English, not as unset', () => {
        // The regression this guards: 'en' is falsy-adjacent in every "|| fallback"
        // chain, so a switch back to English is the one most likely to be silently
        // ignored — and it turns the language setting into a one-way door.
        write(JSON.stringify({ doc_language: 'zh-Hans' }));
        expect(readDocLanguage(dir).code).toBe('zh-Hans');
        write(JSON.stringify({ doc_language: 'en' }));
        expect(readDocLanguage(dir)).toEqual({ code: 'en', name: 'English' });
    });

    it('prefers the config over the sidecar, which the daemon writes later', () => {
        write(JSON.stringify({ doc_language: 'en' }));
        expect(readDocLanguage(dir, 'zh-Hans').code).toBe('en');
    });

    it('falls back to the sidecar when no config exists yet', () => {
        expect(readDocLanguage(dir, 'ja').code).toBe('ja');
    });

    it('falls back to English when there is neither', () => {
        expect(readDocLanguage(dir)).toEqual({ code: 'en', name: 'English' });
    });

    it('preserves other keys nothing here reads', () => {
        write(JSON.stringify({ doc_language: 'ko', something_else: 42 }));
        expect(readDocLanguage(dir).code).toBe('ko');
    });

    it('degrades to the default on a malformed or hostile config', () => {
        // Read on every repaint, so a hand-mangled file must not break the editor.
        for (const body of ['{not json', '', '[]', 'null', '{"doc_language": 7}']) {
            write(body);
            expect(readDocLanguage(dir, 'ja').code).toBe('ja');
        }
    });

    it('treats a blank tag as absent rather than as a language', () => {
        write(JSON.stringify({ doc_language: '   ' }));
        expect(readDocLanguage(dir, 'zh-Hant').code).toBe('zh-Hant');
    });

    it('names a language set from the CLI that has no built-in profile', () => {
        write(JSON.stringify({ doc_language: 'fr' }));
        expect(readDocLanguage(dir)).toEqual({ code: 'fr', name: 'fr' });
    });
});
