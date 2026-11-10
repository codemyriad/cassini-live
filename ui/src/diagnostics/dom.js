/**
 * DOM helpers shared by the page's views. Feed strings reach the DOM only as textContent
 * or attribute values; the page CSP is `style-src 'self'`, so sizes go through CSSOM,
 * never a style attribute.
 */

const SVG_NS = 'http://www.w3.org/2000/svg'

export const $ = (id) => document.getElementById(id)

export function setText(node, text) {
  const value = text === null || text === undefined ? '' : String(text)
  if (node.textContent !== value) node.textContent = value
}

export function setTone(node, tone) {
  if (node.dataset.tone !== tone) node.dataset.tone = tone
}

export function show(node, visible) {
  if (node.hidden === visible) node.hidden = !visible
}

export function setFraction(node, property, fraction) {
  node.style.setProperty(property, `${(fraction * 100).toFixed(2)}%`)
}

/** A custom property through CSSOM, touched only when it changes. */
export function setVar(node, name, value) {
  if (node.style.getPropertyValue(name) !== value) node.style.setProperty(name, value)
}

/** Attributes, each written only when its value changes. */
export function setAttrs(node, attrs) {
  for (const [name, raw] of Object.entries(attrs)) {
    const value = String(raw)
    if (node.getAttribute(name) !== value) node.setAttribute(name, value)
  }
}

export function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

export function svgEl(tag, className) {
  const node = document.createElementNS(SVG_NS, tag)
  if (className) node.setAttribute('class', className)
  return node
}

// Keep exactly `count` children made by `make`, reusing the ones there.
export function ensureChildren(parent, count, make) {
  while (parent.children.length < count) parent.appendChild(make())
  while (parent.children.length > count) parent.lastElementChild.remove()
  return Array.from(parent.children)
}
