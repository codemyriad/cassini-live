from dataclasses import replace

import pytest

from cassini_live.load import THRESHOLDS, LagMeter, LoadGovernor, Thresholds, per_speaker_mib
from cassini_live.resources import MIB

CALM = {
    "memoryLimitBytes": 3072 * MIB,
    "memoryCurrentBytes": 1500 * MIB,
    "memoryFileBytes": 400 * MIB,
    "memoryShmemBytes": 0,
    "memoryStatAvailable": True,
    "cpuStallUsec": 0,
    "cpuPressureRecent": 1.0,
    "pressureAvailable": True,
}


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Speaker:
    """Duck-typed transcriber: the governor reads only these fields."""

    def __init__(self):
        self.pending_since = self.lease_at = self.paused_until = self.shed_reason = None
        self.leased = False

    def has_lease(self):
        return self.leased

    def speak(self, now, lag=0.0):
        # Leased with the oldest undecoded chunk `lag` seconds old.
        self.leased = True
        self.pending_since = self.lease_at = now - lag


def governor(**changes):
    clock = Clock()
    thresholds = replace(THRESHOLDS, **changes.pop("thresholds", {}))
    settings = dict(ceiling=3, per_speaker_mib=61, thresholds=thresholds, observe_only=False)
    settings.update(changes)
    gov = LoadGovernor(clock=clock, wall_clock=lambda: 5000.0, **settings)
    gov.tick(CALM)
    return gov, clock


def seated(gov, clock, count):
    """Admit `count` leased speakers one second apart, keeping up, so "newest" is well
    defined and the earlier ones never raise the control lag. The monitor ticks every
    second, so each admission second gets its calm sample."""
    speakers = []
    for _ in range(count):
        clock.now += 1
        for speaker in speakers:
            speaker.speak(clock.now)
        speaker = Speaker()
        assert gov.admit(speaker).admitted
        speaker.speak(clock.now)
        speakers.append(speaker)
        gov.tick(CALM)
    return speakers


def sustain(gov, clock, speakers, lag, seconds):
    for _ in range(seconds):
        clock.now += 1
        for speaker in speakers:
            speaker.speak(clock.now, lag)
        gov.tick(CALM)


def test_first_speaker_is_always_admitted_whatever_the_inputs():
    gov, clock = governor()
    gov.tick({**CALM, "memoryCurrentBytes": 3060 * MIB, "memoryFileBytes": 0})
    first = Speaker()
    assert gov.admit(first).admitted
    assert first in gov.seats and gov.last_refusal is None


def test_ceiling_refuses_once_every_seat_is_taken():
    gov, clock = governor(ceiling=2)
    holders = seated(gov, clock, 2)  # seats are weak: the holders must stay referenced
    decision = gov.admit(Speaker())
    assert len(holders) == len(gov.seats) == 2
    assert (decision.admitted, decision.reason) == (False, "ceiling")
    assert gov.last_refusal == {"at": 5000.0, "reason": "ceiling"}
    assert not gov.closed  # a ceiling refusal is structural, not load


def test_memory_refuses_below_two_per_speaker_allowances():
    gov, clock = governor()
    holder = seated(gov, clock, 1)
    # 2 x 61 MiB = 122 MiB; headroom is memory.max minus the non-file charge.
    short = 3072 * MIB - 121 * MIB + 400 * MIB
    gov.tick({**CALM, "memoryCurrentBytes": short})
    assert gov.admit(Speaker()).reason == "memory"
    gov.tick({**CALM, "memoryCurrentBytes": short - 2 * MIB})
    assert gov.admit(Speaker()).admitted and holder
    # Shared memory sits in the file figure but cannot be reclaimed: it counts as used.
    gov.tick({**CALM, "memoryCurrentBytes": short - 2 * MIB, "memoryShmemBytes": 2 * MIB})
    assert gov.admit(Speaker()).reason == "memory"


def test_memory_rule_never_binds_for_whistle_or_without_a_limit():
    assert per_speaker_mib("whistle") == 0
    assert per_speaker_mib("nemotron-multilingual") == 16 + 45
    gov, clock = governor(per_speaker_mib=0)
    holder = seated(gov, clock, 1)
    gov.tick({**CALM, "memoryCurrentBytes": 3072 * MIB, "memoryFileBytes": 0})
    assert gov.admit(Speaker()).admitted and holder
    gov, clock = governor()
    holder = seated(gov, clock, 1)
    gov.tick({"cpuStallUsec": 0})
    assert gov.admit(Speaker()).admitted and holder


