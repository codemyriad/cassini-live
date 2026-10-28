"""One-second history columns derived from consecutive diagnostic samples. Standard library
only: the budgeted video child imports it without the service's web and Nextcloud stack,
and the caption service process can import it without Pillow or the video renderer."""

# Two minutes of history: one column per one-second sample.
COLUMNS = 120


def history_column(previous, data, now):
    """One one-second column from cumulative counters; previous is the last sample."""

    def delta(new, old):
        # Counters restart when a speaker's gate is rebuilt (language change).
        return new if new < old else new - old

    lanes = {}
    before = {lane["id"]: lane for lane in previous.get("lanes", [])}
    for lane in data.get("lanes", []):
        old = before.get(lane["id"], {})
        speech = delta(lane.get("speech", 0), old.get("speech", lane.get("speech", 0)))
        lost = delta(lane.get("lost", 0), old.get("lost", lane.get("lost", 0)))
        if lost:
            lanes[lane["id"]] = "lost"
        elif lane.get("paused"):
            lanes[lane["id"]] = "paused"
        elif lane.get("waiting"):
            lanes[lane["id"]] = "waiting"
        elif speech or lane.get("active"):
            lanes[lane["id"]] = "speech"
    finals = int(data.get("finals", 0))
    drops = int(data.get("drops", 0))
    admission = data.get("admission") or {}
    sheds = int(admission.get("shedEvents", 0) or 0)
    before_sheds = int((previous.get("admission") or {}).get("shedEvents", sheds) or 0)
    return {
        "t": now,
        "lanes": lanes,
        "finals": delta(finals, int(previous.get("finals", finals))),
        "drops": delta(drops, int(previous.get("drops", drops))),
        "lag": admission.get("lagSeconds"),
        "shed": delta(sheds, before_sheds) > 0,
    }
