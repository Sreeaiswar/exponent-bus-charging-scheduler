# Exponent Bus Charging Scheduler

A scheduling system for electric buses operating on a shared bidirectional route with limited charging infrastructure.

The scheduler generates feasible charging plans for each bus, selects plans using a congestion-aware strategy, and simulates charger usage using an event-driven simulation engine.

This project was built as part of the Exponent Energy Software Development Engineer assignment.

---

## Problem Statement

A fleet of electric buses operates between two endpoints on a fixed route.

Each bus:

- Has a finite battery range
- Must charge at intermediate stations
- Shares charging infrastructure with other buses
- Can only occupy one charger at a time

The scheduler must:

1. Determine where each bus should charge.
2. Ensure no bus exceeds battery range constraints.
3. Resolve charger contention.
4. Produce a complete charging timeline for the fleet.
5. Generate station occupancy schedules.

---

## Features

- Scenario-driven scheduling
- Bidirectional route support
- Automatic charging plan generation
- Congestion-aware plan selection
- Event-driven charger simulation
- Multi-charger station support
- Deterministic scheduling
- Extensible architecture
- Data-driven route and station configuration
- End-to-end validation using multiple scenarios

---

## Architecture Overview

The scheduling pipeline consists of:

```text
Scenario JSON
      │
      ▼
Loader
      │
      ▼
Scenario
      │
      ▼
Plan Generator
      │
      ▼
Scheduler Engine
      │
      ▼
Simulator
      │
      ▼
Streamlit UI
```

### Module Responsibilities

| Module | Responsibility |
|----------|---------------|
| `scheduler/loader.py` | Parse and validate scenario files |
| `scheduler/route_utils.py` | Shared route geometry calculations |
| `scheduler/plan_generator.py` | Generate all valid charging plans |
| `scheduler/engine.py` | Select charging plans and orchestrate scheduling |
| `scheduler/simulator.py` | Execute event-driven charging simulation |
| `models/` | Domain models and data structures |

For a detailed explanation of design decisions and algorithms, see `ARCHITECTURE.md`.

---

## Project Structure

```text
exponent-bus-charging-scheduler/
│
├── models/
│   ├── bus.py
│   ├── route.py
│   ├── scenario.py
│   └── station.py
│
├── scheduler/
│   ├── loader.py
│   ├── route_utils.py
│   ├── plan_generator.py
│   ├── engine.py
│   └── simulator.py
│
├── scenarios/
│   ├── scenario1.json
│   ├── scenario2.json
│   ├── scenario3.json
│   ├── scenario4.json
│   └── scenario5.json
│
├── ARCHITECTURE.md
├── README.md
├── requirements.txt
└── app.py
```

---

## Scenario Descriptions

### Scenario 1 – Even Spacing

Baseline scenario with evenly spaced departures and moderate charger utilization.

### Scenario 2 – Bunched Departures

Multiple buses depart around the same time, increasing charger contention.

### Scenario 3 – Asymmetric Load

Uneven directional traffic creates different charging demands across stations.

### Scenario 4 – Operator-Heavy Fleet

Fleet composition emphasizes a particular operator to test fairness-related extensions.

### Scenario 5 – Worst-Case Convergence

Large numbers of buses converge on the same charging stations, creating significant contention.

---

## Setup

### Create Virtual Environment

```bash
python -m venv .venv
```

### Activate Virtual Environment

#### Linux / macOS

```bash
source .venv/bin/activate
```

#### Windows

```bash
.venv\Scripts\activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Running a Scenario

Example usage:

```python
from scheduler.loader import load_scenario
from scheduler.engine import Engine

scenario = load_scenario("scenarios/scenario1.json")

engine = Engine(scenario)
result = engine.run()

print(result.chosen_plans)
print(result.infeasible_buses)
```

The generated `ScheduleResult` contains:

- Selected charging plans
- Per-bus timelines
- Station occupancy schedules
- Infeasible bus information
- Scenario metadata and weights

---

## Launch UI

A Streamlit interface is provided for visualizing scheduling results. Launch it with:

```bash
streamlit run app.py
```

Select a scenario from the sidebar to view its inputs, per-bus schedules, and per-station charger occupancy.

---

## Live Demo

https://bus-charging-scheduler-ekva7u9sguk4cxapppzqz7j.streamlit.app

---

## Key Design Decisions

### Centralized Route Geometry

Route distance calculations are implemented in `route_utils.py` and shared across all modules to ensure consistency.

### DFS-Based Plan Generation

Valid charging plans are generated using depth-first search with range-based pruning.

### Greedy Congestion-Aware Selection

The scheduler chooses plans using:

1. Fewest charging stops
2. Lowest estimated congestion
3. Deterministic tie-breaking

### Event-Driven Simulation

Only meaningful events are processed:

- Departure
- Station Arrival
- Charge Start
- Charge End
- Destination Arrival

This avoids unnecessary minute-by-minute simulation.

### FCFS Charger Allocation

Charging stations currently use First-Come-First-Served queueing, with clear extension points for future weighted prioritization.

---

## Assumptions

- Battery range is fixed per scenario.
- Charging always fills the battery to 100%.
- Charging duration is constant.
- Buses start fully charged.
- Travel speed is constant at 60 km/h.
- Endpoints do not contain charging stations.
- Routes are linear with no branching paths.
- All scenario information is known before scheduling begins.

---

## Testing

The scheduler has been validated against all five provided scenarios.

Verification includes:

- Scenario loading and validation
- Charging plan generation
- Plan selection
- Event-driven simulation
- Charger contention handling
- Arrival of all feasible buses
- Deterministic execution across repeated runs

---

## Current Limitations

- Soft-rule weights are not yet incorporated into scheduling decisions.
- Plan selection uses a greedy heuristic rather than global optimization.
- Congestion estimation is performed before simulation and does not account for cascading delays.
- Charging priority is fixed to FCFS.
- Travel speed is constant.
- Partial charging is not supported.
- The current Streamlit UI focuses on visualization and does not support interactive scenario editing.

---

## Future Improvements

- Weighted plan selection using scenario weights
- Priority-based charger allocation
- Dynamic speed and traffic modelling
- Partial charging support
- Real-time schedule adjustments
- Interactive scenario editing
- Automated unit and integration tests

---

## Design Documentation

A detailed engineering design document is available in:

```text
ARCHITECTURE.md
```

This document explains:

- System architecture
- Domain model
- Charging plan generation
- Scheduling strategy
- Event-driven simulation
- Complexity analysis
- Scalability considerations
- Testing approach
- Current limitations