def test_cpu_stall_refuses_and_closes_admission():
    gov, clock = governor()
    holder = seated(gov, clock, 1)
    gov.tick(CALM)
    clock.now += 1
    gov.tick({**CALM, "cpuStallUsec": 300_000})  # 0.3 s stalled in the last second
    assert holder
    assert gov.cpu_stall == pytest.approx(0.3)
    assert gov.admit(Speaker()).reason == "cpu"
    assert gov.closed and gov.status()["reason"] == "cpu"


def test_cpu_rule_is_skipped_without_pressure_counters():
    # A zero threshold refuses on any reading, so an admission proves the rule was skipped.
    gov, clock = governor(thresholds={"cpu_stall_admit": 0.0})
    (holder,) = seated(gov, clock, 1)  # kept referenced: with no seats no rule is reached
    clock.now += 1
    holder.speak(clock.now)  # keeping up, so only the cpu rule could refuse
    gov.tick({**CALM, "cpuStallUsec": None})
    assert gov.cpu_stall is None and len(gov.seats) == 1
    decision = gov.admit(Speaker())
    assert (decision.admitted, decision.reason) == (True, None) and not gov.closed
    # The first counter after a gap only re-anchors; the second yields a reading that fails.
    for _ in range(2):
        clock.now += 1
        holder.speak(clock.now)
        gov.tick({**CALM, "cpuStallUsec": 0})
    assert gov.cpu_stall == 0.0
    assert gov.admit(Speaker()).reason == "cpu"


def test_lag_refuses_from_the_control_lag_of_leased_speakers():
    gov, clock = governor()
    (speaker,) = seated(gov, clock, 1)
    speaker.speak(clock.now, lag=1.0)
    assert gov.admit(Speaker()).reason == "lag"
    assert gov.closed
    # Waiting on a slot is not engine lag: without a lease the same stamps count for nothing.
    gov, clock = governor()
    (speaker,) = seated(gov, clock, 1)
    speaker.speak(clock.now, lag=3.0)
    speaker.leased = False
    assert gov.admit(Speaker()).admitted
    # Nor is the wait a holder just served: only the backlog since its lease counts.
    speaker.leased = True
    speaker.lease_at = clock.now - 0.2  # pending_since is still 3 s back
    assert gov.admit(Speaker()).admitted and gov.lag_now == 0.0
    gov.tick(CALM)
    assert gov.lag_now == pytest.approx(0.2)


def test_seat_holders_are_never_refused():
    gov, clock = governor(ceiling=1)
    (holder,) = seated(gov, clock, 1)
    holder.speak(clock.now, lag=5.0)
    gov.tick({**CALM, "memoryCurrentBytes": 3072 * MIB, "memoryFileBytes": 0})
    assert gov.admit(Speaker()).reason == "ceiling"
    assert gov.admit(holder).admitted
    assert gov.seats[holder] == 101.0  # the original seat, not a new one


def test_seat_expires_after_idle_without_a_lease_and_release_frees_it():
    gov, clock = governor()
    first, second = seated(gov, clock, 2)
    first.leased = second.leased = False
    sustain(gov, clock, [], 0, 14)  # first last leased at the 102 tick: idle 14 s at the last
    assert len(gov.seats) == 2
    second.leased = True
    sustain(gov, clock, [], 0, 1)
    assert first not in gov.seats and second in gov.seats
    gov.release(second)
    assert not gov.seats


def test_admit_refreshes_the_seat_so_a_wait_after_a_gap_does_not_expire_it():
    gov, clock = governor()
    (holder,) = seated(gov, clock, 1)
    holder.leased = False
    sustain(gov, clock, [], 0, 14)  # idle 14 s since the 101 tick
    assert holder in gov.seats
    clock.now += 1  # a new segment at idle 15: admitted, then parked in the slot FIFO
    assert gov.admit(holder).admitted and gov.seats[holder] == 101.0
    sustain(gov, clock, [], 0, 1)  # the tick at idle 16 must not take the seat mid-wait
    assert holder in gov.seats


