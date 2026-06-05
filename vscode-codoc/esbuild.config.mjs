import esbuild from 'esbuild';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

async function build() {
  // 1. Extension host (Node/CommonJS).
  const ext = await esbuild.context({
    entryPoints: [join(__dirname, 'src', 'extension.ts')],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    outfile: join(__dirname, 'dist', 'extension.js'),
    external: ['vscode'],
    sourcemap: true,
  });

  // 2. Webview client (browser/IIFE). `import './doc-view.css'` emits a sibling
  //    dist/webview/doc-view.css that the custom editor links via asWebviewUri.
  const web = await esbuild.context({
    entryPoints: [join(__dirname, 'src', 'webview', 'doc-view.ts')],
    bundle: true,
    format: 'iife',
    platform: 'browser',
    outfile: join(__dirname, 'dist', 'webview', 'doc-view.js'),
    sourcemap: true,
  });

  if (watch) {
    await Promise.all([ext.watch(), web.watch()]);
    console.log('Watching...');
  } else {
    await Promise.all([ext.rebuild(), web.rebuild()]);
    await Promise.all([ext.dispose(), web.dispose()]);
  }
}

build().catch(() => process.exit(1));
