import { describe, it, expect } from 'vitest';
import { settingsFormat, settingsSectionLine } from '../state/settings-sections';

// A `codoc:` citation of a configured value has to land on the section holding it.
// These pin the two things that made it a no-op: a settings file is recognized as one,
// and its section is found by the header grammar the chunker used to name it — not by
// the last dot-segment of the path, which for `tool.pytest.ini_options` names nothing.
//
// Parity with `codoc/settings_files.py` is the contract: `_section_starts` decides what
// a chunk is CALLED, this decides where that name IS, and a disagreement is a citation
// that points at the wrong decision. `tests/test_settings_files.py` holds the other half.

const RULES = [
    '# Which date a summary lines up on.',
    '[periods]',
    'month = "made"',
    '',
    '[periods.week]',
    'starts = "monday"',
].join('\n');

describe('settingsFormat', () => {
    it('names the format of the files a repo keeps decisions in', () => {
        expect(settingsFormat('tally/rules.toml')).toBe('toml');
        expect(settingsFormat('deploy.YAML')).toBe('yaml');
        expect(settingsFormat('a/b.yml')).toBe('yaml');
        expect(settingsFormat('setup.cfg')).toBe('ini');
        expect(settingsFormat('rules.json')).toBe('json');
    });

    it('is null for code, so nothing changes for the citations that already worked', () => {
        expect(settingsFormat('codoc/loop/apply.py')).toBeNull();
        expect(settingsFormat('src/state/bridge.ts')).toBeNull();
        expect(settingsFormat('Makefile')).toBeNull();
    });
});

describe('settingsSectionLine', () => {
    it('finds a TOML section, qualified or bare', () => {
        expect(settingsSectionLine(RULES, 'tally/rules.toml::periods', 'toml')).toBe(1);
        expect(settingsSectionLine(RULES, 'periods', 'toml')).toBe(1);
    });

    it('finds a nested table by its WHOLE dotted name', () => {
        // The leaf rule would look for `week`, which is not a header in this file — the
        // reason this module exists rather than another branch on `symbolLeaf`.
        expect(settingsSectionLine(RULES, 'tally/rules.toml::periods.week', 'toml')).toBe(4);
        expect(settingsSectionLine(RULES, 'week', 'toml')).toBe(-1);
    });

    it('strips the quotes off a quoted table name, as the chunker does', () => {
        const text = '[tool."my.pkg".paths]\nroot = "."\n';
        expect(settingsSectionLine(text, 'tool.my.pkg.paths', 'toml')).toBe(0);
    });

    it('puts the keys above the first section at the top of the file', () => {
        expect(settingsSectionLine(RULES, 'tally/rules.toml::__module__', 'toml')).toBe(0);
    });

    it('numbers a repeated array-of-tables header the way the chunker numbers it', () => {
        const text = '[[servers]]\nname = "a"\n\n[[servers]]\nname = "b"\n';
        expect(settingsSectionLine(text, 'servers', 'toml')).toBe(0);
        expect(settingsSectionLine(text, 'servers[1]', 'toml')).toBe(3);
    });

    it('reports a miss instead of guessing at the nearest section', () => {
        // A stale citation must not move the editor: showing a reader `[periods]` when
        // the sentence claimed `[retries]` states something the file does not say.
        expect(settingsSectionLine(RULES, 'retries', 'toml')).toBe(-1);
    });

    it('finds an INI section, and ignores a bracketed line that is not a header', () => {
        const text = '[pytest]\naddopts = -q [not-a-header]\n\n[flake8]\nmax = 100\n';
        expect(settingsSectionLine(text, 'flake8', 'ini')).toBe(3);
        expect(settingsSectionLine(text, 'not-a-header', 'ini')).toBe(-1);
    });

    it('finds a top-level YAML key and not an indented one', () => {
        const text = 'jobs:\n  build:\n    runs-on: ubuntu\nname: CI\n';
        expect(settingsSectionLine(text, 'jobs', 'yaml')).toBe(0);
        expect(settingsSectionLine(text, 'name', 'yaml')).toBe(3);
        expect(settingsSectionLine(text, 'build', 'yaml')).toBe(-1);
    });

    it('finds a top-level JSON key by depth, not by looking like one', () => {
        // `month` is nested and `periods` is not; a per-line regex cannot tell them
        // apart and would send a reader who clicked `periods` to whichever came first.
        const text = '{\n  "periods": {\n    "month": "made"\n  },\n  "retries": 3\n}\n';
        expect(settingsSectionLine(text, 'periods', 'json')).toBe(1);
        expect(settingsSectionLine(text, 'retries', 'json')).toBe(4);
        expect(settingsSectionLine(text, 'month', 'json')).toBe(-1);
    });

    it('is not fooled by a brace or a colon inside a JSON value', () => {
        const text = '{\n  "tpl": "{a}: b",\n  "real": 1\n}\n';
        expect(settingsSectionLine(text, 'real', 'json')).toBe(2);
    });
});
