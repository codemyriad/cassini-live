"""Reading Talk's call end-to-end encryption flag from Nextcloud's capabilities."""

import asyncio

import pytest

from cassini_live.e2ee import CallEncryption


class FakeNc:
    """Answers /ocs/v2.php/cloud/capabilities with one canned document."""

    def __init__(self, data, calls):
        self.data, self.calls = data, calls

    async def ocs(self, method, path):
        self.calls.append((method, path))
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def capabilities(flag):
    return {"capabilities": {"spreed": {"config": {"call": {"end-to-end-encryption": flag}}}}}


def encryption(data, ttl=30.0, clock=None):
    calls = []
    kwargs = {"clock": clock} if clock else {}
    return CallEncryption(lambda: FakeNc(data, calls), ttl=ttl, **kwargs), calls


async def test_reads_the_flag():
    on, _ = encryption(capabilities(True))
    assert await on.active() is True
    off, _ = encryption(capabilities(False))
    assert await off.active() is False


@pytest.mark.parametrize(
    "data",
    [
        {},  # no capabilities at all
        {"capabilities": {"spreed": {"config": {}}}},  # Talk too old for the call config
        {"capabilities": {"spreed": {"config": {"call": {}}}}},  # flag absent
        {"capabilities": {"spreed": {"config": {"call": {"end-to-end-encryption": 1}}}}},
        RuntimeError("Nextcloud is unreachable"),
    ],
)
async def test_anything_but_the_boolean_true_means_not_encrypted(data):
    """Captions must not stop working over a capabilities hiccup or an odd shape; an
    encrypted call that slips through fails no worse than before the check existed."""
    checker, _ = encryption(data)
    assert await checker.active() is False


async def test_caches_the_answer_until_the_ttl_passes():
    now = [0.0]
    checker, calls = encryption(capabilities(True), ttl=30.0, clock=lambda: now[0])
    assert await checker.active() and await checker.active()
    assert len(calls) == 1
    now[0] = 31.0
    assert await checker.active()
    assert len(calls) == 2


async def test_concurrent_starts_share_one_request():
    calls = []
    asked = asyncio.Event()
    release = asyncio.Event()

    class SlowNc:
        async def ocs(self, method, path):
            calls.append((method, path))
            asked.set()
            await release.wait()
            return capabilities(True)

    checker = CallEncryption(lambda: SlowNc())
    first = asyncio.ensure_future(checker.active())
    await asked.wait()
    second = asyncio.ensure_future(checker.active())
    await asyncio.sleep(0)
    release.set()
    assert await first is True and await second is True
    assert len(calls) == 1


async def test_a_cancelled_start_leaves_the_answer_to_the_others():
    asked = asyncio.Event()
    release = asyncio.Event()

    class SlowNc:
        async def ocs(self, method, path):
            asked.set()
            await release.wait()
            return capabilities(True)

    checker = CallEncryption(lambda: SlowNc())
    first = asyncio.ensure_future(checker.active())
    await asked.wait()
    second = asyncio.ensure_future(checker.active())
    await asyncio.sleep(0)
    first.cancel()
    release.set()
    assert await second is True