def test_shedding_pauses_the_newest_seat_holder_with_a_lease():
    gov, clock = governor()
    older, middle, newest = seated(gov, clock, 3)
    newest.leased = False  # a parked waiter cannot drain anything
    sustain(gov, clock, [older, middle], lag=2.5, seconds=5)
    assert middle.shed_reason == "lag" and older.shed_reason is None
    assert middle not in gov.seats and newest in gov.seats
    assert gov.shed_events == 1 and gov.last_shed == {"at": 5000.0, "lag": 2.5}


def test_shedding_needs_a_full_window_above_lag_shed():
    gov, clock = governor()
    speakers = seated(gov, clock, 2)
    sustain(gov, clock, speakers, lag=2.5, seconds=4)
    assert gov.shed_events == 0
    sustain(gov, clock, speakers, lag=1.0, seconds=1)
    assert gov.shed_events == 0 and gov.lag_sustained == 1.0


def test_a_single_seat_holder_is_never_shed():
    gov, clock = governor()
    alone, parked = seated(gov, clock, 2)
    parked.leased = False
    sustain(gov, clock, [alone], lag=4.0, seconds=6)
    assert gov.shed_events == 0 and alone.shed_reason is None


def test_shed_interval_spaces_consecutive_sheds():
    gov, clock = governor()
    speakers = seated(gov, clock, 3)
    sustain(gov, clock, speakers, lag=3.0, seconds=5)
    assert gov.shed_events == 1
    sustain(gov, clock, speakers, lag=3.0, seconds=4)
    assert gov.shed_events == 1
    sustain(gov, clock, speakers, lag=3.0, seconds=1)
    assert gov.shed_events == 2 and len(gov.seats) == 1


def test_measuring_suspends_shedding():
    gov, clock = governor()
    speakers = seated(gov, clock, 2)
    gov.measuring = True
    sustain(gov, clock, speakers, lag=3.0, seconds=6)
    assert gov.shed_events == 0 and gov.lag_sustained == 3.0


def test_hold_off_refuses_a_paused_speaker_until_it_elapses():
    gov, clock = governor()
    speakers = seated(gov, clock, 2)
    gov.pause(speakers[1])
    speakers[1].leased = False  # _finish("shed") ends the lease before the hold-off
    assert speakers[1] not in gov.seats
    assert speakers[1].paused_until == clock.now + 10
    clock.now += 9.9
    speakers[0].speak(clock.now)  # the remaining speaker keeps up, so only the hold-off binds
    assert gov.admit(speakers[1]).reason == "shed"
    clock.now += 0.1
    speakers[0].speak(clock.now)
    decision = gov.admit(speakers[1])
    assert decision.admitted and gov.seats[speakers[1]] == clock.now  # a new seat, newest


def test_reopen_needs_half_the_threshold_for_one_full_window():
    gov, clock = governor()
    (speaker,) = seated(gov, clock, 1)
    speaker.speak(clock.now, lag=1.5)
    assert gov.admit(Speaker()).reason == "lag"
    # The momentary lag is gone, but the governor stays closed on hysteresis.
    speaker.speak(clock.now)
    assert gov.admit(Speaker()).reason == "lag"
    sustain(gov, clock, [speaker], lag=0.6, seconds=6)
    assert gov.closed  # above half the threshold for a whole window: no calm yet
    # The rolling minimum turns calm at the first calm second; a full window must follow.
    sustain(gov, clock, [speaker], lag=0.1, seconds=5)
    assert gov.closed
    sustain(gov, clock, [speaker], lag=0.1, seconds=1)
    assert not gov.closed
    assert gov.admit(Speaker()).admitted


def test_reopen_kicks_parked_slot_waiters_once():
    gov, clock = governor()
    kicks = []
    gov.on_reopen = lambda: kicks.append(clock.now)
    (speaker,) = seated(gov, clock, 1)
    speaker.speak(clock.now, lag=1.5)
    gov.admit(Speaker())
    sustain(gov, clock, [speaker], lag=0.1, seconds=6)
    assert not gov.closed and len(kicks) == 1
    sustain(gov, clock, [speaker], lag=0.1, seconds=3)
    assert len(kicks) == 1


