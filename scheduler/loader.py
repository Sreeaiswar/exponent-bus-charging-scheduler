
"""Scenario loader: read a scenario JSON file and build the domain models.

This is the single boundary where untrusted external data becomes validated
domain objects. It parses the approved scenario schema, converts human-friendly
values (``"19:00"`` -> minutes, ``"forward"`` -> :class:`Direction`), expands a
charger *count* into individual :class:`Charger` objects, and fails loudly with
a descriptive message when anything is missing or malformed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from models.bus import Bus
from models.route import Direction, Route, RouteSegment
from models.scenario import Scenario
from models.station import Charger, Station

# Soft-rule weights every scenario must declare (see the assignment).
REQUIRED_WEIGHTS = ("individual", "operator", "overall")

# Accepted direction tokens in the JSON, mapped to the domain enum.
_DIRECTIONS: Dict[str, Direction] = {
    "forward": Direction.FORWARD,
    "backward": Direction.BACKWARD,
}


class ScenarioError(ValueError):
    """Raised when a scenario file is missing data or fails validation.

    Carries a human-readable message that points at the offending field so the
    problem can be fixed in the data file without reading the loader code.
    """


def load_scenario(path: str) -> Scenario:
    """Load and validate a scenario from a JSON file on disk.

    Args:
        path: Filesystem path to the scenario JSON file.

    Returns:
        A fully populated, validated :class:`Scenario`.

    Raises:
        ScenarioError: If the file cannot be read, is not valid JSON, or does
            not satisfy the scenario schema.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ScenarioError(f"Scenario file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"Scenario file is not valid JSON ({path}): {exc}") from exc

    return load_scenario_dict(data)


def load_scenario_dict(data: Dict[str, Any]) -> Scenario:
    """Build and validate a :class:`Scenario` from an already-parsed dict.

    Kept separate from :func:`load_scenario` so scenarios can also be built from
    in-memory dicts (tests, the UI) without touching disk.

    Args:
        data: The parsed contents of a scenario JSON file.

    Returns:
        A fully populated, validated :class:`Scenario`.

    Raises:
        ScenarioError: If the data does not satisfy the scenario schema.
    """
    if not isinstance(data, dict):
        raise ScenarioError("Scenario root must be a JSON object.")

    scenario_name = _require(data, "scenario_name", str)
    route = _build_route(_require(data, "route", dict))
    stations = _build_stations(_require(data, "stations", list))
    physical = _require(data, "physical", dict)
    weights = _build_weights(_require(data, "weights", dict))
    buses = _build_buses(_require(data, "buses", list))

    battery_range = _require_positive_number(physical, "battery_range", "physical.battery_range")
    charging_time = _require_positive_number(physical, "charging_time", "physical.charging_time")

    return Scenario(
        scenario_name=scenario_name,
        route=route,
        stations=stations,
        buses=buses,
        battery_range=int(battery_range),
        charging_time=int(charging_time),
        weights=weights,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_route(raw: Dict[str, Any]) -> Route:
    """Parse the ``route`` object into a :class:`Route` with its segments."""
    stops = _require(raw, "stops", list, context="route.stops")
    if len(stops) < 2:
        raise ScenarioError("route.stops must list at least two stops (the two endpoints).")
    for stop in stops:
        if not isinstance(stop, str) or not stop:
            raise ScenarioError(f"route.stops must be non-empty strings; got {stop!r}.")

    raw_segments = _require(raw, "segments", list, context="route.segments")
    if not raw_segments:
        raise ScenarioError("route.segments must contain at least one segment.")

    segments: List[RouteSegment] = []
    for index, raw_segment in enumerate(raw_segments):
        where = f"route.segments[{index}]"
        if not isinstance(raw_segment, dict):
            raise ScenarioError(f"{where} must be an object.")
        source = _require(raw_segment, "source", str, context=f"{where}.source")
        destination = _require(raw_segment, "destination", str, context=f"{where}.destination")
        distance = _require_positive_number(raw_segment, "distance", f"{where}.distance")
        segments.append(
            RouteSegment(source=source, destination=destination, distance=float(distance))
        )

    return Route(stops=list(stops), segments=segments)


def _build_stations(raw_stations: List[Any]) -> List[Station]:
    """Parse the ``stations`` array, expanding each charger count into objects."""
    if not raw_stations:
        raise ScenarioError("stations must contain at least one station.")

    stations: List[Station] = []
    seen_ids = set()
    for index, raw_station in enumerate(raw_stations):
        where = f"stations[{index}]"
        if not isinstance(raw_station, dict):
            raise ScenarioError(f"{where} must be an object.")

        station_id = _require(raw_station, "station_id", str, context=f"{where}.station_id")
        if station_id in seen_ids:
            raise ScenarioError(f"Duplicate station_id {station_id!r}.")
        seen_ids.add(station_id)

        count = _require(raw_station, "chargers", int, context=f"{where}.chargers")
        if isinstance(count, bool) or count < 1:
            raise ScenarioError(
                f"{where}.chargers must be a positive integer; got {count!r}."
            )

        chargers = [Charger(charger_id=f"{station_id}-{n}") for n in range(1, count + 1)]
        stations.append(Station(station_id=station_id, chargers=chargers))

    return stations


def _build_buses(raw_buses: List[Any]) -> List[Bus]:
    """Parse the ``buses`` array into :class:`Bus` objects."""
    if not raw_buses:
        raise ScenarioError("buses must contain at least one bus.")

    buses: List[Bus] = []
    seen_ids = set()
    for index, raw_bus in enumerate(raw_buses):
        where = f"buses[{index}]"
        if not isinstance(raw_bus, dict):
            raise ScenarioError(f"{where} must be an object.")

        bus_id = _require(raw_bus, "bus_id", str, context=f"{where}.bus_id")
        if bus_id in seen_ids:
            raise ScenarioError(f"Duplicate bus_id {bus_id!r}.")
        seen_ids.add(bus_id)

        operator = _require(raw_bus, "operator", str, context=f"{where}.operator")
        direction = _parse_direction(
            _require(raw_bus, "direction", str, context=f"{where}.direction"), where
        )
        departure = _parse_time(
            _require(raw_bus, "departure", str, context=f"{where}.departure"), where
        )

        buses.append(
            Bus(
                bus_id=bus_id,
                operator=operator,
                direction=direction,
                departure_time=departure,
            )
        )

    return buses


def _build_weights(raw_weights: Dict[str, Any]) -> Dict[str, float]:
    """Validate and coerce the ``weights`` object to ``{name: float}``.

    Every required weight must be present; extra keys are allowed and preserved
    so future soft rules can introduce new weights without a schema change.
    """
    weights: Dict[str, float] = {}
    for key, value in raw_weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScenarioError(f"weights.{key} must be a number; got {value!r}.")
        weights[key] = float(value)

    missing = [name for name in REQUIRED_WEIGHTS if name not in weights]
    if missing:
        raise ScenarioError(f"weights is missing required keys: {', '.join(missing)}.")

    return weights


# ---------------------------------------------------------------------------
# Field-level helpers
# ---------------------------------------------------------------------------


def _require(data: Dict[str, Any], key: str, expected_type: type, context: str = "") -> Any:
    """Fetch ``key`` from ``data``, asserting presence and type.

    Args:
        data: The object to read from.
        key: The key that must be present.
        expected_type: The type the value must be an instance of.
        context: Optional dotted path used in error messages (defaults to ``key``).

    Returns:
        The value at ``key``.

    Raises:
        ScenarioError: If the key is absent or the value is the wrong type.
    """
    where = context or key
    if key not in data:
        raise ScenarioError(f"Missing required field: {where}.")

    value = data[key]
    # bool is a subclass of int; reject it where a real int/number is expected.
    if expected_type in (int, float) and isinstance(value, bool):
        raise ScenarioError(f"{where} must be a number, not a boolean.")
    if not isinstance(value, expected_type):
        raise ScenarioError(
            f"{where} must be of type {expected_type.__name__}; got {type(value).__name__}."
        )
    return value


def _require_positive_number(data: Dict[str, Any], key: str, context: str) -> float:
    """Fetch a strictly positive number at ``key``.

    Raises:
        ScenarioError: If absent, non-numeric, or not greater than zero.
    """
    if key not in data:
        raise ScenarioError(f"Missing required field: {context}.")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{context} must be a number; got {value!r}.")
    if value <= 0:
        raise ScenarioError(f"{context} must be positive; got {value}.")
    return float(value)


def _parse_direction(token: str, context: str) -> Direction:
    """Map a ``"forward"``/``"backward"`` token to a :class:`Direction`.

    Raises:
        ScenarioError: If the token is not a recognised direction.
    """
    direction = _DIRECTIONS.get(token)
    if direction is None:
        valid = ", ".join(sorted(_DIRECTIONS))
        raise ScenarioError(
            f"{context}.direction must be one of [{valid}]; got {token!r}."
        )
    return direction


def _parse_time(value: str, context: str) -> int:
    """Convert a 24-hour ``"HH:MM"`` string to minutes since midnight.

    Args:
        value: A time string such as ``"19:00"``.
        context: Dotted path used in error messages.

    Returns:
        Minutes since midnight (``"19:00"`` -> ``1140``).

    Raises:
        ScenarioError: If the string is not a valid ``HH:MM`` time.
    """
    parts = value.split(":")
    if len(parts) != 2:
        raise ScenarioError(
            f"{context}.departure must be in 'HH:MM' format; got {value!r}."
        )
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError as exc:
        raise ScenarioError(
            f"{context}.departure must be in 'HH:MM' format; got {value!r}."
        ) from exc

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ScenarioError(
            f"{context}.departure must be a valid 24h time; got {value!r}."
        )
    return hours * 60 + minutes
