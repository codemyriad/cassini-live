"""One-second diagnostics and a two-minute memory buffer while captions run.

Viewer presence controls participant-name lookup only. When the last caption room
closes, keep the final buffer until captions restart or the service shuts down.
"""

import asyncio
import secrets
import time
from collections import deque

from .diagnostic_history import COLUMNS, history_column
from .diagnostics import OTHER_CALL_SPEAKER, SAMPLE_S, SOME_SPEAKER, UNNAMED, DiagnosticSampler
from .resources import snapshot

# A viewer counts as watching this long after its last poll; the page polls once a second.
PRESENCE_S = 30.0


def _recent(seen, now):
    return seen is not None and now - seen < PRESENCE_S


class DiagnosticFeed:
    """The latest sample and up to COLUMNS history columns, sampled against one owner room:
    the diagnostic video's while it runs, else the oldest live caption room, else none."""

    def __init__(self, service, names):
        self.service = service
        self.names = names
        self.sampler = DiagnosticSampler(service)
        # Lane numbers count from 1 in each sampler, but the host's monotonic clock goes on
        # across a container restart (measured), so `now` alone cannot tell the page that
        # its columns are another process's. A random token: it says nothing else.
        self.instance = secrets.token_hex(8)
        self.task = None
        # Monotonic time of the last poll by a member of room `_member_of`:
        # Talk checked that room only, so its presence holds no other room's names.
        self._member = self._member_of = None
        # The client whose names this feed holds.
        self._holding = None
        self.columns = deque(maxlen=COLUMNS)
        self.latest = None
        # The room whose names `latest` may carry.
        self.latest_owner_token = None
        self.error = None
        self.sampled_at = None

    def start(self):
        """Start once when captions begin; a new run gets a fresh history."""
        if self.task is None or self.task.done():
            if self.latest is not None:
                self.instance = secrets.token_hex(8)
            self._idle()
            self.task = asyncio.create_task(self._run())

    def touch(self, member_of, now=None):
        """Keep an authorized viewer's name lookup alive, without starting collection."""
        now = time.monotonic() if now is None else now
        if member_of:
            self._member, self._member_of = now, member_of

    def _select(self):
        clients = self.service.clients
        video = self.service.diagnostics
        # A video that stopped itself (paused, failed) keeps its task and owner until that
        # room closes; it no longer runs, so it no longer picks the room.
        if video.task is not None and not video.task.done() and video.owner in clients:
            return video.owner, clients[video.owner]
        for token, client in list(clients.items()):
            defunct = getattr(client, "defunct", None)
            if defunct is None or not defunct.is_set():
                return token, client
        return None, None

    def owner_token(self):
        return self._select()[0]

    def names_token(self):
        return self.latest_owner_token

    async def _run(self):
        primed = False
        try:
            while self.owner_token() is not None:
                now = time.monotonic()
                try:
                    # The first tick only primes: CPU figures need a second to measure.
                    if primed:
                        self._step(now)
                    else:
                        self.sampler.prime(now)
                        primed = True
                    self.error = None
                except Exception as error:
                    # The type only: a message could carry a name or a path. The last good
                    # sample and columns stay.
                    self.error = type(error).__name__
                await asyncio.sleep(SAMPLE_S)
        finally:
            self._release()
            self._member = self._member_of = None
            # Retain anonymous history, not names after their lookup lifetime ends.
            if self.latest is not None:
                for lane in self.latest.get("lanes", []):
                    if "name" in lane:
                        lane["name"] = UNNAMED
                decision = self.latest.get("decision")
                if decision and decision.get("who") != OTHER_CALL_SPEAKER:
                    decision["who"] = SOME_SPEAKER

    def _step(self, now):
        started = time.thread_time()
        token, owner = self._select()
        if owner is not None and token == self._member_of and _recent(self._member, now):
            if self._holding is not owner:
                self._release()
                self.names.hold(owner, self)
                self._holding = owner
        else:
            self._release()
        sample = self.sampler.sample(owner, snapshot(), now, started=started)
        if self.latest is not None:
            # Rounded as the page sends it back in `since`.
            self.columns.append(history_column(self.latest, sample, round(now, 3)))
        self.latest, self.latest_owner_token = sample, token
        self.sampled_at = round(now, 3)

    def read(self, since=0.0):
        """The latest sample and the columns newer than `since`; times are this process's
        monotonic seconds, lane numbers this feed's (`instance`)."""
        return {
            "instance": self.instance,
            "collecting": self.task is not None and not self.task.done(),
            "sampledAt": self.sampled_at,
            "head": self.columns[-1]["t"] if self.columns else 0,
            "now": round(time.monotonic(), 3),
            "sample": self.latest,
            "columns": [column for column in self.columns if column["t"] > since],
            "error": self.error,
        }

    def _release(self):
        if self._holding is not None:
            self.names.release(self._holding, self)
            self._holding = None

    def _idle(self):
        self._release()
        # A later caption session must establish fresh counter and voice baselines.
        self.sampler.forget_voices()
        self.columns.clear()
        self.latest = self.latest_owner_token = self.error = self.sampled_at = None
        self._member = self._member_of = None

    async def close(self):
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._idle()