def test_calm_window_restarts_when_lag_returns():
    gov, clock = governor()
    (speaker,) = seated(gov, clock, 1)
    speaker.speak(clock.now, lag=1.5)
    gov.admit(Speaker())
    sustain(gov, clock, [speaker], lag=0.1, seconds=1)
    sustain(gov, clock, [speaker], lag=0.6, seconds=5)  # a window above half: calm resets
    assert gov.closed
    sustain(gov, clock, [speaker], lag=0.1, seconds=5)
    assert gov.closed
    sustain(gov, clock, [speaker], lag=0.1, seconds=1)
    assert not gov.closed


def test_observe_mode_records_refusals_but_admits_everyone():
    gov, clock = governor(ceiling=1, observe_only=True)
    holder = seated(gov, clock, 1)
    extra = Speaker()
    assert holder
    decision = gov.admit(extra)
    assert (decision.admitted, decision.reason) == (True, "ceiling")
    assert gov.last_refusal == {"at": 5000.0, "reason": "ceiling"}
    assert extra in gov.seats  # the seat count keeps matching who is live


def test_observe_mode_computes_sheds_but_marks_nothing():
    gov, clock = governor(observe_only=True)
    speakers = seated(gov, clock, 2)
    sustain(gov, clock, speakers, lag=3.0, seconds=5)
    assert gov.shed_events == 1 and gov.last_shed["lag"] == 3.0
    assert all(s.shed_reason is None and s in gov.seats for s in speakers)
    assert gov.status()["observeOnly"] is True


def test_observe_mode_follows_provisional_thresholds_by_default():
    assert LoadGovernor(ceiling=2).observe_only is THRESHOLDS.provisional is True
    measured = Thresholds(provisional=False, measured_on="2026-10-20")
    assert LoadGovernor(ceiling=2, thresholds=measured).observe_only is False


def test_thresholds_cannot_leave_provisional_without_the_measurement_date():
    with pytest.raises(ValueError, match="measured_on"):
        Thresholds(provisional=False)
    with pytest.raises(ValueError, match="measured_on"):
        replace(THRESHOLDS, provisional=False, measured_on="")


def test_observe_mode_seats_live_speakers_from_the_tick_and_marks_nothing():
    # A speaker that never asked admit() is seated by the observing tick, with its own
    # backlog left out of the lag rule, as it is at the real decision point (no lease yet).
    gov, clock = governor(ceiling=1, observe_only=True)
    first, second = Speaker(), Speaker()
    first.speak(clock.now, lag=1.5)
    second.speak(clock.now, lag=1.5)
    gov.transcribers.add(first)
    gov.tick(CALM)
    assert set(gov.seats) == {first} and gov.last_refusal is None and gov.lag_now == 1.5
    gov.transcribers.add(second)
    gov.tick(CALM)
    assert set(gov.seats) == {first, second}  # observing: seated despite the refusal
    assert gov.last_refusal == {"at": 5000.0, "reason": "ceiling"}
    assert gov.status()["speaking"] == 2 and gov.closed is False
    assert all(s.shed_reason is None and s.paused_until is None for s in (first, second))
    # A seat is never re-evaluated, and a parked waiter is not seated until it is served.
    waiter = Speaker()
    waiter.pending_since = clock.now - 3.0
    gov.transcribers.add(waiter)
    gov.tick(CALM)
    assert waiter not in gov.seats and gov.last_refusal["reason"] == "ceiling"
    gov.measuring = True  # synthetic tracks of a capacity run are never seated
    waiter.speak(clock.now)
    gov.tick(CALM)
    assert waiter not in gov.seats
    gov.measuring = False
    gov.tick(CALM)
    assert waiter in gov.seats
    gov, clock = governor(ceiling=1)  # acting mode: seats come from admit() only
    gov.transcribers.add(first)
    gov.tick(CALM)
    assert not gov.seats


