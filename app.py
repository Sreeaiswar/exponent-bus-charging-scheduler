"""Streamlit UI for the bus charging scheduler.

A thin presentation layer over the existing backend. It loads a chosen scenario
with the project's loader, runs the scheduling :class:`Engine`, and renders the
resulting :class:`ScheduleResult` across three tabs: the scenario input, the
per-bus schedules, and the per-station charger occupancy.

This module only reads backend objects and formats them; it contains no
scheduling logic of its own.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from models.scenario import Scenario
from scheduler.engine import Engine, ScheduleResult
from scheduler.loader import ScenarioError, load_scenario

# Scenario files live alongside this app in the ``scenarios`` directory.
SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")
SCENARIO_FILES = [
    "scenario1.json",
    "scenario2.json",
    "scenario3.json",
    "scenario4.json",
    "scenario5.json",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def format_minutes(minutes: Optional[int]) -> str:
    """Format minutes-since-midnight as a ``HH:MM`` clock time.

    Times past midnight roll over and are tagged with a ``(+Nd)`` day suffix so
    a late arrival reads unambiguously. ``None`` (e.g. a bus that never arrived)
    renders as a dash.
    """
    if minutes is None:
        return "—"

    day, clock = divmod(int(minutes), 1440)
    hours, mins = divmod(clock, 60)
    text = f"{hours:02d}:{mins:02d}"
    if day:
        text += f" (+{day}d)"
    return text


def format_duration(minutes: Optional[int]) -> str:
    """Format an elapsed span of minutes as ``Hh MMm`` (e.g. ``6h 05m``).

    Unlike :func:`format_minutes`, this reads a duration rather than a clock
    time, so it never rolls over at midnight. ``None`` renders as a dash.
    """
    if minutes is None:
        return "—"

    hours, mins = divmod(int(minutes), 60)
    return f"{hours}h {mins:02d}m"


def scenario_summary(scenario: Scenario) -> Dict[str, str]:
    """Build a small label -> value map of the scenario's headline facts."""
    return {
        "Scenario name": scenario.scenario_name,
        "Battery range": f"{scenario.battery_range} km",
        "Charging time": f"{scenario.charging_time} min",
        "Stops": str(len(scenario.route.stops)),
        "Stations": str(len(scenario.stations)),
        "Buses": str(len(scenario.buses)),
    }


def bus_summary_dataframe(result: ScheduleResult) -> pd.DataFrame:
    """Summarise every bus's outcome as one row in a DataFrame.

    Covers route endpoints, departure/arrival, total journey and waiting time,
    the chosen charging stations, and whether the bus was schedulable at all.
    """
    infeasible = set(result.infeasible_buses)
    rows: List[Dict[str, object]] = []

    # Sort by departure then id so the table reads in dispatch order.
    timelines = sorted(
        result.simulation.buses.values(),
        key=lambda timeline: (timeline.departure_time, timeline.bus_id),
    )

    for timeline in timelines:
        total_wait = sum(stop.wait for stop in timeline.stops)
        stations = result.chosen_plans.get(timeline.bus_id, [])

        if timeline.arrival_time is not None:
            journey = format_duration(timeline.arrival_time - timeline.departure_time)
        else:
            journey = "—"

        rows.append(
            {
                "Bus": timeline.bus_id,
                "Operator": timeline.operator,
                "Direction": timeline.direction,
                "From": timeline.origin,
                "To": timeline.destination,
                "Departure": format_minutes(timeline.departure_time),
                "Arrival": format_minutes(timeline.arrival_time),
                "Journey": journey,
                "Charge stops": len(stations),
                "Total wait": format_duration(total_wait),
                "Stations": " → ".join(stations) if stations else "—",
                "Feasible": "No" if timeline.bus_id in infeasible else "Yes",
            }
        )

    return pd.DataFrame(rows)


def station_dataframe(result: ScheduleResult, station_id: str) -> pd.DataFrame:
    """Build the occupancy table for one station, ordered as buses charged."""
    occupancy = result.simulation.stations.get(station_id)
    rows: List[Dict[str, object]] = []

    if occupancy is not None:
        for slot in occupancy.slots:
            rows.append(
                {
                    "charger_id": slot.charger_id,
                    "bus_id": slot.bus_id,
                    "start": format_minutes(slot.start),
                    "end": format_minutes(slot.end),
                }
            )

    return pd.DataFrame(rows, columns=["charger_id", "bus_id", "start", "end"])


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def run_scenario(filename: str) -> Tuple[Scenario, ScheduleResult]:
    """Load ``filename`` from the scenarios directory and run the engine.

    Cached on the filename so flipping between tabs does not re-simulate.
    """
    scenario = load_scenario(os.path.join(SCENARIO_DIR, filename))
    return scenario, Engine(scenario).run()


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------


