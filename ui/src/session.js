/**
 * Conversation/call scoping for the transcript (D-830).
 *
 * Watches the room token and call token (via the tap). On a *new call* — a call
 * starting (call token appearing) or a switch to a different conversation while
 * in a call — it resets the store and seeds it with that conversation's
 * catch-up history, so:
 *  - two conversations never mix in one pane;
 *  - a late joiner sees what was said before they arrived, then a "You joined
 *    here" divider, then the live continuation;
 *  - a new call in the same conversation starts fresh, while an ended call stays
 *    on screen (no reset when the call token merely disappears) until the viewer
 *    navigates away.
 */

const POLL_MS = 1000

export function createSession({ tap, store, fetchHistory, onChange = () => {} }) {
  let prevRoom = null
  let prevCall = null
  let seedToken = 0 // guards against a slow history fetch landing after a newer reset
  let timer = null
  let stopped = false

  async function newCall(roomToken) {
    const mine = ++seedToken
    store.reset()
    onChange({ roomToken, inCall: true })
    const entries = await fetchHistory(roomToken)
    if (stopped || mine !== seedToken) return // a newer call superseded this fetch
    store.seedHistory(entries)
  }

  function poll() {
    if (stopped) return
    const roomToken = tap.roomToken()
    const callToken = tap.callToken()
    const inCall = Boolean(callToken)
    const callStarted = inCall && !prevCall // call token appeared
    const roomChanged = roomToken !== prevRoom
    if (callStarted || (inCall && roomChanged)) {
      newCall(roomToken)
    }
    prevRoom = roomToken
    prevCall = callToken
  }

  timer = setInterval(poll, POLL_MS)
  poll()

  return {
    get inCall() {
      return Boolean(tap.callToken())
    },
    roomToken: () => tap.roomToken(),
    destroy() {
      stopped = true
      if (timer) clearInterval(timer)
    },
  }
}
