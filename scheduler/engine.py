"""Scheduling engine: the orchestrator that ties the pipeline together.

The engine owns no scheduling mechanics of its own. It runs the pipeline:

    load (caller) -> generate valid plans -> select one plan per bus
                  -> simulate -> assemble the final schedule

Plan *selection* is the only new logic here. It is intentionally simple:

    1. Prefer fewer charging stops (each charge costs a fixed 25 min + contention).
    2. Break ties by lower *estimated* congestion (a cheap, free-flow guess).
    3. Break remaining ties deterministically (lexicographic station order).

Congestion is only *estimated* up front (no simulation) so selection stays fast.
The simulator remains the source of truth for real arrival and wait times.

Weighted plan scoring and weighted who-charges-first are deliberately NOT here
yet. Selection is isolated in :meth:`Engine._select_plan`, which is the single
seam a future weighted strategy plugs into without touching the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from models.route import Direction
from models.scenario import Scenario
from scheduler.plan_generator import generate_charging_plans
from scheduler.route_utils import travel_offsets
from scheduler.simulator import DEFAULT_SPEED_KMPH, SimulationResult, simulate


@dataclass
class ScheduleResult:
    """The complete output of an engine run.

    Attributes:
        scenario_name: Name of the scenario that was scheduled.
        weights: The scenario's soft-rule weights (carried through for the UI;
            not yet applied to selection).
        chosen_plans: Mapping of ``bus_id -> ordered station names`` selected.
        simulation: The simulator's per-bus timelines and per-station occupancy.
        infeasible_buses: Bus ids for which no valid charging plan exists.
    """

    scenario_name: str
    weights: Dict[str, float]
    chosen_plans: Dict[str, List[str]]
    simulation: SimulationResult
    infeasible_buses: List[str] = field(default_factory=list)


class Engine:
    """Selects a charging plan for every bus, then simulates the result.

    Args:
        scenario: The world to schedule (route, stations, buses, constants).
        speed_kmph: Constant travel speed, shared with the simulator so the
            congestion estimate and the simulation use the same time model.
    """

    def __init__(self, scenario: Scenario, speed_kmph: int = DEFAULT_SPEED_KMPH) -> None:
        self._scenario = scenario
        self._speed_kmph = speed_kmph
        # Buses arriving within this many minutes of each other contend for the
        # same charger, so this is the window the congestion estimate counts over.
        self._window = scenario.charging_time

    # -- public API --------------------------------------------------------

    def run(self) -> ScheduleResult:
        """Select plans for all buses, simulate, and return the final schedule."""
        chosen_plans, infeasible = self._select_all_plans()
        simulation = simulate(self._scenario, chosen_plans, speed_kmph=self._speed_kmph)
        return ScheduleResult(
            scenario_name=self._scenario.scenario_name,
            weights=dict(self._scenario.weights),
            chosen_plans=chosen_plans,
            simulation=simulation,
            infeasible_buses=infeasible,
        )

    # -- plan selection ----------------------------------------------------

    def _select_all_plans(self) -> Tuple[Dict[str, List[str]], List[str]]:
        """Choose one plan per bus in departure order, spreading load greedily.

        Buses are processed in ``(departure_time, bus_id)`` order. Each bus picks
        its best plan against the running demand profile, then its predicted
        arrivals are added to that profile so later buses see the load already
        committed and steer toward emptier stations.

        Returns:
            ``(chosen_plans, infeasible_buses)``.
        """
        charger_count = {
            station.station_id: len(station.chargers)
            for station in self._scenario.stations
        }
        # station_id -> predicted (free-flow) arrival minutes already committed.
        demand: Dict[str, List[int]] = {
            station.station_id: [] for station in self._scenario.stations
        }
        # Cache the km-offset map per direction (it only depends on the route).
        offsets_cache: Dict[Direction, Dict[str, float]] = {}

        chosen_plans: Dict[str, List[str]] = {}
        infeasible: List[str] = []

        ordered_buses = sorted(
            self._scenario.buses, key=lambda bus: (bus.departure_time, bus.bus_id)
        )

        for bus in ordered_buses:
            if bus.direction not in offsets_cache:
                _, offsets_cache[bus.direction] = travel_offsets(
                    self._scenario.route, bus.direction
                )
            offsets = offsets_cache[bus.direction]

            valid_plans = generate_charging_plans(
                self._scenario.route, self._scenario.battery_range, bus.direction
            )
            if not valid_plans:
                infeasible.append(bus.bus_id)
                chosen_plans[bus.bus_id] = []
                continue

            plan = self._select_plan(
                valid_plans, bus.departure_time, offsets, demand, charger_count
            )
            chosen_plans[bus.bus_id] = plan

            # Commit this bus's predicted arrivals so later buses see the load.
            for station_id in plan:
                demand.setdefault(station_id, []).append(
                    self._arrival(bus.departure_time, offsets[station_id])
                )

        return chosen_plans, infeasible

    def _select_plan(
        self,
        valid_plans: List[List[str]],
        departure_time: int,
        offsets: Dict[str, float],
        demand: Dict[str, List[int]],
        charger_count: Dict[str, int],
    ) -> List[str]:
        """Pick the best plan by (fewest stops, congestion, lexicographic order).

        Args:
            valid_plans: All legal plans for the bus.
            departure_time: Bus departure minute (for predicted arrivals).
            offsets: Stop -> km-from-origin map for the bus's direction.
            demand: Running demand profile of committed predicted arrivals.
            charger_count: Station id -> number of chargers.

        Returns:
            The chosen plan (a list of station names).
        """
        return min(
            valid_plans,
            key=lambda plan: (
                len(plan),
                self._estimate_congestion(
                    plan, departure_time, offsets, demand, charger_count
                ),
                tuple(plan),
            ),
        )

    def _estimate_congestion(
        self,
        plan: List[str],
        departure_time: int,
        offsets: Dict[str, float],
        demand: Dict[str, List[int]],
        charger_count: Dict[str, int],
    ) -> float:
        """Estimate a plan's congestion against the current demand profile.

        For each station in the plan, counts how many already-committed buses are
        predicted to arrive within the contention window of this bus, divided by
        the station's charger count (more chargers -> less congestion).

        Returns:
            The summed, charger-normalised congestion score (lower is better).
        """
        total = 0.0
        for station_id in plan:
            arrival = self._arrival(departure_time, offsets[station_id])
            nearby = sum(
                1
                for other in demand.get(station_id, [])
                if abs(other - arrival) <= self._window
            )
            chargers = charger_count.get(station_id, 1)
            total += nearby / chargers
        return total

    def _arrival(self, departure_time: int, offset_km: float) -> int:
        """Predicted free-flow arrival minute at a stop ``offset_km`` away."""
        return departure_time + int(round(offset_km * 60 / self._speed_kmph))


def schedule(scenario: Scenario, speed_kmph: int = DEFAULT_SPEED_KMPH) -> ScheduleResult:
    """Convenience wrapper: build an :class:`Engine` and run it."""
    return Engine(scenario, speed_kmph=speed_kmph).run()
