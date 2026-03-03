from __future__ import annotations

from collections import defaultdict

from .models import Event


def select_top_events(
    events: list[Event],
    top_k: int,
    per_entity_limit: int,
) -> list[Event]:
    ordered = sorted(events, key=lambda e: e.score, reverse=True)
    chosen: list[Event] = []
    entity_count = defaultdict(int)
    deferred: list[Event] = []

    for event in ordered:
        if entity_count[event.entity] < per_entity_limit:
            chosen.append(event)
            entity_count[event.entity] += 1
        else:
            deferred.append(event)

        if len(chosen) >= top_k:
            return chosen[:top_k]

    for event in deferred:
        if len(chosen) >= top_k:
            break
        chosen.append(event)

    return chosen[:top_k]
