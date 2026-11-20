import { describe, expect, it, vi } from 'vitest';
import * as vscode from 'vscode';

import { runInTerminal } from '../../host/terminal';

vi.mock('vscode', () => ({
    Task: class {
        presentationOptions = {};
        constructor(public definition: unknown, public scope: unknown, public name: string,
                    public source: string, public execution: unknown) {}
    },
    ProcessExecution: class {
        constructor(public process: string, public args: string[], public options: unknown) {}
    },
    TaskScope: { Workspace: 2 },
    tasks: { executeTask: vi.fn(async () => ({})) },
}));

describe('runInTerminal', () => {
    it('starts codoc with its arguments as a list and keeps the terminal open', async () => {
        const codoc = 'C:\\Program Files\\codoc\\codoc.exe';
        await runInTerminal(codoc, 'realize', ['realize'], 'C:\\My Repo');
        const task = vi.mocked(vscode.tasks.executeTask).mock.calls[0][0];
        // No shell reads a command line back, so nothing is quoted.
        expect(task.execution).toEqual({ process: codoc, args: ['realize'], options: { cwd: 'C:\\My Repo' } });
        expect(task.presentationOptions.close).toBe(false);
    });
});
