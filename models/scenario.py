"""Scenario domain model: the complete, self-describing input to the scheduler.

A scenario bundles the world (route, stations), the fleet (buses), the physical
constants (battery range, charging time), and the tunable soft-rule weights. It
is the single object the scheduler reads, so every knob the assignment cares
about lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from models.bus import Bus
from models.route import Route
from models.station import Station


@dataclass
class Scenario:
    """A full scheduling situation read from a single scenario data file.

    Attributes:
        scenario_name: Human-readable name (e.g. ``"Even spacing"``).
        route: The route all buses travel.
        stations: Charging stations along the route.
        buses: The fleet to be scheduled.
        battery_range: Maximum distance on a full charge, in kilometres.
        charging_time: Time for one charge (always to full), in minutes.
        weights: Tunable soft-rule weights keyed by name
            (e.g. ``{"individual": 1.0, "operator": 1.0, "overall": 1.0}``).
            A plain dict so new weight keys need no schema change.
    """

    scenario_name: str
    route: Route
    stations: List[Station] = field(default_factory=list)
    buses: List[Bus] = field(default_factory=list)
    battery_range: float = 240.0
    charging_time: int = 25
    weights: Dict[str, float] = field(default_factory=dict)
