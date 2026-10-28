import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const css = readFileSync(new URL('../../src/diagnostics/app.css', import.meta.url), 'utf8')

function tokens(block) {
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((m) => [m[1], m[2].toLowerCase()]))
}

const dark = tokens(css.match(/^:root\s*\{([^}]*)\}/m)[1])
const THEMES = {
  dark,
  light: { ...dark, ...tokens(css.match(/@media \(prefers-color-scheme: light\)\s*\{\s*:root\s*\{([^}]*)\}/)[1]) },
}

const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
const linear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)

function luminance(hex) {
  const [r, g, b] = channels(hex).map(linear)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a, b) {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

// CIELAB under D65.
function lab(hex) {
  const [r, g, b] = channels(hex).map(linear)
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116)
  const x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047)
  const y = f(0.2126 * r + 0.7152 * g + 0.0722 * b)
  const z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)]
}

const distance = (a, b) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]))

test('the lag figure turns from gold to amber visibly in both themes', () => {
  // The video's own pair (the dark tokens) is ΔE 20 apart.
  for (const [name, theme] of Object.entries(THEMES)) {
    assert.ok(distance(theme.gold, theme.amber) >= 15, `${name}: gold ${theme.gold} vs amber ${theme.amber}`)
  }
})

test('light state hues keep 3:1 on the page and panels and differ in lightness', () => {
  const theme = THEMES.light
  for (const hue of ['cyan', 'waiting', 'gold', 'amber', 'red']) {
    for (const ground of ['bg', 'panel']) {
      assert.ok(contrast(theme[hue], theme[ground]) >= 3, `${hue} on ${ground}`)
    }
  }
  const lightness = ['cyan', 'red', 'waiting', 'paused-bg'].map((name) => lab(theme[name])[0])
  for (let i = 1; i < lightness.length; i++) assert.ok(lightness[i] - lightness[i - 1] >= 10, `L* ${lightness}`)
})

test('the four lane states differ in lightness, not only in hue, in both themes', () => {
  // Captioned, held (its outline), paused (its fill) and lost, as the lane band draws them.
  for (const [name, theme] of Object.entries(THEMES)) {
    const lightness = ['cyan', 'waiting', 'paused-bg', 'red'].map((token) => lab(theme[token])[0]).sort((a, b) => a - b)
    for (let i = 1; i < lightness.length; i++) assert.ok(lightness[i] - lightness[i - 1] >= 10, `${name}: L* ${lightness}`)
  }
  // Held and paused keep their edges visible on the lane: 3:1 on the panel.
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const edge of ['waiting', 'amber']) assert.ok(contrast(theme[edge], theme.panel) >= 3, `${name}: ${edge} on panel`)
  }
})

// The colour token of a rule as app.css writes it: `<selector> { … color: var(--token); … }`.
function ruleColor(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = css.match(new RegExp(`^${escaped}\\s*\\{[^}]*?[\\s;{]color:\\s*var\\(--([\\w-]+)\\)`, 'm'))
  assert.ok(match, selector)
  return match[1]
}

test('the lag graph legend words keep 4.5:1 on the page; gold only keys the ticks', () => {
  // 12 px words beside the graph, on the page, need AA text contrast; the ▮ key is a graphic.
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const selector of ['.graph-legend .shed', '.graph-legend .admit', '.graph-legend .sent']) {
      const token = ruleColor(selector)
      assert.ok(contrast(theme[token], theme.bg) >= 4.5, `${name}: ${selector} (${token}) on bg`)
    }
    assert.ok(contrast(theme[ruleColor('.graph-legend .key')], theme.bg) >= 3, `${name}: the caption-sent key on bg`)
    // The graph's "lag" label sits on the graph's panel.
    assert.ok(contrast(theme[ruleColor('.graph-label')], theme.panel) >= 4.5, `${name}: the lag label on panel`)
  }
})

test('text tokens keep 4.5:1 on the page and panels; the outline only draws edges', () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const text of ['text-main', 'text-soft', 'text-dim']) {
      for (const ground of ['bg', 'panel']) {
        assert.ok(contrast(theme[text], theme[ground]) >= 4.5, `${name}: ${text} on ${ground}`)
      }
    }
  }
  // The dark outline is 2.65:1 on the page.
  assert.doesNotMatch(css, /(^|[\s;{])color:\s*var\(--outline\)/m)
})
