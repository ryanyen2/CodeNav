/**
 * terminal.ts: run a codoc command where the person can watch and interrupt it.
 *
 * `codoc realize` and `codoc serve` run as a task. A task starts codoc with
 * its arguments as a list, so no shell has to read a typed command line back:
 * a path with a space works the same under bash, cmd.exe and PowerShell. Its
 * terminal stays open after the command ends, so the person can read what the
 * command printed. (A terminal made with `createTerminal({ shellPath })` closes
 * the moment its process exits.)
 */
import * as vscode from 'vscode';

export function runInTerminal(codoc: string, name: string, args: string[], cwd: string): Thenable<vscode.TaskExecution> {
    const task = new vscode.Task({ type: 'codoc', command: name }, vscode.TaskScope.Workspace, name, 'codoc',
        new vscode.ProcessExecution(codoc, args, { cwd }));
    task.presentationOptions = { close: false };
    return vscode.tasks.executeTask(task);
}
