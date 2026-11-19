import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { daemonRunning, parsePid, readPid } from '../../host/pidfile';

describe('parsePid', () => {
    it('reads the bare decimal pid the daemon writes', () => {
        expect(parsePid('12345')).toBe(12345);
        expect(parsePid('  6789\n')).toBe(6789);
    });

    it('names no pid for anything else', () => {
        expect(parsePid('')).toBeUndefined();
        expect(parsePid('   \n')).toBeUndefined();
        expect(parsePid('0')).toBeUndefined();
        expect(parsePid('-3')).toBeUndefined();
        expect(parsePid('12.5')).toBeUndefined();
        expect(parsePid('not-a-number')).toBeUndefined();
        expect(parsePid('{"pid": 999}')).toBeUndefined();
    });
});

describe('the pidfile on disk', () => {
    let codocDir: string;
    beforeEach(() => { codocDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codoc-pidfile-')); });
    afterEach(() => { fs.rmSync(codocDir, { recursive: true, force: true }); });

    it('is absent until a daemon writes it', () => {
        expect(readPid(codocDir)).toBeUndefined();
    });

    it('is read from watch.pid', () => {
        fs.writeFileSync(path.join(codocDir, 'watch.pid'), '4242');
        expect(readPid(codocDir)).toBe(4242);
    });
});

describe('daemonRunning', () => {
    let codocDir: string;
    beforeEach(() => { codocDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codoc-pidfile-')); });
    afterEach(() => { fs.rmSync(codocDir, { recursive: true, force: true }); });
    const names = (pid: number | undefined) => fs.writeFileSync(path.join(codocDir, 'watch.pid'), String(pid));

    it('is false with no pidfile', () => {
        expect(daemonRunning(codocDir)).toBe(false);
    });

    it('is false for a live pid another program has been given', () => {
        names(process.pid);
        expect(daemonRunning(codocDir)).toBe(false);
    });

    it.skipIf(process.platform === 'win32')('is true while a codoc watch runs, and false once it has exited', async () => {
        const watch = cp.spawn(process.execPath, ['-e', 'setTimeout(() => {}, 30000)', 'codoc', 'watch']);
        try {
            names(watch.pid);
            expect(daemonRunning(codocDir)).toBe(true);
        } finally {
            const exited = new Promise(resolve => watch.once('exit', resolve));
            watch.kill();
            await exited;
        }
        expect(daemonRunning(codocDir)).toBe(false);
    });
});