def test_status_shape_matches_the_design():
    gov, clock = governor()
    leased, parked = seated(gov, clock, 2)
    parked.leased = False
    gov.pause(parked)
    gov.tick(CALM)
    status = gov.status()
    assert set(status) == {
        "open",
        "reason",
        "seats",
        "ceiling",
        "speaking",
        "paused",
        "lagSeconds",
        "lagSustainedSeconds",
        "captionAgeSeconds",
        "cpuStall",
        "cpuPressureAvg10",
        "memoryHeadroomMiB",
        "memoryUsedMiB",
        "memoryLimitMiB",
        "memoryStatAvailable",
        "pressureAvailable",
        "engineDuty",
        "lastRefusal",
        "lastShed",
        "shedEvents",
        "measuring",
        "tickMs",
        "observeOnly",
    }
    assert status["open"] and status["reason"] is None
    assert (status["seats"], status["ceiling"], status["speaking"], status["paused"]) == (
        1,
        3,
        1,
        1,
    )
    assert (status["memoryHeadroomMiB"], status["memoryUsedMiB"], status["memoryLimitMiB"]) == (
        3072 - 1100,
        1100,
        3072,
    )
    assert status["memoryStatAvailable"] and status["pressureAvailable"]
    assert status["cpuStall"] == 0 and status["cpuPressureAvg10"] == 1.0
    assert status["engineDuty"] is None and status["lastRefusal"] is None
    assert status["lastShed"] is None and status["shedEvents"] == 0
    assert not status["measuring"] and not status["observeOnly"]
    assert isinstance(status["tickMs"], float)


def test_caption_age_includes_waiters_and_lag_excludes_them():
    gov, clock = governor()
    (holder,) = seated(gov, clock, 1)
    holder.speak(clock.now, lag=0.5)
    waiter = Speaker()
    waiter.pending_since = clock.now - 3.0
    gov.admit(waiter)
    gov.tick(CALM)
    assert gov.status()["captionAgeSeconds"] == 3.0
    assert gov.status()["lagSeconds"] == 0.5


def test_lag_meter_sustained_is_the_window_minimum_of_per_second_maxima():
    meter = LagMeter()
    for second, lag in enumerate([3.0, 3.0, 3.0, 3.0, 0.2, 3.0, 3.0, 3.0, 3.0]):
        meter.observe(lag, 100 + second)
        meter.observe(0.0, 100 + second + 0.5)  # a dip within the second does not count
    assert meter.sustained(108.9) == 0.2
    meter.observe(3.0, 109)
    assert meter.sustained(109) == 3.0
    # Seconds nobody sampled are no evidence of calm; history beyond the buckets is dropped
    # and a late sample stands in for the seconds it skipped.
    assert meter.sustained(111) == 3.0
    meter.observe(1.0, 120)
    assert meter.maxima == dict.fromkeys(range(111, 121), 1.0)


def test_lag_meter_fills_the_seconds_a_late_tick_skipped():
    meter = LagMeter()
    # A 1.0001 s tick period starting just under a boundary leaves second 101 unsampled.
    ticks = [round(100.9999 + i * 1.0001, 4) for i in range(6)]
    assert [int(t) for t in ticks] == [100, 102, 103, 104, 105, 106]
    for now in ticks:
        meter.observe(3.0, now)
    assert meter.maxima[101] == 3.0
    assert [meter.sustained(now) for now in ticks[4:]] == [3.0, 3.0]
    # A tick delayed 2.5 s fills the gap with the lag it then saw: nobody looked in between.
    meter.observe(4.0, ticks[-1] + 2.5)
    assert meter.maxima[107] == meter.maxima[108] == 4.0
    assert meter.sustained(108.5) == 3.0
    # Before the first sample nothing is sustained, and that sample is not diluted by it.
    fresh = LagMeter()
    assert fresh.sustained(50) == 0.0
    fresh.observe(2.5, 50)
    assert fresh.sustained(50) == 2.5


def test_lag_meter_p95_of_recent_requests():
    meter = LagMeter()
    assert meter.p95() == 0.0 and meter.last() == 0.0  # the backends' status convention
    for value in range(1, 101):
        meter.record(value / 100)
    assert meter.p95() == pytest.approx(0.95) and meter.last() == 1.0
    meter.record(0.4)
    meter.record(0.2)
    assert meter.last() == 0.2
    for value in range(300):
        meter.record(2.0)
    assert meter.p95() == 2.0 and len(meter.requests) == LagMeter.REQUESTS


