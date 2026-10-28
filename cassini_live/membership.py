"""Talk room membership of a Nextcloud user, asked of Talk as that user."""

import asyncio
import time


class RoomMembership:
    """Authorize by acting as the requesting user and asking Talk for the room's
    participants: Talk returns them only to members, so a 200 is proof of membership and a
    403/404 is a clean deny. Talk's own room ACL is the gate; we keep no separate membership
    state. Guests (empty username) cannot be acted-as and are refused.

    Membership is stable during a call, and pages poll, so a positive answer is cached per
    (user, room) for `ttl` seconds to spare Talk a round-trip per poll."""

    def __init__(self, nc_factory, ttl=30.0):
        self.nc_factory = nc_factory
        self.ttl = ttl
        self._until: dict[tuple[str, str], float] = {}

    async def check(self, username: str, room_token: str) -> bool:
        if not username or not room_token:
            return False
        now = asyncio.get_event_loop().time()
        key = (username, room_token)
        cached = self._until.get(key)
        if cached is not None and cached > now:
            return True
        try:
            nc = self.nc_factory()
            await nc.set_user(username)
            await nc.ocs("GET", f"/ocs/v2.php/apps/spreed/api/v4/room/{room_token}/participants")
        except Exception:
            self._until.pop(key, None)
            return False
        self._until[key] = now + self.ttl
        return True


class PolledMembership:
    """RoomMembership for a page that non-members poll too. A RoomMembership miss costs a
    fresh Nextcloud client and two requests (capabilities, then participants), so a refusal
    is kept `ttl` seconds per (user, room), and concurrent checks of one (user, room) share
    one answer. A user who joins the call is recognised `ttl` seconds later at most."""

    def __init__(self, membership, ttl=10.0):
        self.membership = membership
        self.ttl = ttl
        self._refused: dict[tuple[str, str], float] = {}
        self._asking: dict[tuple[str, str], asyncio.Future] = {}

    async def check(self, username: str, room_token: str) -> bool:
        key = (username, room_token)
        if self._refused.get(key, 0.0) > time.monotonic():
            return False
        asking = self._asking.get(key)
        if asking is None:
            asking = self._asking[key] = asyncio.ensure_future(self._ask(key))
            asking.add_done_callback(lambda _: self._asking.pop(key, None))
        # A poll that goes away leaves the answer to the others.
        return await asyncio.shield(asking)

    async def _ask(self, key):
        member = await self.membership.check(*key)
        if not member:
            now = time.monotonic()
            self._refused = {k: until for k, until in self._refused.items() if until > now}
            self._refused[key] = now + self.ttl
        return member
