/** Sync the repo's canonical sources into the site:
 *   - ../docs/**\/*.md  -> src/content/docs/docs/   (H1 lifted into frontmatter,
 *     relative .md links resolved to site URLs: Astro renders them verbatim,
 *     so `](agents.md)` would 404 as /docs/adapters/agents.md)
 *   - ../packs/*.json   -> public/packs/            (the playground's data)
 * Run automatically before dev/build so the site can never drift from the repo.
 */
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = join(SITE, '..');
const SRC_DOCS = join(REPO, 'docs');
const DEST_DOCS = join(SITE, 'src', 'content', 'docs', 'docs');

function* mdFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* mdFiles(p);
    else if (name.endsWith('.md')) yield p;
  }
}

rmSync(DEST_DOCS, { recursive: true, force: true });
let count = 0;
for (const src of mdFiles(SRC_DOCS)) {
  const rel = relative(SRC_DOCS, src);
  let raw = readFileSync(src, 'utf8');
  // Strip any pre-existing frontmatter so we never double-wrap. We re-derive the
  // title below (from the H1, else the frontmatter title, else the filename).
  const fm = raw.match(/^---\n([\s\S]*?)\n---\n/);
  const fmTitle = fm && fm[1].match(/^title:\s*["']?(.+?)["']?\s*$/m);
  if (fm) raw = raw.slice(fm[0].length).replace(/^\s+/, '');
  const m = raw.match(/^#\s+(.+)\n/);
  const title = m ? m[1].trim() : fmTitle ? fmTitle[1].trim() : rel.replace(/\.md$/, '');
  let body = m ? raw.slice(m[0].length) : raw;
  // `](tiers.md)` / `](results/tmdb.md)` / `](../contract.md)` -> `/docs/.../`
  const srcDir = posix.dirname(rel.split(sep).join('/'));
  body = body.replace(/\]\((?!https?:|\/|#)([^)\s]+?)\.md(#[^)]*)?\)/g, (_all, target, hash = '') => {
    const resolved = posix.normalize(posix.join(srcDir === '.' ? '' : srcDir, target));
    return `](/docs/${resolved}/${hash})`;
  });
  const out = `---\ntitle: "${title.replace(/"/g, '\\"')}"\n---\n\n${body}`;
  const dest = join(DEST_DOCS, rel);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, out);
  count += 1;
}

const DEST_PACKS = join(SITE, 'public', 'packs');
rmSync(DEST_PACKS, { recursive: true, force: true });
mkdirSync(DEST_PACKS, { recursive: true });
cpSync(join(REPO, 'packs'), DEST_PACKS, { recursive: true });

console.log(`synced ${count} docs pages + packs into the site`);
