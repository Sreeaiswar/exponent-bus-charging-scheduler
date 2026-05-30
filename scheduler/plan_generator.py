"""Charging-plan generator: enumerate every valid set of charging stops.

Given a route, a battery range, and a travel direction, this module derives all
charging plans a bus *could* legally follow. A plan is just the ordered list of
intermediate stations where the bus charges to full. Endpoints are never charge
stops (buses leave their origin full and the trip ends at the destination).

Nothing here is hardcoded to the assignment's route: stations and distances come
entirely from the :class:`Route`, so a longer route, different distances, or a
new direction all work without changes. This module only *generates* plans — it
knows nothing about chargers, queues, waits, or who-goes-first.

Validity of a plan:
    * The bus never travels more than ``battery_range`` between consecutive full
      charges (and between origin->first charge and last charge->destination).
    * Stations are visited in route order, no backtracking.

Example:
    Route:  Bengaluru -> A -> B -> C -> D -> Kochi
    Segments (km): 100, 120, 100, 120, 100   (cumulative A=100, B=220,
                   C=320, D=440, Kochi=540)
    Range:  240, Direction: FORWARD

    Among the valid plans returned are ``["A", "C"]`` and ``["B", "D"]``.
    ``["A", "D"]`` is *not* valid (A->D = 340 km > 240), and ``["A"]`` is not
    valid (A->Kochi = 440 km > 240).
"""

from __future__ import annotations

from typing import List, Tuple

from models.route import Direction, Route
from scheduler.route_utils import travel_offsets

# A station as seen along the direction of travel: its name and its cumulative
# distance (km) from the origin endpoint.
StationPosition = Tuple[str, float]


def generate_charging_plans(
    route: Route, battery_range: int, direction: Direction
) -> List[List[str]]:
    """Return every valid charging plan for a bus on ``route``.

    Args:
        route: The route to traverse (stops + segment distances).
        battery_range: Maximum distance, in km, the bus can travel on a full
            charge. Must be positive.
        direction: Travel direction along the route's stop order.

    Returns:
        A list of valid plans. Each plan is an ordered list of station names
        (intermediate stops) where the bus charges. The empty plan ``[]`` is
        returned when the whole trip fits within a single charge. Plans are
        ordered by number of charges, then by station order, for determinism.
        An empty list means no legal plan exists (the route cannot be completed
        within range given the available stations).

    Raises:
        ValueError: If ``battery_range`` is not positive.

    Example:
        For the assignment route with range 240 in the FORWARD direction, the
        result includes ``["A", "C"]``, ``["B", "C"]`` and ``["B", "D"]`` among
        the valid plans.
    """
    if battery_range <= 0:
        raise ValueError(f"battery_range must be positive; got {battery_range}.")

    stations, destination_distance = _station_positions(route, direction)

    plans: List[List[str]] = []
    _extend_plan(
        current_distance=0.0,
        next_index=0,
        plan_so_far=[],
        stations=stations,
        destination_distance=destination_distance,
        battery_range=battery_range,
        results=plans,
    )

    plans.sort(key=lambda plan: (len(plan), plan))
    return plans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _station_positions(
    route: Route, direction: Direction
) -> Tuple[List[StationPosition], float]:
    """Derive intermediate stations (with distances) for a travel direction.

    Walks the route in travel order, accumulating segment distances to give each
    stop's distance from the origin. The two endpoints are dropped — only
    intermediate stops are chargeable.

    Args:
        route: The route to read stops and segment distances from.
        direction: Travel direction; ``BACKWARD`` reverses the route.

    Returns:
        A tuple of ``(stations, destination_distance)`` where ``stations`` is the
        ordered list of ``(name, distance_from_origin)`` for intermediate stops,
        and ``destination_distance`` is the total trip length in km.
    """
    ordered_stops, offsets = travel_offsets(route, direction)

    # Intermediate stops only (exclude the first and last endpoints).
    stations: List[StationPosition] = [
        (name, offsets[name]) for name in ordered_stops[1:-1]
    ]
    return stations, offsets[ordered_stops[-1]]


def _extend_plan(
    current_distance: float,
    next_index: int,
    plan_so_far: List[str],
    stations: List[StationPosition],
    destination_distance: float,
    battery_range: int,
    results: List[List[str]],
) -> None:
    """Recursively build valid plans by depth-first search with range pruning.

    At each step the bus sits at ``current_distance`` having charged at the
    stations in ``plan_so_far``. If the destination is now reachable, the plan is
    complete and recorded. The search then tries extending the plan with each
    later station still within range, pruning anything out of reach.

    Args:
        current_distance: Distance from origin of the bus's last charge (0 at
            the origin before any charge).
        next_index: Index into ``stations`` of the first station eligible to be
            the next charge (enforces route order / no backtracking).
        plan_so_far: Stations charged at so far, in order (mutated during search).
        stations: All intermediate stations as ``(name, distance)``.
        destination_distance: Total trip length in km.
        battery_range: Maximum distance per charge.
        results: Accumulator that completed valid plans are appended to.
    """
    # A plan is complete whenever the destination is reachable from here.
    if destination_distance - current_distance <= battery_range:
        results.append(list(plan_so_far))

    # Try charging next at any later station that is within range.
    for index in range(next_index, len(stations)):
        name, distance = stations[index]
        if _within_range(current_distance, distance, battery_range):
            plan_so_far.append(name)
            _extend_plan(
                current_distance=distance,
                next_index=index + 1,
                plan_so_far=plan_so_far,
                stations=stations,
                destination_distance=destination_distance,
                battery_range=battery_range,
                results=results,
            )
            plan_so_far.pop()


def _within_range(from_distance: float, to_distance: float, battery_range: int) -> bool:
    """Return whether a forward leg is drivable on a single charge.

    Args:
        from_distance: Distance from origin of the current position.
        to_distance: Distance from origin of the next charge stop.
        battery_range: Maximum distance per charge.

    Returns:
        ``True`` if the leg moves forward and is no longer than ``battery_range``.
    """
    leg = to_distance - from_distance
    return 0 < leg <= battery_range
