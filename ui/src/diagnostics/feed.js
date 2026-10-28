/**
 * The page's side of the feed protocol: the poll URL, the retry schedule and the page's
 * own copy of the history columns. Pure: no DOM, no timers.
 */

// The feed samples once a second; the page asks again a second after each answer.
export const POLL_MS = 1000
const BACKOFF_FIRST_MS = 2000
const BACKOFF_MAX_MS = 30000
// Two minutes of one-second columns (diagnostic_history.COLUMNS), for an answer without one.
const WINDOW = 120

/** The feed's path relative to the page, so it resolves under the AppAPI proxy prefix. */
export function feedPath(since) {
  return `api/feed?since=${encodeURIComponent(String(since))}`
}

/** The wait after `failures` failed polls in a row: 2, 4, 8 and 16 s, then 30 s. */
export function backoffMs(failures) {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_FIRST_MS * 2 ** Math.max(0, failures - 1))
}

/** The server's monotonic clock went back: Cassini Live restarted and its columns start over. */
export function restarted(lastNow, now) {
  return typeof lastNow === 'number' && typeof now === 'number' && now < lastNow
}

/**
 * Where the feed's own history begins, in server seconds, after `body`: an answer with head 0
 * holds no columns, so the feed dropped them (nobody polled for 30 s) or Cassini Live
 * restarted, and its next columns count from a fresh first sample. Null while the page has
 * seen no such answer.
 */
export function historyStart(start, body) {
  return body.head === 0 ? body.now : start
}

/**
 * The page's columns that belong to the feed's own history: the video's `history`. Older
 * columns stay in the ring, but the seconds between them and the feed's restart were never
 * counted.
 */
export function feedHistory(columns, start) {
  return typeof start === 'number' ? columns.filter((column) => column.t > start) : columns
}

/**
 * The columns after an answer: the new ones appended in time order, then at most `window`
 * of them, none older than `window` seconds of server time. The feed sends one column per
 * second, but a paused tab or an idle feed leaves gaps, so age is trimmed as well as count.
 */
export function mergeColumns(ring, incoming, window, now) {
  const last = ring.length ? ring[ring.length - 1].t : -Infinity
  const added = (Array.isArray(incoming) ? incoming : [])
    .filter((column) => column && typeof column.t === 'number' && column.t > last)
    .sort((a, b) => a.t - b.t)
  const size = Number.isInteger(window) && window > 0 ? window : WINDOW
  const kept = ring.concat(added).slice(-size)
  return typeof now === 'number' ? kept.filter((column) => column.t > now - size) : kept
}
