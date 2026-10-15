from dataclasses import replace

import pytest

from cassini_live.load import THRESHOLDS, LagMeter, LoadGovernor, Thresholds, per_speaker_mib
from cassini_live.resources import MIB

CALM = {
    "memoryLimitBytes": 3072 * MIB,
    "memoryCurrentBytes": 1500 * MIB,
    "memoryFileBytes": 400 * MIB,
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
    defined and the earlier ones never raise the control lag."""
    speakers = []
    for _ in range(count):
        clock.now += 1
        for speaker in speakers:
            speaker.speak(clock.now)
        speaker = Speaker()
        assert gov.admit(speaker).admitted
        speaker.speak(clock.now)
        speakers.append(speaker)
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
    gov, clock = governor()
    seated(gov, clock, 1)
    clock.now += 1
    gov.tick({**CALM, "cpuStallUsec": None})
    assert gov.cpu_stall is None
    assert gov.admit(Speaker()).admitted


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
    sustain(gov, clock, [], 0, 13)  # first admitted at 101: idle 14 s at the last tick
    assert len(gov.seats) == 2
    second.leased = True
    sustain(gov, clock, [], 0, 1)
    assert first not in gov.seats and second in gov.seats
    gov.release(second)
    assert not gov.seats


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
    # A second without a tick counts as no lag; history beyond the buckets is dropped.
    assert meter.sustained(111) == 0.0
    meter.observe(1.0, 120)
    assert set(meter.maxima) == {120}


def test_lag_meter_p95_of_recent_requests():
    meter = LagMeter()
    assert meter.p95() is None
    for value in range(1, 101):
        meter.record(value / 100)
    assert meter.p95() == pytest.approx(0.95)
    for value in range(300):
        meter.record(2.0)
    assert meter.p95() == 2.0 and len(meter.requests) == LagMeter.REQUESTS
