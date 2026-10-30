/**
 * Conversation/call scoping and the gap-fill poll (D-830).
 *
 * Liveness comes from the HPB feed (see tap.js); this polls `/history` only to
 * fill gaps the live feed missed, to deliver joins and the initial catch-up, and
 * to confirm the contiguous seq. It asks `since=<confirmedSeq>` — the last
 * contiguous seq the store holds — so a gap is re-requested rather than skipped.
 *
 * Scoping:
 *  - a new call (call token appears) or a switch to another conversation resets
 *    the store, enables captions for this session (onCallStart), and restarts
 *    polling — two conversations never mix, a new call in the same room starts
 *    fresh;
 *  - when a call ends the poll stops but the transcript stays on screen until
 *    the viewer navigates away (which resets via a room change).
 */

// Poll cadence: the HPB feed carries liveness, so this is just a gap/joins
// backstop — a few seconds is plenty and keeps load off Talk's OCS.
const WATCH_MS = 1000
const POLL_MS = 3000

export function createSession({ tap, store, fetchHistory, onCallStart = () => {}, onChange = () => {} }) {
  let prevRoom = null
  let prevCall = null
  let activeRoom = null
  let generation = 0
  let watchTimer = null
  let pollTimer = null
  let stopped = false

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  async function pollOnce(room, mine) {
    if (stopped || mine !== generation) return
    const { entries } = await fetchHistory(room, store.confirmedSeq)
    if (stopped || mine !== generation) return
    store.applyPoll(entries)
    pollTimer = setTimeout(() => pollOnce(room, mine), POLL_MS)
  }

  function startCall(room) {
    generation++
    activeRoom = room
    stopPolling()
    store.reset()
    onChange({ roomToken: room, inCall: true })
    onCallStart(room) // turn captions on so this session is on the HPB feed
    pollOnce(room, generation)
  }

  function endPolling() {
    generation++ // invalidate any in-flight poll
    stopPolling()
    activeRoom = null
  }

  function watch() {
    if (stopped) return
    const roomToken = tap.roomToken()
    const callToken = tap.callToken()
    const inCall = Boolean(callToken)
    const callStarted = inCall && !prevCall
    const roomChanged = roomToken !== prevRoom
    if (callStarted || (inCall && roomChanged && roomToken !== activeRoom)) {
      startCall(roomToken)
    } else if (!inCall && prevCall) {
      // Call ended: stop polling but keep the transcript on screen.
      endPolling()
    } else if (roomChanged && !inCall) {
      // Navigated to another conversation with no active call: drop the stale one.
      if (store.items.length) store.reset()
      endPolling()
    }
    prevRoom = roomToken
    prevCall = callToken
  }

  watchTimer = setInterval(watch, WATCH_MS)
  watch()

  return {
    get inCall() {
      return Boolean(tap.callToken())
    },
    roomToken: () => tap.roomToken(),
    destroy() {
      stopped = true
      if (watchTimer) clearInterval(watchTimer)
      stopPolling()
    },
  }
}