def test_control_lag_and_caption_age_read_the_given_transcribers():
    from cassini_live.load import caption_age, control_lag

    holder, waiter, served = Speaker(), Speaker(), Speaker()
    holder.speak(100.0, lag=0.5)
    waiter.pending_since = 97.0  # parked on the slot FIFO: no lease
    assert control_lag([holder, waiter], 100.0) == 0.5
    assert caption_age([holder, waiter], 100.0) == 3.0
    served.speak(100.0)  # just handed the slot it waited 3 s for: lag counts from the lease
    served.pending_since, served.lease_at = 97.0, 99.8
    assert control_lag([holder, served], 100.0) == pytest.approx(0.5)
    assert control_lag([served], 100.0) == pytest.approx(0.2)
    assert control_lag([], 100.0) == caption_age([], 100.0) == 0.0


def test_governor_reads_a_backend_transcriber_set_when_given_one():
    import weakref

    live = weakref.WeakSet()
    gov = LoadGovernor(ceiling=2, transcribers=live, clock=Clock(), observe_only=True)
    speaker = Speaker()
    live.add(speaker)
    speaker.speak(Clock.now, lag=1.5)
    gov.tick(CALM)
    assert gov.lag_now == 1.5 and gov.transcribers is live
    assert gov.status()["lagSeconds"] == 1.5 and gov.status()["seats"] == 1


def test_a_shed_closes_admission_for_lag():
    gov, clock = governor()
    older, newest = seated(gov, clock, 2)
    sustain(gov, clock, [older, newest], lag=2.5, seconds=5)
    assert newest.shed_reason == "lag" and gov.closed and gov.closed_reason == "lag"
    assert not gov.admit(Speaker()).admitted  # the reopen hysteresis applies


def flap(seconds=180):
    """The review's flap simulation: two speakers who do not fit together. Alone, A's
    backlog drains at 1 s/s; together each grows 0.3 s/s. B acts on a shed mark at once
    and, once its hold-off ends, asks admission again."""
    clock = Clock()
    clock.now = 1000.0
    thresholds = Thresholds(provisional=False, measured_on="2026-10-16")
    gov = LoadGovernor(ceiling=3, thresholds=thresholds, clock=clock)
    a, b = Speaker(), Speaker()
    backlog = {a: 0.0, b: 0.0}
    for speaker in (a, b):
        clock.now += 1
        assert gov.admit(speaker).admitted
        speaker.leased, speaker.lease_at = True, clock.now
    log = []
    for step in range(seconds * 10):
        clock.now += 0.1
        now = clock.now
        leased = [s for s in (a, b) if s.leased]
        for s in leased:
            backlog[s] = max(0.0, backlog[s] + (0.03 if len(leased) == 2 else -0.1))
            s.pending_since = now - backlog[s]
        if b.shed_reason:
            b.shed_reason, b.leased, b.pending_since, backlog[b] = None, False, None, 0.0
            gov.pause(b, now)
            log.append(("shed", now, b.paused_until - now))
        if not b.leased and b.paused_until and now >= b.paused_until:
            if gov.admit(b, now).admitted:
                b.leased, b.lease_at = True, now
                log.append(("admitted", now, None))
            else:
                gov.pause(b, now)
        if step % 10 == 0:
            gov.tick({}, now)
    return log


def test_a_speaker_shed_again_soon_after_readmission_waits_longer():
    # Review 0.8.0: without this, B was paused for 10 s and re-admitted every ~21 s for
    # as long as the call lasted. Each shed within repeat_window of the re-admission
    # doubles the hold-off up to hold_off_max.
    sheds = [hold for kind, _, hold in flap() if kind == "shed"]
    assert [round(h) for h in sheds] == [10, 20, 40, 60]


def test_strikes_reset_once_a_speaker_keeps_up_for_the_repeat_window():
    gov, clock = governor()
    older, newest = seated(gov, clock, 2)
    sustain(gov, clock, [older, newest], lag=2.5, seconds=5)
    gov.pause(newest)
    assert newest.paused_until - clock.now == 10
    clock.now += 11
    older.speak(clock.now)  # caught up
    gov.closed_reason = None
    newest.shed_reason = None
    assert gov.admit(newest).admitted
    for _ in range(31):  # keeps its seat, keeping up, longer than repeat_window
        clock.now += 1
        older.speak(clock.now)
        newest.speak(clock.now)
        gov.tick(CALM)
    gov.pause(newest)
    assert newest.paused_until - clock.now == 10
