"""Station domain models: a charging station and the chargers it holds.

A station owns a list of chargers rather than a single hardcoded charger, so
"double the chargers at B" or "give every station two chargers" is a data-only
change with no impact on the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Charger:
    """A single charger at a station.

    Attributes:
        charger_id: Identifier unique within its station (e.g. ``"A-1"``).
    """

    charger_id: str


@dataclass
class Station:
    """A charging station positioned at one of the route's stops.

    Attributes:
        station_id: Identifier matching the corresponding stop name
            (e.g. ``"A"``).
        chargers: Chargers available at this station. The number of chargers is
            data-driven, so capacity can grow without code changes.
    """

    station_id: str
    chargers: List[Charger] = field(default_factory=list)
