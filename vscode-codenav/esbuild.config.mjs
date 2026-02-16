import esbuild from 'esbuild';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

async function build() {
  const ctx = await esbuild.context({
    entryPoints: [join(__dirname, 'src', 'extension.ts')],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    outfile: join(__dirname, 'dist', 'extension.js'),
    external: ['vscode'],
    sourcemap: true,
  });
  if (watch) {
    await ctx.watch();
    console.log('Watching...');
  } else {
    await ctx.rebuild();
    await ctx.dispose();
  }
}

build().catch(() => process.exit(1));
