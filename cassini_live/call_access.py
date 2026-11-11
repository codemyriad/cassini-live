"""Whether a Nextcloud user may turn captions on or off for a Talk session.

Under the app id live_transcription, Talk authorizes its caption button and calls
/api/v1/call/transcribe server to server. Under any other app id (the App Store's
cassini_live) Talk's button never reaches this app, so the transcript panel calls the USER
proxy route /captions/transcribe with the same body instead. AppAPI only tells us who the
caller is; these are the checks Talk would otherwise have made, asked of Talk as that user:

- the caller is a participant of the room and past its lobby: Talk returns a room's
  participants only then;
- the session is the caller's own: otherwise any member could stop someone else's
  captions or push captions to their session;
- to turn captions on, the caller is in the call.

Answers are not cached: a toggle is rare, and whether a session is in the call changes.
"""

import re
import time
from collections.abc import Callable

# Talk room tokens are short alphanumerics; anything else never reaches the OCS path.
ROOM_TOKEN = re.compile(r"[A-Za-z0-9]{1,64}")


class CallSessions:
    def __init__(self, nc_factory: Callable):
        self.nc_factory = nc_factory

    async def may_toggle(self, username: str, room_token: str, session_id: str, enable: bool):
        """True if `session_id` is `username`'s own session in `room_token` (and, to
        enable, in its call). Any failure to ask Talk is a refusal."""
        if not username or not session_id or not ROOM_TOKEN.fullmatch(room_token or ""):
            return False
        try:
            nc = self.nc_factory()
            await nc.set_user(username)
            participants = await nc.ocs(
                "GET", f"/ocs/v2.php/apps/spreed/api/v4/room/{room_token}/participants"
            )
        except Exception:
            return False
        for participant in participants if isinstance(participants, list) else []:
            if not isinstance(participant, dict):
                continue
            if participant.get("actorType") != "users" or participant.get("actorId") != username:
                continue
            if session_id not in (participant.get("sessionIds") or []):
                continue
            # Talk reports inCall per attendee (the union of its sessions' call flags).
            return not enable or bool(participant.get("inCall"))
        return False


class RateLimit:
    """At most `limit` requests per `window` seconds per key, as a sliding window. Starting
    captions opens a signalling connection and takes admission capacity."""

    def __init__(self, limit=10, window=60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, list[float]] = {}

    def retry_after(self, key: str) -> float:
        """0 and the request counted if allowed; otherwise seconds until it would be."""
        now = self.clock()
        horizon = now - self.window
        self._hits = {
            k: kept for k, hits in self._hits.items() if (kept := [t for t in hits if t > horizon])
        }
        hits = self._hits.setdefault(key, [])
        if len(hits) >= self.limit:
            return hits[0] - horizon
        hits.append(now)
        return 0.0
