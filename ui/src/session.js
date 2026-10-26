/**
 * Conversation/call scoping and transcript polling (D-830).
 *
 * The server is the source of truth: it transcribes every speaker in the call
 * and records joins. This watches the room/call tokens (via the tap) and, while
 * in a call, polls `/history?since=<lastSeq>` and feeds the delta to the store.
 * Polling (not a one-shot fetch) is what closes the gap between joining a call
 * and enabling captions: everything said is on the server and arrives on the
 * next poll, whether or not the viewer ever subscribed to live captions.
 *
 * Scoping:
 *  - a new call (call token appears) or a switch to another conversation resets
 *    the store and restarts polling for that conversation — two conversations
 *    never mix, a new call in the same room starts fresh;
 *  - when a call ends the poll stops but the transcript stays on screen until
 *    the viewer navigates away (which resets via a room change).
 */

const POLL_MS = 1200

export function createSession({ tap, store, fetchHistory, onChange = () => {} }) {
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
    const { entries } = await fetchHistory(room, store.lastSeq)
    if (stopped || mine !== generation) return
    store.applyTimeline(entries)
    pollTimer = setTimeout(() => pollOnce(room, mine), POLL_MS)
  }

  function startCall(room) {
    generation++
    activeRoom = room
    stopPolling()
    store.reset()
    onChange({ roomToken: room, inCall: true })
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

  watchTimer = setInterval(watch, POLL_MS)
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
