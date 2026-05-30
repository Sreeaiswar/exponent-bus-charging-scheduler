"""Shared route geometry: the single source of truth for stop distances.

Converting a :class:`Route` + :class:`Direction` into "how far is each stop from
the origin" is needed in three places — the plan generator (to derive valid
plans), the simulator (to compute travel times), and the engine (to estimate
congestion). Keeping it here means those three can never disagree about
distances, and there is no private cross-module import.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from models.route import Direction, Route


def travel_offsets(
    route: Route, direction: Direction
) -> Tuple[List[str], Dict[str, float]]:
    """Return travel-order stop names and each stop's km-offset from the origin.

    Walks the route in travel order (reversed for ``BACKWARD``), accumulating
    segment distances so each stop carries its distance from the origin endpoint.

    Args:
        route: The route to read stops and segment distances from.
        direction: Travel direction along the route's stop order.

    Returns:
        ``(ordered_stops, offsets)`` where ``ordered_stops`` is every stop in
        travel order (origin first, destination last) and ``offsets`` maps each
        stop name to its distance (km) from the origin.
    """
    stops = list(route.stops)
    segment_distances = [segment.distance for segment in route.segments]

    if direction is Direction.BACKWARD:
        stops.reverse()
        segment_distances.reverse()

    offsets: Dict[str, float] = {stops[0]: 0.0}
    cumulative = 0.0
    for index, distance in enumerate(segment_distances):
        cumulative += distance
        offsets[stops[index + 1]] = cumulative

    return stops, offsets
