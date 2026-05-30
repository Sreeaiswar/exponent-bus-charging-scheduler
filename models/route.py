"""Route domain models: travel direction, individual segments, and the full route.

A route is modelled as an ordered list of stops with the segments that connect
them. Everything is direction-agnostic: the same route is traversed forwards
(first stop -> last stop) or backwards (last stop -> first stop). This keeps the
model reusable for any future route, not just Bengaluru -> Kochi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Direction(Enum):
    """Direction of travel along a route's ordered ``stops``.

    ``FORWARD`` follows ``stops`` from first to last; ``BACKWARD`` follows them
    in reverse. Expressing direction relative to stop order (rather than naming
    concrete endpoints) means a new route works without touching this enum.

    For the assignment's route:
        FORWARD  -> Bengaluru -> Kochi
        BACKWARD -> Kochi -> Bengaluru
    """

    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass
class RouteSegment:
    """A single leg of the route between two adjacent stops.

    Attributes:
        source: Name of the stop the segment starts at.
        destination: Name of the stop the segment ends at.
        distance: Length of the segment in kilometres.
    """

    source: str
    destination: str
    distance: float


@dataclass
class Route:
    """An ordered route described by its stops and connecting segments.

    Attributes:
        stops: Ordered stop names, including the two endpoints
            (e.g. ``["Bengaluru", "A", "B", "C", "D", "Kochi"]``).
        segments: Segments connecting consecutive stops, in route order.
    """

    stops: List[str] = field(default_factory=list)
    segments: List[RouteSegment] = field(default_factory=list)
