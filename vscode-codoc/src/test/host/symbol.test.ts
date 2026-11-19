import { describe, expect, it } from 'vitest';

import { symbolOffset } from '../../host/symbol';

describe('symbolOffset', () => {
    const ts = 'class Ky {\n    async #run() {}\n    go() { this.#run(); }\n}\n';

    it('finds a private member, whose name starts with #', () => {
        expect(symbolOffset(ts, 'ky.ts::Ky.#run')).toBe(ts.indexOf('#run'));
    });

    it('does not take a private member for a public one of the same name', () => {
        expect(symbolOffset(ts, 'ky.ts::Ky.run')).toBe(-1);
    });

    it('finds a name that starts with $', () => {
        const js = 'const a = 1;\nconst $el = a;\n';
        expect(symbolOffset(js, 'a.js::$el')).toBe(js.indexOf('const $el'));
    });

    it('prefers the definition and matches the name whole', () => {
        const py = 'x = resend\ny = send\ndef send(): ...\n';
        expect(symbolOffset(py, 'c.py::Client.send')).toBe(py.indexOf('def send'));
        expect(symbolOffset('x = resend\n', 'c.py::send')).toBe(-1);
    });

    it('a whole file has no offset', () => {
        expect(symbolOffset(ts, 'ky.ts')).toBe(-1);
    });
});
