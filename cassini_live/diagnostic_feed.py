"""The diagnostics page's feed: one-second samples and history while an administrator watches.

The feed's own DiagnosticSampler reads the same live state with the same code as the
diagnostic video's, so their numbers agree; each keeps its own continuity. Nothing runs while
nobody polls: the ticker stops PRESENCE_S after the last poll and drops its history and its
hold on participant names.
"""

import asyncio
import time
from collections import deque

from .diagnostic_history import COLUMNS, history_column
from .diagnostics import SAMPLE_S, DiagnosticSampler
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
        self.task = None
        # Monotonic times of the last poll, and of the last by a member of room `_member_of`:
        # Talk checked that room only, so its presence holds no other room's names.
        self._viewer = self._member = self._member_of = None
        # The client whose names this feed holds.
        self._holding = None
        self.columns = deque(maxlen=COLUMNS)
        self.latest = None
        # The room whose names `latest` may carry.
        self.latest_owner_token = None
        self.error = None

    def touch(self, member_of, now=None):
        """A poll keeps the feed sampling for PRESENCE_S. A poll by a member of room
        `member_of`, as Talk answered, also keeps that room's participant names read for as
        long while it is the owner room."""
        now = time.monotonic() if now is None else now
        self._viewer = now
        if member_of:
            self._member, self._member_of = now, member_of
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    def _select(self):
        clients = self.service.clients
        video = self.service.diagnostics
        if video.task is not None and video.owner in clients:
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
            while _recent(self._viewer, time.monotonic()):
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
            self._idle()

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

    def read(self, since=0.0):
        """The latest sample and the columns newer than `since`; times are this process's
        monotonic seconds."""
        return {
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
        # Speech while nobody watches would otherwise show as current on the next start.
        self.sampler.forget_voices()
        self.columns.clear()
        self.latest = self.latest_owner_token = self.error = None
        self._member = self._member_of = None

    async def close(self):
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._idle()
