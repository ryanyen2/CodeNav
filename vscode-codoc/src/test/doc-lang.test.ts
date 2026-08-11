/**
 * doc-lang.test.ts — the authoring-language display contract.
 *
 * Two things are worth pinning here. First the `lang` policy: the attribute is what
 * the browser reads for per-element font fallback and line-breaking, and it
 * inherits — so stamping the majority case would be noise that buries the
 * exceptions the attribute exists to mark. Second the parity of the language table
 * with `codoc/doclang.py`: a profile added on the Python side and missed here would
 * silently vanish from the switcher, with nothing failing.
 */
import { readFileSync } from 'fs';
import { join } from 'path';
import { describe, it, expect } from 'vitest';
import {
    DOC_LANGUAGE_CHOICES, langAttrFor, languageName, shortLanguageLabel,
} from '../webview/doc-lang';

describe('langAttrFor — tag only the exceptions', () => {
    it('returns null when the node matches the tree, so `lang` inherits', () => {
        expect(langAttrFor(undefined, 'zh-Hans')).toBeNull();
        expect(langAttrFor('zh-Hans', 'zh-Hans')).toBeNull();
    });

    it('returns the node tag when it differs from the tree', () => {
        expect(langAttrFor('en', 'zh-Hans')).toBe('en');
        expect(langAttrFor('zh-Hans', 'en')).toBe('zh-Hans');
    });

    it('treats blank and whitespace-only as absent, never as a language', () => {
        expect(langAttrFor('', 'zh-Hans')).toBeNull();
        expect(langAttrFor('   ', 'zh-Hans')).toBeNull();
    });
});

describe('labels', () => {
    it('uses the endonym, which a reader of that language recognizes untranslated', () => {
        expect(shortLanguageLabel('zh-Hans')).toBe('简体中文');
        expect(shortLanguageLabel('ja')).toBe('日本語');
        expect(shortLanguageLabel('ko')).toBe('한국어');
        expect(shortLanguageLabel('en')).toBe('English');
    });

    it('falls back to the raw tag for a language set from the CLI', () => {
        // `codoc lang fr` is valid — the menu is not the limit — so the UI must be
        // able to name a language it has no entry for rather than render blank.
        expect(shortLanguageLabel('fr')).toBe('fr');
        expect(languageName('fr')).toBe('fr');
    });

    it('never returns empty, which would render an unlabelled button', () => {
        for (const code of ['', 'zz', 'zh-Hant']) {
            expect(shortLanguageLabel(code).length).toBeGreaterThan(0);
        }
    });
});

describe('parity with codoc/doclang.py', () => {
    const source = readFileSync(
        join(__dirname, '..', '..', '..', 'codoc', 'doclang.py'), 'utf-8');

    it('offers exactly the tags that have a bespoke Python profile', () => {
        // The Python profiles are the authority: each `_PROFILES` entry declares its
        // own `code=`, so scraping those is a direct read of the source of truth
        // rather than a second list to keep in step.
        const profiles = source.slice(source.indexOf('_PROFILES: dict[str, DocLanguage]'));
        const body = profiles.slice(0, profiles.indexOf('\n}\n'));
        const codes = [...body.matchAll(/code="([^"]+)"/g)].map(m => m[1]);

        expect(codes.length).toBeGreaterThan(1);
        expect(DOC_LANGUAGE_CHOICES.map(c => c.code).sort()).toEqual(codes.sort());
    });

    it('keeps English first so the default reads as the default', () => {
        expect(DOC_LANGUAGE_CHOICES[0].code).toBe('en');
    });
});
