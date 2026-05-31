# Exponent Bus Charging Scheduler — Architecture

**Status:** Implemented (core scheduling pipeline)
**Last updated:** 2026-05-30

This document describes the design of the bus charging scheduler: the problem it
solves, the assumptions it rests on, and how the implemented modules
(`loader`, `route_utils`, `plan_generator`, `engine`, `simulator`) fit together.
It describes only what the code actually does today; sections on future work are
explicitly marked as not-yet-implemented.

---

## 1. Problem Statement

A fleet of electric buses runs along a fixed route between two endpoints
(in the reference scenarios, Bengaluru ↔ Kochi). Buses depart from either end at
scheduled times and travel in a single direction (`forward` = first stop → last
stop, `backward` = last stop → first stop).

Each bus has a finite **battery range** and cannot complete the trip on a single
charge. A number of **charging stations** sit at intermediate stops along the
route. Each station has one or more **chargers**, and charging is always to full
and takes a fixed amount of time. A charger can serve only one bus at a time, so
when several buses converge on the same station they must queue.

The scheduler must, for every bus:

1. **Decide where it charges** — pick a set of intermediate stations such that the
   bus never runs out of charge between consecutive full charges (including the
   origin → first charge and last charge → destination legs).
2. **Produce a concrete timeline** — given those decisions for the whole fleet,
   compute when each bus arrives at each station, how long it waits for a charger,
   when it charges, and when it ultimately reaches its destination.

The scenario also declares tunable **soft-rule weights** (`individual`,
`operator`, `overall`) intended to express preferences such as minimising any one
bus's delay, balancing across operators, and minimising total system delay. These
weights are carried through the pipeline but are **not yet applied** to any
decision (see §6 and §11).

---

## 2. Assumptions

These assumptions are baked into the current implementation:

- **Constant travel speed.** Travel time is derived purely from distance at a
  constant speed (`DEFAULT_SPEED_KMPH = 60`, i.e. 1 km ≈ 1 minute). There is no
  acceleration, traffic, or per-segment speed variation.
- **Integer-minute time model.** All times are integer minutes since midnight.
  Travel times are rounded to whole minutes (`round(distance * 60 / speed)`).
- **Charging is always to full and takes a fixed `charging_time`.** There is no
  partial charging and no dependence of charge time on remaining battery.
- **Buses leave their origin fully charged** and the **endpoints are never charge
  stops** — only intermediate stops can host charging.
- **A charger serves one bus at a time**, with no setup/teardown time between
  buses beyond the fixed charge duration.
- **Direction is fixed per bus.** A bus travels strictly in one direction with no
  backtracking; stations are visited in route order.
- **Static, fully-known input.** The entire fleet, route, and station capacity are
  known up front from a single scenario file; nothing arrives dynamically.
- **Distances are positive and the route is linear** (an ordered list of stops
  joined by segments). There are no branches or alternate paths.

---

## 3. System Architecture

The system is a straight pipeline. Each stage has a single responsibility and
hands a well-defined structure to the next:

```
 scenario.json
      │
      ▼
 ┌──────────┐   validated      ┌──────────────┐
 │  loader  │ ───────────────► │  Scenario    │  (domain models)
 └──────────┘   domain objects └──────────────┘
                                      │
                                      ▼
                              ┌─────────────────┐
                              │     engine      │  orchestrator
                              └─────────────────┘
                                  │        │
                  plan generation │        │ plan selection
                                  ▼        │
                        ┌──────────────────┐│
                        │  plan_generator  ││  all valid plans per bus
                        └──────────────────┘│
                                  │         ▼
                                  │   one chosen plan per bus
                                  ▼
                            ┌───────────┐
                            │ simulator │  event-driven execution
                            └───────────┘
                                  │
                                  ▼
                          ScheduleResult
                  (per-bus timelines + per-station occupancy)

         route_utils.travel_offsets()  ← shared by generator, engine, simulator
```

**Module responsibilities**

| Module | Responsibility |
| --- | --- |
| `scheduler/loader.py` | The single trust boundary. Parses and validates the scenario JSON, converts human-friendly values into domain types, and fails loudly with `ScenarioError`. |
| `scheduler/route_utils.py` | The single source of truth for route geometry: converts a `Route` + `Direction` into per-stop distance-from-origin offsets. |
| `scheduler/plan_generator.py` | Enumerates every *valid* charging plan for a bus, given route, range, and direction. Knows nothing about chargers or queues. |
| `scheduler/engine.py` | Orchestrates the pipeline and owns the only new logic — **plan selection** (one plan per bus). |
| `scheduler/simulator.py` | Executes the chosen plans as a discrete-event simulation, modelling charger contention and producing concrete timelines. |
| `models/` | Plain dataclasses for the domain (`Scenario`, `Route`, `RouteSegment`, `Direction`, `Bus`, `Station`, `Charger`). |

