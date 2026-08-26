"""Pure parts of the kube adapter."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from htrflow_reconciler.k8s import lease_is_free

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _spec(holder, renewed_ago: int, duration: int = 600):
    return SimpleNamespace(
        holder_identity=holder,
        acquire_time=NOW - timedelta(seconds=renewed_ago),
        renew_time=NOW - timedelta(seconds=renewed_ago),
        lease_duration_seconds=duration,
    )


def test_lease_free_when_unheld_or_ours():
    assert lease_is_free(None, "me", NOW)
    assert lease_is_free(_spec(None, 0), "me", NOW)
    assert lease_is_free(_spec("me", 0), "me", NOW)


def test_lease_held_by_a_live_tick_is_busy():
    assert not lease_is_free(_spec("other", 30), "me", NOW)


def test_lease_of_a_dead_tick_expires():
    """A tick killed by the deadline never releases; its Lease must not wedge
    every later tick."""
    assert lease_is_free(_spec("other", 601), "me", NOW)
