/**
 * output.ts — the one "codoc" output channel.
 *
 * Setup, provisioning and the daemon all log to it. It is created on first use
 * and shared, because `createOutputChannel` called twice with one name makes
 * two channels.
 */
import * as vscode from 'vscode';

let channel: vscode.OutputChannel | undefined;

export function output(): vscode.OutputChannel {
    return (channel ??= vscode.window.createOutputChannel('codoc'));
}
