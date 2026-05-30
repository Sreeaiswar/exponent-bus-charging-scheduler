"""Bus domain model: a single bus and its scheduled departure.

A bus only carries the facts that come from the scenario input. Everything the
scheduler computes (charging plan, waits, arrival time) lives in scheduler
output structures, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.route import Direction


@dataclass
class Bus:
    """A bus departing from one end of the route at a scheduled time.

    Attributes:
        bus_id: Unique identifier (e.g. ``"bus-BK-01"``).
        operator: Operating company name (e.g. ``"kpn"``).
        direction: Travel direction along the route's stop order.
        departure_time: Departure time in minutes since midnight
            (e.g. ``19:00`` is ``1140``).
    """

    bus_id: str
    operator: str
    direction: Direction
    departure_time: int
