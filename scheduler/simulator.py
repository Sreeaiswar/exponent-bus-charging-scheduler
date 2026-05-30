"""Event-driven charging simulator.

Executes an already-chosen charging plan for every bus and produces the concrete
timeline that results once buses contend for chargers. This module is pure
mechanics: it advances time through discrete events, models each station's
chargers as a finite resource, and serves waiting buses first-come-first-served.

It deliberately does **not** decide which plan a bus uses, nor who-charges-first
by weight — those are later concerns. Swapping FCFS for weighted prioritisation
later means changing only how the waiting queue is ordered (see ``_next_waiting``).

Time model:
    All times are integer minutes since midnight. Travel time is derived from
    distance and a constant speed (default 60 km/h, i.e. 1 km == 1 min), matching
    the assignment's "travel time is determined by distance" rule.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from models.scenario import Scenario
from scheduler.route_utils import travel_offsets

DEFAULT_SPEED_KMPH = 60


class EventType(Enum):
    """The discrete events that make up a bus's journey."""

    DEPART = "depart"
    ARRIVE_STATION = "arrive_station"
    CHARGE_START = "charge_start"
    CHARGE_END = "charge_end"
    ARRIVE_DESTINATION = "arrive_destination"


# ---------------------------------------------------------------------------
# Output structures
# ---------------------------------------------------------------------------


@dataclass
class ChargeStop:
    """One charging stop on a bus's timeline.

    Attributes:
        station_id: Station where the bus charged.
        charger_id: Charger used.
        arrive: Minute the bus arrived at the station.
        wait: Minutes spent waiting for a charger (0 if one was free).
        charge_start: Minute charging began.
        charge_end: Minute charging finished.
    """

    station_id: str
    charger_id: str
    arrive: int
    wait: int
    charge_start: int
    charge_end: int


@dataclass
class BusTimeline:
    """The full computed timeline for a single bus.

    Attributes:
        bus_id: Bus identifier.
        operator: Operating company.
        direction: Travel direction value (``"forward"``/``"backward"``).
        origin: Origin endpoint name.
        destination: Destination endpoint name.
        departure_time: Minute the bus left its origin.
        stops: Charging stops in route order.
        arrival_time: Minute the bus reached its destination.
    """

    bus_id: str
    operator: str
    direction: str
    origin: str
    destination: str
    departure_time: int
    stops: List[ChargeStop] = field(default_factory=list)
    arrival_time: Optional[int] = None


@dataclass
class ChargerSlot:
    """A single occupancy block on a charger.

    Attributes:
        charger_id: Charger that was used.
        bus_id: Bus that occupied it.
        start: Minute charging began.
        end: Minute charging finished.
    """

    charger_id: str
    bus_id: str
    start: int
    end: int


@dataclass
class StationOccupancy:
    """The ordered usage log for one station's chargers.

    Attributes:
        station_id: Station identifier.
        slots: Occupancy blocks ordered by start time, then charger id —
            i.e. the order buses charged here.
    """

    station_id: str
    slots: List[ChargerSlot] = field(default_factory=list)


@dataclass
class SimulationResult:
    """Everything the simulation produced.

    Attributes:
        buses: Per-bus timelines keyed by bus id.
        stations: Per-station occupancy keyed by station id.
    """

    buses: Dict[str, BusTimeline]
    stations: Dict[str, StationOccupancy]


# ---------------------------------------------------------------------------
# Internal runtime state (not part of the public output)
# ---------------------------------------------------------------------------


@dataclass
class _Event:
    """An item on the simulation's event queue."""

    type: EventType
    bus_id: str
    stop_index: int = -1  # index into the bus's plan (for station events)
    charger_id: Optional[str] = None


@dataclass
class _BusRun:
    """Mutable per-bus state tracked while the simulation runs."""

    timeline: BusTimeline
    plan: List[str]                 # ordered station names the bus charges at
    offsets: Dict[str, float]       # station/endpoint name -> km from origin
    destination_offset: float       # total trip length in km


