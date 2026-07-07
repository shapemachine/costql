/** Generate llms.txt + llms-full.txt into dist/ after the build, so any LLM
 * tool can ingest the docs (https://llmstxt.org convention). */
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = join(SITE, '..', 'docs');
const DIST = join(SITE, 'dist');

function* mdFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* mdFiles(p);
    else if (name.endsWith('.md')) yield p;
  }
}

/** Trim to <=max chars on a word boundary, adding an ellipsis if we cut. */
function clip(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  return `${cut.slice(0, cut.lastIndexOf(' ')).trimEnd()}…`;
}

const pages = [];
for (const p of [...mdFiles(DOCS)].sort()) {
  const rel = relative(DOCS, p).replace(/\.md$/, '');
  const raw = readFileSync(p, 'utf8');
  const title = raw.match(/^#\s+(.+)\n/)?.[1]?.trim() ?? rel;
  const firstPara = raw.split('\n\n').find((b) => b && !b.startsWith('#'))?.replace(/\n/g, ' ') ?? '';
  pages.push({ rel, title, firstPara, raw });
}

const index = [
  '# costQL',
  '',
  '> Price GraphQL queries before they run: calibrate a live API into a static',
  '> pricing pack, then quote any query offline: in Python or JavaScript. Cost-units',
  '> only, never dollars. No hosted service; the pack is a plain local file.',
  '',
  'Install: `pip install costql` (build + quote) · `npm install costql` (quote only)',
  '',
  '## Docs',
  '',
  ...pages.map((p) => `- [${p.title}](https://costql.com/docs/${p.rel}/): ${clip(p.firstPara, 160)}`),
  '',
  '## Source',
  '',
  '- [GitHub](https://github.com/shapemachine/costql): engine, adapters, demos, packs, conformance oracle',
].join('\n');

const full = [
  index,
  '',
  '---',
  '',
  ...pages.map((p) => p.raw),
].join('\n');

writeFileSync(join(DIST, 'llms.txt'), index);
writeFileSync(join(DIST, 'llms-full.txt'), full);
console.log(`wrote llms.txt (${pages.length} pages) + llms-full.txt`);