A key design property is that route geometry is centralised in
`route_utils.travel_offsets`. The plan generator (to validate range), the engine
(to estimate congestion), and the simulator (to compute travel times) all read
distances from this one function, so they can never disagree.

**Presentation layer.** `app.py` is implemented as a Streamlit application that
sits on top of the pipeline above. It is a thin presentation layer: it loads a
chosen scenario with the project's `loader`, runs the `Engine`, and renders the
resulting `ScheduleResult` — it contains no scheduling logic of its own and only
reads and formats backend objects. The UI exposes three views:

- **Scenario View** — the scenario input: battery range, charging time, soft-rule
  weights, the route and its segments, the stations and their chargers, and the
  fleet of buses.
- **Bus Schedule View** — a per-bus summary table (departure, arrival, journey
  and waiting time, chosen charging stations, feasibility) plus an expandable
  detailed timeline for each bus.
- **Station Schedule View** — per-station charger occupancy, showing which bus
  used which charger over which window, in the order buses actually charged.

`requirements.txt` already declares `streamlit` and `pandas`, the dependencies
this layer needs.

---

## 4. Domain Model

All domain types are plain `@dataclass` objects in `models/`. They carry only
facts from the input — computed results live in scheduler output structures.

- **`Direction`** (`Enum`): `FORWARD` / `BACKWARD`, expressed relative to the
  route's stop order rather than naming concrete endpoints, so a new route needs
  no enum change.
- **`RouteSegment`**: `source`, `destination`, `distance` (km) — one leg between
  adjacent stops.
- **`Route`**: ordered `stops` (including both endpoints) and the `segments`
  joining them.
- **`Charger`**: a single charger with a `charger_id` unique within its station
  (e.g. `"A-1"`).
- **`Station`**: a `station_id` (matching a stop name) and a list of `Charger`s.
  Capacity is data-driven — adding chargers is a data-only change.
- **`Bus`**: `bus_id`, `operator`, `direction`, and `departure_time` (minutes
  since midnight).
- **`Scenario`**: the complete, self-describing input — `route`, `stations`,
  `buses`, `battery_range`, `charging_time`, and the soft-rule `weights` dict.
  `weights` is a plain dict so new weight keys need no schema change.

**Scheduler output structures** (in `simulator.py` / `engine.py`):

- **`ChargeStop`**: one charging stop on a bus's timeline — `station_id`,
  `charger_id`, `arrive`, `wait`, `charge_start`, `charge_end`.
- **`BusTimeline`**: a bus's full computed journey — identity, origin/destination,
  departure, ordered `stops`, and final `arrival_time`.
- **`ChargerSlot` / `StationOccupancy`**: per-station usage log — which bus
  occupied which charger over which `[start, end]` window, ordered by start time.
- **`SimulationResult`**: `buses` (timelines keyed by id) + `stations` (occupancy
  keyed by id).
- **`ScheduleResult`**: the engine's top-level output — `scenario_name`,
  `weights` (carried through), `chosen_plans`, the `simulation`, and
  `infeasible_buses`.

---

## 5. Charging Plan Generation

`plan_generator.generate_charging_plans(route, battery_range, direction)` returns
**every valid charging plan** for a single bus. A plan is an ordered list of
intermediate station names where the bus charges to full.

**Geometry.** It first calls `route_utils.travel_offsets` to get each stop's
distance from the origin in travel order, then drops the two endpoints to leave
the chargeable intermediate stations as `(name, distance_from_origin)` pairs and
the total trip length.

**Validity.** A plan is valid when:

- the bus never travels more than `battery_range` between consecutive full charges,
  including origin → first charge and last charge → destination, and
- stations are visited in route order with no backtracking.

**Search.** `_extend_plan` performs a depth-first search with range pruning. At
each node the bus "sits" at its last charge distance:

- If the destination is within `battery_range` of the current position, the
  current plan is complete and recorded (the empty plan `[]` is valid when the
  whole trip fits in one charge).
- It then tries extending the plan with each *later* station that is within range
  (`0 < leg ≤ battery_range`), recursing and backtracking. The `next_index`
  argument enforces route order and prevents revisiting earlier stations.

Results are sorted by `(len(plan), plan)` — fewest charges first, then
lexicographically — so output is deterministic. An empty result list means the
route cannot be completed within range given the available stations (the bus is
infeasible).

The generator is fully data-driven: route length, distances, station set, and
direction all come from the inputs, so no part of it is specialised to the
reference Bengaluru–Kochi route.

---

## 6. Plan Selection Strategy

Selection is the only scheduling *decision* the engine makes, and it is
deliberately simple and isolated in `Engine._select_plan` — the single seam a
future weighted strategy can plug into.