@dataclass
class _StationState:
    """Mutable per-station charger state tracked while the simulation runs."""

    free_chargers: List[str]              # ids of currently-idle chargers
    waiting: List[str] = field(default_factory=list)  # bus ids, FCFS order


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class Simulator:
    """Runs the discrete-event charging simulation for a set of buses.

    Args:
        scenario: The world (route, stations, charging time) to simulate.
        plans: Mapping of ``bus_id -> ordered list of station names`` the bus
            charges at. Plans must be valid for the bus's direction (as produced
            by the plan generator).
        speed_kmph: Constant travel speed used to convert distance to minutes.
    """

    def __init__(
        self,
        scenario: Scenario,
        plans: Dict[str, List[str]],
        speed_kmph: int = DEFAULT_SPEED_KMPH,
    ) -> None:
        self._scenario = scenario
        self._plans = plans
        self._speed_kmph = speed_kmph

        self._queue: List[Tuple[int, int, _Event]] = []
        self._seq = itertools.count()  # stable tie-breaker for equal times

        self._runs: Dict[str, _BusRun] = {}
        self._stations: Dict[str, _StationState] = {}
        self._occupancy: Dict[str, StationOccupancy] = {}

    # -- public API --------------------------------------------------------

    def run(self) -> SimulationResult:
        """Execute the simulation and return per-bus and per-station results."""
        self._init_stations()
        self._init_buses()
        self._drain_queue()
        return self._build_result()

    # -- setup -------------------------------------------------------------

    def _init_stations(self) -> None:
        """Initialise charger availability and occupancy logs per station."""
        for station in self._scenario.stations:
            charger_ids = [charger.charger_id for charger in station.chargers]
            self._stations[station.station_id] = _StationState(
                free_chargers=sorted(charger_ids)
            )
            self._occupancy[station.station_id] = StationOccupancy(
                station_id=station.station_id
            )

    def _init_buses(self) -> None:
        """Build each bus's runtime state and seed its DEPART event."""
        for bus in self._scenario.buses:
            ordered_stops, offsets = travel_offsets(
                self._scenario.route, bus.direction
            )
            origin, destination = ordered_stops[0], ordered_stops[-1]
            plan = list(self._plans.get(bus.bus_id, []))

            run = _BusRun(
                timeline=BusTimeline(
                    bus_id=bus.bus_id,
                    operator=bus.operator,
                    direction=bus.direction.value,
                    origin=origin,
                    destination=destination,
                    departure_time=bus.departure_time,
                ),
                plan=plan,
                offsets=offsets,
                destination_offset=offsets[destination],
            )
            self._runs[bus.bus_id] = run
            self._push(bus.departure_time, _Event(EventType.DEPART, bus.bus_id))

    # -- event loop --------------------------------------------------------

    def _drain_queue(self) -> None:
        """Process events in time order until the queue is empty."""
        handlers = {
            EventType.DEPART: self._on_depart,
            EventType.ARRIVE_STATION: self._on_arrive_station,
            EventType.CHARGE_START: self._on_charge_start,
            EventType.CHARGE_END: self._on_charge_end,
            EventType.ARRIVE_DESTINATION: self._on_arrive_destination,
        }
        while self._queue:
            time, _, event = heapq.heappop(self._queue)
            handlers[event.type](time, event)

    def _on_depart(self, time: int, event: _Event) -> None:
        """Bus leaves its origin (full charge) and heads to its first stop."""
        run = self._runs[event.bus_id]
        if run.plan:
            travel = self._travel(0.0, run.offsets[run.plan[0]])
            self._push(
                time + travel,
                _Event(EventType.ARRIVE_STATION, event.bus_id, stop_index=0),
            )
        else:
            travel = self._travel(0.0, run.destination_offset)
            self._push(
                time + travel, _Event(EventType.ARRIVE_DESTINATION, event.bus_id)
            )

    def _on_arrive_station(self, time: int, event: _Event) -> None:
        """Bus reaches a charge stop; grab a charger or join the FCFS queue."""
        run = self._runs[event.bus_id]
        station_id = run.plan[event.stop_index]
        state = self._stations[station_id]

        # Record arrival; wait/charge fields are filled when a charger is granted.
        run.timeline.stops.append(
            ChargeStop(
                station_id=station_id,
                charger_id="",
                arrive=time,
                wait=0,
                charge_start=time,
                charge_end=time,
            )
        )

        if state.free_chargers:
            charger_id = state.free_chargers.pop(0)
            self._grant(time, event.bus_id, event.stop_index, charger_id)
        else:
            state.waiting.append(event.bus_id)

    def _on_charge_start(self, time: int, event: _Event) -> None:
        """Bus plugs in; mark the charge window and schedule its completion."""
        run = self._runs[event.bus_id]
        stop = run.timeline.stops[event.stop_index]
        duration = self._scenario.charging_time

        stop.charger_id = event.charger_id or ""
        stop.charge_start = time
        stop.charge_end = time + duration

        self._push(
            time + duration,
            _Event(
                EventType.CHARGE_END,
                event.bus_id,
                stop_index=event.stop_index,
                charger_id=event.charger_id,
            ),
        )

    def _on_charge_end(self, time: int, event: _Event) -> None:
        """Bus finishes charging; free the charger, move on, serve the queue."""
        run = self._runs[event.bus_id]
        stop = run.timeline.stops[event.stop_index]
        station_id = stop.station_id
        charger_id = event.charger_id or ""

        # Log this completed occupancy block.
        self._occupancy[station_id].slots.append(
            ChargerSlot(
                charger_id=charger_id,
                bus_id=event.bus_id,
                start=stop.charge_start,
                end=stop.charge_end,
            )
        )

        # Send the bus on to its next stop or its destination.
        self._advance(time, run, event.stop_index)

        # Hand the just-freed charger to the next waiting bus, if any.
        state = self._stations[station_id]
        next_bus_id = self._next_waiting(state)
        if next_bus_id is not None:
            next_run = self._runs[next_bus_id]
            next_stop_index = self._current_stop_index(next_run, station_id)
            self._grant(time, next_bus_id, next_stop_index, charger_id)
        else:
            state.free_chargers.append(charger_id)
            state.free_chargers.sort()

    def _on_arrive_destination(self, time: int, event: _Event) -> None:
        """Bus reaches its destination; the journey is complete."""
        self._runs[event.bus_id].timeline.arrival_time = time

    # -- helpers -----------------------------------------------------------

    def _grant(self, time: int, bus_id: str, stop_index: int, charger_id: str) -> None:
        """Give a charger to a bus now, recording any wait, and start charging."""
        run = self._runs[bus_id]
        stop = run.timeline.stops[stop_index]
        stop.wait = time - stop.arrive
        self._push(
            time,
            _Event(
                EventType.CHARGE_START,
                bus_id,
                stop_index=stop_index,
                charger_id=charger_id,
            ),
        )

    def _advance(self, time: int, run: _BusRun, stop_index: int) -> None:
        """Schedule the bus's next arrival after finishing a charge."""
        current_offset = run.offsets[run.plan[stop_index]]
        next_index = stop_index + 1
        if next_index < len(run.plan):
            travel = self._travel(current_offset, run.offsets[run.plan[next_index]])
            self._push(
                time + travel,
                _Event(
                    EventType.ARRIVE_STATION, run.timeline.bus_id, stop_index=next_index
                ),
            )
        else:
            travel = self._travel(current_offset, run.destination_offset)
            self._push(
                time + travel,
                _Event(EventType.ARRIVE_DESTINATION, run.timeline.bus_id),
            )

    @staticmethod
    def _next_waiting(state: _StationState) -> Optional[str]:
        """Pick the next bus to charge from the waiting queue (FCFS for now).

        This is the single seam where weighted prioritisation will later plug in:
        replace the front-of-queue pop with a weighted selection over
        ``state.waiting``. Nothing else in the simulator needs to change.
        """
        if state.waiting:
            return state.waiting.pop(0)
        return None

    @staticmethod
    def _current_stop_index(run: _BusRun, station_id: str) -> int:
        """Find the index of the stop a waiting bus is currently sitting at."""
        for index in range(len(run.timeline.stops) - 1, -1, -1):
            if run.timeline.stops[index].station_id == station_id:
                return index
        raise ValueError(
            f"Bus {run.timeline.bus_id} has no recorded arrival at {station_id}."
        )

    def _travel(self, from_offset: float, to_offset: float) -> int:
        """Convert a forward distance (km) into whole minutes of travel."""
        distance = to_offset - from_offset
        return int(round(distance * 60 / self._speed_kmph))

    def _push(self, time: int, event: _Event) -> None:
        """Enqueue an event at ``time`` with a stable insertion order."""
        heapq.heappush(self._queue, (time, next(self._seq), event))

    def _build_result(self) -> SimulationResult:
        """Assemble the public result, ordering station occupancy by start time."""
        for occupancy in self._occupancy.values():
            occupancy.slots.sort(key=lambda slot: (slot.start, slot.charger_id))
        return SimulationResult(
            buses={bus_id: run.timeline for bus_id, run in self._runs.items()},
            stations=self._occupancy,
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def simulate(
    scenario: Scenario,
    plans: Dict[str, List[str]],
    speed_kmph: int = DEFAULT_SPEED_KMPH,
) -> SimulationResult:
    """Run a simulation for ``scenario`` with the given per-bus ``plans``."""
    return Simulator(scenario, plans, speed_kmph=speed_kmph).run()
