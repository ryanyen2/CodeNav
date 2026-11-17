/**
 * shell.ts: one command line for the integrated terminal.
 *
 * codoc's long-running commands (`realize`, `sync`, `serve`) run in a terminal
 * so the person can watch and interrupt them, which means an argument vector
 * has to become text a shell reads back as the same arguments.
 *
 * No `vscode` import, so vitest can load it.
 */

/** Quote one argument, only when it needs it. A POSIX shell gets single
 *  quotes. On Windows the argument is wrapped in double quotes, which cmd.exe
 *  reads; PowerShell does not run a quoted path without `&`. */
export function shellQuote(arg: string, platform: NodeJS.Platform = process.platform): string {
    if (platform === 'win32') return /[\s&|<>^]/.test(arg) ? `"${arg}"` : arg;
    return /[^\w@%+=:,./-]/.test(arg) ? `'${arg.replace(/'/g, `'\\''`)}'` : arg;
}

export function commandLine(argv: readonly string[], platform: NodeJS.Platform = process.platform): string {
    return argv.map(a => shellQuote(a, platform)).join(' ');
}
