import { describe, expect, it } from 'vitest';

import { commandLine, shellQuote } from '../../host/shell';

describe('shellQuote', () => {
    it('leaves a plain argument alone', () => {
        expect(shellQuote('/usr/bin/codoc', 'darwin')).toBe('/usr/bin/codoc');
        expect(shellQuote('C:\\tools\\codoc.exe', 'win32')).toBe('C:\\tools\\codoc.exe');
    });

    it('single-quotes for a POSIX shell, escaping a quote inside', () => {
        expect(shellQuote('/My Repo/x', 'linux')).toBe(`'/My Repo/x'`);
        expect(shellQuote(`it's`, 'darwin')).toBe(`'it'\\''s'`);
        expect(shellQuote('$HOME/x', 'darwin')).toBe(`'$HOME/x'`);
    });

    it('double-quotes on Windows', () => {
        expect(shellQuote('C:\\My Repo\\codoc.exe', 'win32')).toBe('"C:\\My Repo\\codoc.exe"');
    });
});

describe('commandLine', () => {
    it('joins quoted arguments', () => {
        expect(commandLine(['/v/codoc', 'serve', '--root', '/My Repo', '--port', '8787'], 'darwin'))
            .toBe(`/v/codoc serve --root '/My Repo' --port 8787`);
    });
});