**Per-bus ordering.** `Engine._select_all_plans` processes buses in
`(departure_time, bus_id)` order. It maintains a running **demand profile**: for
each station, the list of predicted (free-flow) arrival minutes of buses already
committed. After a bus picks its plan, its own predicted arrivals are added to
the profile, so later buses see the load already committed and steer toward
emptier stations — a greedy load-spreading heuristic.

**Selection key.** For a given bus, among all valid plans the engine picks the
minimum of:

1. **Fewest charging stops** — each charge costs a fixed `charging_time` plus
   potential contention, so fewer is better.
2. **Lowest estimated congestion** (tie-break) — see below.
3. **Lexicographic station order** (final deterministic tie-break).

**Congestion estimate.** `_estimate_congestion` is a cheap, free-flow guess (no
simulation). For each station in the plan it predicts the bus's arrival minute
(`departure_time + round(offset_km * 60 / speed)`), counts how many
already-committed buses are predicted to arrive within a **contention window** of
that arrival, and divides by the station's charger count (more chargers → less
congestion). The contention window is `scenario.charging_time` — the period over
which buses realistically compete for the same charger.

This keeps selection fast: congestion is only *estimated* up front, while the
simulator remains the source of truth for actual arrival and wait times.

**Why a greedy approach?** A globally optimal fleet-wide schedule would require
evaluating combinations of charging plans across all buses simultaneously — a
search that grows combinatorially with the fleet size and the number of valid
plans per bus. For this assignment, a greedy congestion-aware strategy provides
deterministic behaviour, good load distribution, and significantly lower
complexity, while keeping the design extensible: the `_select_plan` seam can be
upgraded to a weighted or optimising strategy later without disturbing the rest
of the pipeline (see §9).

> **Not yet implemented.** Weighted plan scoring (using the `individual` /
> `operator` / `overall` weights) is intentionally absent. The weights are
> carried through to `ScheduleResult` for display but do not influence selection.
> `Engine._select_plan` is the documented seam where a weighted strategy will
> later attach without touching the rest of the pipeline.

---

## 7. Event-Driven Simulation

`simulator.Simulator` executes the chosen plans as a discrete-event simulation
and produces the concrete fleet timeline. It is pure mechanics — it makes no
planning or prioritisation *decisions*.

**Why event-driven instead of minute-by-minute?** Only a small set of moments
actually change system state: departure, station arrival, charge start, charge
end, and destination arrival. Processing only those events avoids the wasted work
of stepping through every idle minute and keeps runtime proportional to actual
activity (`O(B · K)` events) rather than to elapsed wall-clock time.

**Event queue.** A binary heap (`heapq`) holds events ordered by
`(time, sequence, event)`. The monotonic `sequence` counter is a stable
tie-breaker so events at the same minute are processed in insertion order,
making runs fully deterministic.

**Event types and handlers:**

| Event | Handler behaviour |
| --- | --- |
| `DEPART` | Bus leaves its origin (full). Schedules arrival at the first charge stop, or directly at the destination if the plan is empty. |
| `ARRIVE_STATION` | Records arrival. If a charger is free, grabs it (lowest id first) and grants it; otherwise the bus joins the station's FCFS waiting queue. |
| `CHARGE_START` | Marks the charge window, records any wait (`grant time − arrive`), and schedules `CHARGE_END` at `time + charging_time`. |
| `CHARGE_END` | Logs the occupancy block, advances the bus toward its next stop (or destination), then hands the just-freed charger to the next waiting bus, if any — otherwise returns it to the free pool. |
| `ARRIVE_DESTINATION` | Records the bus's final `arrival_time`. |

**Resource model.** Each `_StationState` tracks `free_chargers` (idle charger
ids, kept sorted) and a `waiting` list of bus ids. Granting pops the
lowest-numbered free charger; freeing either serves the head of the queue or
returns the charger to the sorted free pool.

**Queue discipline.** `_next_waiting` pops the front of the waiting list —
**first-come-first-served**. This static method is explicitly documented as the
single seam where weighted who-charges-first prioritisation will later plug in:
only the choice of which waiting bus to serve would change; nothing else in the
simulator would.

**Travel time.** `_travel` converts a forward distance into whole minutes using
the same constant speed as the engine, so estimate and simulation share one time
model.

**Output.** `_build_result` returns per-bus `BusTimeline`s and per-station
`StationOccupancy` logs, with each station's occupancy slots sorted by
`(start, charger_id)` — i.e. the order buses actually charged there.

---

## 8. Complexity Analysis

Let:

- `S` = number of intermediate (chargeable) stations,
- `B` = number of buses,
- `P` = number of valid plans for a bus,
- `K` = maximum charges in any plan,
- `E` = total simulation events.

**Route geometry** — `travel_offsets` is `O(S)` per call; results are cached per
direction in the engine.

