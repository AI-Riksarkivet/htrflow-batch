"""Bounded, campaign-fair submission planning (spec §4.7)."""

from __future__ import annotations

from itertools import zip_longest

from .models import Volume


def plan_submissions(
    pending: dict[str, list[Volume]],
    in_flight: int,
    window: int,
) -> list[tuple[str, Volume]]:
    """Interleave submittable volumes round-robin across campaigns.

    ``pending`` maps campaign name to its submittable volumes in file order;
    dict insertion order is the campaign fairness order. The result is capped
    at ``max(0, window - in_flight)`` so no tick exceeds the in-flight window.
    """
    budget = max(0, window - in_flight)
    if budget == 0:
        return []
    lanes = [[(name, v) for v in vols] for name, vols in pending.items() if vols]
    interleaved = [
        item for round_ in zip_longest(*lanes) for item in round_ if item is not None
    ]
    return interleaved[:budget]