def render_scenario_tab(scenario: Scenario) -> None:
    """Render the scenario input: constants, weights, route, stations, buses."""
    st.subheader(scenario.scenario_name)

    summary = scenario_summary(scenario)
    cols = st.columns(4)
    cols[0].metric("Battery range", summary["Battery range"])
    cols[1].metric("Charging time", summary["Charging time"])
    cols[2].metric("Stations", summary["Stations"])
    cols[3].metric("Buses", summary["Buses"])

    st.markdown("**Weights**")
    weights_df = pd.DataFrame(
        [{"weight": name, "value": value} for name, value in scenario.weights.items()]
    )
    st.dataframe(weights_df, use_container_width=True, hide_index=True)

    st.markdown("**Route**")
    st.write(" → ".join(scenario.route.stops))
    segments_df = pd.DataFrame(
        [
            {
                "source": segment.source,
                "destination": segment.destination,
                "distance (km)": segment.distance,
            }
            for segment in scenario.route.segments
        ]
    )
    st.dataframe(segments_df, use_container_width=True, hide_index=True)

    st.markdown("**Stations**")
    stations_df = pd.DataFrame(
        [
            {
                "station_id": station.station_id,
                "chargers": len(station.chargers),
                "charger_ids": ", ".join(charger.charger_id for charger in station.chargers),
            }
            for station in scenario.stations
        ]
    )
    st.dataframe(stations_df, use_container_width=True, hide_index=True)

    st.markdown("**Buses**")
    buses_df = pd.DataFrame(
        [
            {
                "bus_id": bus.bus_id,
                "operator": bus.operator,
                "direction": bus.direction.value,
                "departure": format_minutes(bus.departure_time),
            }
            for bus in scenario.buses
        ]
    )
    st.dataframe(buses_df, use_container_width=True, hide_index=True)


def render_bus_schedules_tab(result: ScheduleResult) -> None:
    """Render the per-bus summary table and an expandable timeline per bus."""
    if result.infeasible_buses:
        st.warning(
            "No valid charging plan for: " + ", ".join(sorted(result.infeasible_buses))
        )

    st.markdown("**Summary**")
    st.dataframe(
        bus_summary_dataframe(result), use_container_width=True, hide_index=True
    )

    st.markdown("**Detailed timelines**")
    timelines = sorted(
        result.simulation.buses.values(),
        key=lambda timeline: (timeline.departure_time, timeline.bus_id),
    )
    for timeline in timelines:
        label = (
            f"{timeline.bus_id} — {timeline.origin} → {timeline.destination} "
            f"(departs {format_minutes(timeline.departure_time)})"
        )
        with st.expander(label):
            st.write(f"Operator: {timeline.operator}")
            st.write(f"Direction: {timeline.direction}")
            st.write(f"Departure: {format_minutes(timeline.departure_time)}")
            st.write(f"Arrival: {format_minutes(timeline.arrival_time)}")

            if timeline.stops:
                stops_df = pd.DataFrame(
                    [
                        {
                            "station_id": stop.station_id,
                            "charger_id": stop.charger_id,
                            "arrive": format_minutes(stop.arrive),
                            "wait (min)": stop.wait,
                            "charge_start": format_minutes(stop.charge_start),
                            "charge_end": format_minutes(stop.charge_end),
                        }
                        for stop in timeline.stops
                    ]
                )
                st.dataframe(stops_df, use_container_width=True, hide_index=True)
            else:
                st.write("No charging stops — completes on a single charge.")


def render_station_schedules_tab(scenario: Scenario, result: ScheduleResult) -> None:
    """Render one occupancy section per station."""
    for station in scenario.stations:
        st.markdown(
            f"**Station {station.station_id}**  ({len(station.chargers)} charger(s))"
        )
        table = station_dataframe(result, station.station_id)
        if table.empty:
            st.write("No buses charged at this station.")
        else:
            st.dataframe(table, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Wire up the sidebar selector and the three result tabs."""
    st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")
    st.title("Bus Charging Scheduler")

    st.sidebar.header("Scenario")
    filename = st.sidebar.selectbox("Select a scenario", SCENARIO_FILES)

    try:
        scenario, result = run_scenario(filename)
    except ScenarioError as exc:
        st.error(f"Could not load {filename}: {exc}")
        return

    total_buses = len(result.simulation.buses)
    infeasible_count = len(result.infeasible_buses)
    feasible_count = total_buses - infeasible_count
    charging_events = sum(
        len(occupancy.slots) for occupancy in result.simulation.stations.values()
    )

    st.subheader("Results Summary")
    summary_cols = st.columns(4)
    summary_cols[0].metric("Total buses", total_buses)
    summary_cols[1].metric("Feasible buses", feasible_count)
    summary_cols[2].metric("Infeasible buses", infeasible_count)
    summary_cols[3].metric("Total charging events", charging_events)

    scenario_tab, bus_tab, station_tab = st.tabs(
        ["Scenario", "Bus Schedules", "Station Schedules"]
    )

    with scenario_tab:
        render_scenario_tab(scenario)
    with bus_tab:
        render_bus_schedules_tab(result)
    with station_tab:
        render_station_schedules_tab(scenario, result)


if __name__ == "__main__":
    main()