**Plan generation** — the DFS explores valid plans only, pruned by range. In the
worst case (large range relative to segment lengths) the number of valid plans is
exponential in `S` — up to `O(2^S)` subsets — and each is `O(K)` to record, so
generation is `O(P · K)`. In practice the range constraint prunes this heavily.
The final sort is `O(P log P)`.

**Plan selection** — for each bus, scoring all its plans costs
`O(P · K · W)` where `W` is the window-overlap check against the committed demand
at each station (bounded by the number of committed arrivals there). Across the
fleet this is `O(B · P · K · W)`. Buses are sorted once, `O(B log B)`.

**Simulation** — each bus generates a constant number of events per charge plus
endpoints, so `E = O(B · K)`. Each event does `O(log E)` heap work, giving
`O(B · K · log(B · K))` overall. The `_current_stop_index` lookup is `O(K)` per
served wait, which is negligible for realistic plan lengths.

For the reference scenarios (a handful of stations, tens of buses) every stage
runs effectively instantly; the only theoretically exponential term is plan
generation in `S`, which the range constraint keeps small.

---

## 9. Scalability and Future Extensions

The architecture was built so the most likely changes are local:

- **Weighted plan scoring** plugs into `Engine._select_plan` — replace the
  `(stops, congestion, order)` key with a weighted objective over the
  `individual` / `operator` / `overall` weights already carried on the scenario.
- **Weighted charging priority** plugs into `Simulator._next_waiting` — replace
  the FCFS front-pop with a weighted selection over the waiting buses. No other
  simulator code changes.
- **Bigger / different routes** are data-only: stops, distances, and directions
  all flow from the scenario, and `route_utils` keeps geometry consistent across
  modules. There is no hardcoding to Bengaluru–Kochi.
- **More chargers / more stations** are data-only: a station owns a list of
  chargers, expanded from a count by the loader.
- **New soft-rule weights** require no schema change — `weights` is an open dict;
  the loader validates the required keys but preserves extras.
- **The UI layer** sits on top of `ScheduleResult` without touching the pipeline
  — the implemented Streamlit front-end (`app.py`) only reads the engine's
  output, so it can grow (new views, richer charts) without any change to the
  scheduling code.
- **In-memory scenarios** are supported today via `load_scenario_dict`, so tests
  and a future UI can build scenarios without touching disk.

---

## 10. Testing Strategy

The implementation was validated end-to-end against all five provided scenarios
(`scenarios/scenario1.json` … `scenario5.json`), which span even spacing, a
bunched start, asymmetric load, an operator-heavy fleet, and a worst-case
convergence case.

Verification covered:

- Successful scenario loading and validation through the loader.
- Generation of valid charging plans for every bus and direction.
- Deterministic plan selection.
- Successful completion of the simulation (the event queue fully drains).
- Arrival of all feasible buses at their destinations.
- Correct handling of charger contention (buses queue FCFS and waits are recorded).
- Consistent results across repeated runs (identical chosen plans and arrival
  times on re-execution).

Running each scenario serves as an end-to-end validation of the complete
scheduling pipeline — **Loader → Plan Generator → Engine → Simulator** — exercising
every module on realistic, varied input rather than testing them only in
isolation.

Future work could complement this integration-level coverage with automated unit
tests for individual modules (loader validation, plan generation, and the
simulator's contention handling), giving finer-grained regression safety
alongside the current end-to-end scenario validation.

---

## 11. Current Limitations

- **Soft-rule weights are not yet applied.** `individual` / `operator` /
  `overall` are currently loaded, validated, and surfaced in `ScheduleResult`,
  but are not yet incorporated into scheduling decisions. Both plan selection and
  charging priority remain independent of operator fairness and per-bus/overall
  delay objectives today.
- **Selection is greedy, not globally optimal.** Buses are planned one at a time
  in departure order against a running demand estimate. There is no global
  optimisation or backtracking across buses, so the chosen set of plans can be
  suboptimal for the fleet as a whole.
- **Congestion is estimated free-flow.** The selection-time congestion score
  assumes predicted (un-delayed) arrivals and ignores the cascading effect of
  waits. The simulator captures real waits, but its results do not feed back into
  selection.
- **Charging discipline is fixed FCFS.** Whoever arrives first charges first;
  there is no preemption, reservation, or priority.
- **Static, single-direction, linear model.** No dynamic arrivals, no in-trip
  re-planning, no partial charging, no branching routes, and a single constant
  travel speed with no traffic model.
- **Visualization-only UI.** The current Streamlit UI (`app.py`) focuses on
  visualization and does not support interactive scenario editing or real-time
  interaction; it renders the result of running a pre-defined scenario through
  the pipeline.
- **Infeasible buses are reported, not resolved.** A bus with no valid plan is
  recorded in `infeasible_buses` with an empty plan; the scheduler does not
  attempt any mitigation (e.g. suggesting additional stations).
