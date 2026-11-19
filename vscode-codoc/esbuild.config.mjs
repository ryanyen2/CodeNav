// Builds the three bundles in `dist/`. `--watch` rebuilds on change.
import esbuild from 'esbuild';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

const bundles = [
    // The extension host: Node, CommonJS, `vscode` provided by VS Code.
    { entryPoints: [join(here, 'src', 'extension.ts')], outfile: join(here, 'dist', 'extension.js'),
      format: 'cjs', platform: 'node', external: ['vscode'] },
    // The editor page. Its `import './app.css'` is emitted beside it as
    // dist/webview/app.css. The webview loads both, and so does the hub.
    { entryPoints: [join(here, 'src', 'web', 'editor', 'app.ts')], outfile: join(here, 'dist', 'webview', 'app.js'),
      format: 'iife', platform: 'browser' },
    // The page's host connection on the hub, which the hub loads before app.js.
    { entryPoints: [join(here, 'src', 'web', 'hub.ts')], outfile: join(here, 'dist', 'webview', 'hub.js'),
      format: 'iife', platform: 'browser' },
];

async function build() {
    const contexts = await Promise.all(bundles.map(b => esbuild.context({ ...b, bundle: true, sourcemap: true })));
    if (watch) {
        await Promise.all(contexts.map(c => c.watch()));
        console.log('Watching...');
    } else {
        await Promise.all(contexts.map(c => c.rebuild()));
        await Promise.all(contexts.map(c => c.dispose()));
    }
}

build().catch(() => process.exit(1));
