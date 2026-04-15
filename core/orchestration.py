"""
core/orchestration.py — Non-HTTP orchestration helpers extracted from route modules.

Routes should parse + validate the request, then call into here for state-mutating
work that spans multiple objects or requires careful locking. Keeping this out of
the HTTP layer makes lock-contention and concurrency bugs easier to reason about
(e.g. M2 in the v0.9.1 audit — a dict-mutation-during-iteration in the host
propagation path that lived inline in routes/devices.py).
"""


def propagate_device_host(dev, new_host: str) -> int:
    """Set the device host and cascade to every sensor that hasn't been
    manually overridden. Snapshots the sensors dict before iterating so a
    concurrent delete-sensor request can't raise RuntimeError.

    Returns the number of sensors whose host was updated.
    """
    dev.host = new_host
    updated = 0
    for _s in list(dev.sensors.values()):
        if not _s.host_override:
            _s.host = new_host
            updated += 1
    return updated
