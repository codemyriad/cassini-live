/**
 * Build the panel bundle and both bookmarklet flavours, and stage the files
 * the ExApp serves (cassini_live/static/panel/).
 *
 * Outputs:
 *   dist/panel.js                 minified IIFE bundle (also ExApp-served)
 *   dist/bookmarklet-inline.txt   whole bundle as one javascript: URL
 *   dist/bookmarklet-loader.txt   nonce loader as a javascript: URL
 *   ../cassini_live/static/panel/{panel.js,index.html,install.js}
 *
 * `--check` rebuilds into memory and fails when any committed output is
 * stale, mirroring the waveforms repo's check:bookmarklet gate.
 */

import { build } from 'esbuild'
import { minify } from 'terser'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const uiRoot = join(here, '..')
const distDir = join(uiRoot, 'dist')
const staticPanelDir = join(uiRoot, '..', 'cassini_live', 'static', 'panel')
const checkMode = process.argv.includes('--check')

const pkg = JSON.parse(await readFile(join(uiRoot, 'package.json'), 'utf8'))

// Browsers percent-decode javascript: URLs before running them. Escaping only
// % and # keeps the code intact: % so stray sequences aren't decoded, # so the
// URL isn't truncated as a fragment. (Same rule as bookmarklet-waveforms.)
function toBookmarklet(code) {
  return 'javascript:' + code.replaceAll('%', '%25').replaceAll('#', '%23')
}

async function bundle(entry, { iife = true } = {}) {
  const result = await build({
    entryPoints: [join(uiRoot, entry)],
    bundle: true,
    write: false,
    format: iife ? 'iife' : 'esm',
    target: ['chrome109', 'firefox115', 'safari16'],
    minify: true,
    legalComments: 'none',
    define: { __CASSINI_LIVE_UI_VERSION__: JSON.stringify(pkg.version) },
  })
  return result.outputFiles[0].text
}

async function squeeze(code) {
  const result = await minify(code, {
    compress: { passes: 2 },
    mangle: true,
    format: { comments: false },
  })
  return result.code
}

const panelBundle = await bundle('src/main.js')
const loaderBundle = await bundle('src/loader.js')

const banner = `/* Cassini Live transcript panel v${pkg.version} — https://github.com/codemyriad/cassini-live */\n`
const panelJs = banner + panelBundle
const inlineBookmarklet = toBookmarklet(await squeeze(panelBundle)) + '\n'
const loaderBookmarklet = toBookmarklet(await squeeze(loaderBundle)) + '\n'

const installJs = [
  banner,
  `window.CASSINI_LIVE_BOOKMARKLETS = {`,
  `  version: ${JSON.stringify(pkg.version)},`,
  `  loader: ${JSON.stringify(loaderBookmarklet.trim())},`,
  `  inline: ${JSON.stringify(inlineBookmarklet.trim())},`,
  `};`,
  await readFile(join(uiRoot, 'src', 'install-page.js'), 'utf8'),
].join('\n')

const installHtml = await readFile(join(uiRoot, 'src', 'install-page.html'), 'utf8')
const installCss = await readFile(join(uiRoot, 'src', 'install-page.css'), 'utf8')

const outputs = [
  [join(distDir, 'panel.js'), panelJs],
  [join(distDir, 'bookmarklet-inline.txt'), inlineBookmarklet],
  [join(distDir, 'bookmarklet-loader.txt'), loaderBookmarklet],
  [join(staticPanelDir, 'panel.js'), panelJs],
  [join(staticPanelDir, 'install.js'), installJs],
  [join(staticPanelDir, 'install.css'), installCss],
  [join(staticPanelDir, 'index.html'), installHtml],
]

if (checkMode) {
  let stale = false
  for (const [path, content] of outputs) {
    const existing = await readFile(path, 'utf8').catch(() => null)
    if (existing !== content) {
      console.error(`stale: ${path}`)
      stale = true
    }
  }
  if (stale) {
    console.error('Run `npm run build` in ui/ and commit the outputs.')
    process.exit(1)
  }
  console.log('build outputs are current')
} else {
  await mkdir(distDir, { recursive: true })
  await mkdir(staticPanelDir, { recursive: true })
  for (const [path, content] of outputs) {
    await writeFile(path, content)
  }
  const kb = (s) => `${(Buffer.byteLength(s) / 1024).toFixed(1)} KB`
  console.log(`panel.js ${kb(panelJs)}, inline bookmarklet ${kb(inlineBookmarklet)}, loader ${kb(loaderBookmarklet)}`)
}
