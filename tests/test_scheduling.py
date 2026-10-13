import asyncio

import pytest

from cassini_live.pool import CapacityError, WorkerPool
from tests.test_streaming import FakeWorker


async def test_fifo_handoff_cannot_be_stolen_by_previous_speaker():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    held = pool.acquire()
    first = asyncio.create_task(pool.acquire_wait())
    second = asyncio.create_task(pool.acquire_wait())
    await asyncio.sleep(0)
    await pool.release(held)
    with pytest.raises(CapacityError):
        pool.acquire()
    assert await first is held
    assert not second.done()
    await pool.release(held)
    assert await second is held
    await pool.release(held)
    assert pool.idle.qsize() == 1 and not pool.waiting
    await pool.close()


async def test_cancelled_waiter_returns_worker_even_during_handoff():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    held = pool.acquire()
    waiting = asyncio.create_task(pool.acquire_wait())
    await asyncio.sleep(0)
    await pool.release(held)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert pool.acquire() is held
    await pool.release(held)
    await pool.close()


async def test_timeout_and_shutdown_leave_no_orphan_waiters():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    held = pool.acquire()
    with pytest.raises(CapacityError):
        await pool.acquire_wait(timeout=0.001)
    assert not pool.waiting
    task = asyncio.create_task(pool.acquire_wait())
    await asyncio.sleep(0)
    await pool.close()
    with pytest.raises(CapacityError):
        await task
    await pool.release(held)
    assert not pool.waiting and pool.idle.empty()
