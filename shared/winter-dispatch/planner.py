"""Deterministic baseline for version 1 winter-maintenance scenarios."""

import argparse
import heapq
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Arc:
    id: str
    start: str
    end: str
    travel: int


@dataclass(frozen=True)
class Job:
    id: str
    arc_id: str
    service_min: int
    release_min: int
    due_min: int
    priority: int
    requires: frozenset[str]


@dataclass
class Motion:
    arc: Arc
    finish: int
    service: bool


@dataclass
class Vehicle:
    node: str
    capabilities: frozenset[str]
    available: bool = True
    job: Job | None = None
    motion: Motion | None = None
    approach: list[str] = field(default_factory=list)
    deadhead_min: int = 0


def shortest_path(graph, start, goal, closed):
    queue = [(0, (), start)]
    seen = set()
    while queue:
        minutes, path, node = heapq.heappop(queue)
        if node in seen:
            continue
        if node == goal:
            return minutes, path
        seen.add(node)
        for arc in graph[node]:
            if arc.id not in closed and arc.end not in seen:
                heapq.heappush(queue, (minutes + arc.travel, (*path, arc.id), arc.end))
    return None


def plan_shift(scenario, variant_id="normal"):
    if scenario["schema_version"] != 1:
        raise ValueError("unsupported scenario version")
    variant = next((item for item in scenario["variants"] if item["id"] == variant_id), None)
    if variant is None:
        raise ValueError(f"unknown variant: {variant_id}")

    end = scenario["duration_min"]
    if type(end) is not int or end < 0:
        raise ValueError("duration_min must be a nonnegative integer")
    graph = defaultdict(list)
    arcs = {}
    for item in scenario["arcs"]:
        arc = Arc(item["id"], item["from"], item["to"], item["travel_min"])
        if type(arc.travel) is not int or arc.travel <= 0:
            raise ValueError(f"travel_min must be a positive integer: {arc.id}")
        arcs[arc.id] = arc
        graph[arc.start].append(arc)

    vehicles = {
        item["id"]: Vehicle(item["start_node"], frozenset(item["capabilities"]))
        for item in sorted(scenario["vehicles"], key=lambda item: item["id"])
    }
    jobs = sorted(
        (Job(**{**item, "requires": frozenset(item["requires"])}) for item in scenario["jobs"]),
        key=lambda job: (job.priority, job.due_min, job.id),
    )
    for job in jobs:
        if type(job.service_min) is not int or job.service_min <= 0:
            raise ValueError(f"service_min must be a positive integer: {job.id}")
    external = defaultdict(list)
    for event in variant["events"]:
        if event["kind"] not in {"arc_closed", "vehicle_unavailable"}:
            raise ValueError(f"unknown event kind: {event['kind']}")
        external[event["at_min"]].append(event)

    closed = set()
    completed = {}
    events = []
    now = 0

    def record(kind, **details):
        events.append({"at_min": now, "kind": kind, **details})

    def approach_to(vehicle, job):
        if job.arc_id in closed:
            return None
        return shortest_path(graph, vehicle.node, arcs[job.arc_id].start, closed)

    while True:
        for vehicle_id, vehicle in vehicles.items():
            motion = vehicle.motion
            if motion is None or motion.finish != now:
                continue
            vehicle.node = motion.arc.end
            vehicle.motion = None
            if motion.service:
                job = vehicle.job
                result = {
                    "job_id": job.id, "vehicle_id": vehicle_id,
                    "finished_min": now, "on_time": now <= job.due_min,
                    "approach_arcs": vehicle.approach,
                }
                completed[job.id] = result
                vehicle.job = None
                record("job_completed", job_id=job.id, vehicle_id=vehicle_id)

        for event in external[now]:
            events.append(dict(event))
            if event["kind"] == "arc_closed":
                closed.add(event["arc_id"])
            else:
                vehicles[event["vehicle_id"]].available = False

        for vehicle_id, vehicle in vehicles.items():
            if vehicle.job is None or vehicle.motion is not None:
                continue
            route = approach_to(vehicle, vehicle.job)
            reason = None
            if not vehicle.available:
                reason = "vehicle_unavailable"
            elif route is None:
                reason = "route_unavailable"
            elif now + route[0] + vehicle.job.service_min > end:
                reason = "insufficient_shift_time"
            if reason:
                record("assignment_released", vehicle_id=vehicle_id, job_id=vehicle.job.id, reason=reason)
                vehicle.job = None

        assigned = {vehicle.job.id for vehicle in vehicles.values() if vehicle.job is not None}
        for job in jobs:
            if job.release_min > now or job.id in completed or job.id in assigned:
                continue
            candidates = []
            for vehicle_id, vehicle in vehicles.items():
                if not vehicle.available or vehicle.job is not None or not job.requires <= vehicle.capabilities:
                    continue
                route = approach_to(vehicle, job)
                if route is not None and now + route[0] + job.service_min <= end:
                    candidates.append((route[0], vehicle_id))
            if candidates:
                _, vehicle_id = min(candidates)
                vehicle = vehicles[vehicle_id]
                vehicle.job = job
                vehicle.approach = []
                record("job_assigned", job_id=job.id, vehicle_id=vehicle_id)

        for vehicle_id, vehicle in vehicles.items():
            if vehicle.job is None or vehicle.motion is not None:
                continue
            job = vehicle.job
            _, path = approach_to(vehicle, job)
            if path:
                arc = arcs[path[0]]
                vehicle.motion = Motion(arc, now + arc.travel, service=False)
                vehicle.approach.append(arc.id)
                vehicle.deadhead_min += arc.travel
                record("deadhead_started", vehicle_id=vehicle_id, job_id=job.id, arc_id=arc.id, end_min=vehicle.motion.finish)
            else:
                vehicle.motion = Motion(arcs[job.arc_id], now + job.service_min, service=True)
                record("service_started", vehicle_id=vehicle_id, job_id=job.id, arc_id=job.arc_id, end_min=vehicle.motion.finish)

        if now == end:
            break
        future = [time for time in external if now < time <= end]
        future.extend(job.release_min for job in jobs if now < job.release_min <= end)
        future.extend(vehicle.motion.finish for vehicle in vehicles.values() if vehicle.motion is not None)
        now = min([end, *future])

    unassigned = []
    for job in jobs:
        if job.id in completed:
            continue
        eligible = [vehicle for vehicle in vehicles.values() if vehicle.available and job.requires <= vehicle.capabilities]
        if job.release_min > end:
            reason = "not_released"
        elif job.arc_id in closed:
            reason = "work_arc_closed"
        elif not eligible:
            reason = "no_available_eligible_vehicle"
        elif all(approach_to(vehicle, job) is None for vehicle in eligible):
            reason = "no_route_to_work"
        else:
            reason = "insufficient_shift_time"
        unassigned.append({"job_id": job.id, "reason": reason})

    released = [job for job in jobs if job.release_min <= end]
    on_time_jobs = sum(result["on_time"] for result in completed.values())
    return {
        "city_profile_id": scenario["city_profile_id"], "data_origin": scenario["data_origin"],
        "variant": variant_id, "assignments": list(completed.values()),
        "unassigned": unassigned, "events": events,
        "metrics": {
            "completed_jobs": len(completed), "released_jobs": len(released),
            "on_time_share": on_time_jobs / len(released) if released else None,
            "critical_completion_min": {
                job.id: completed[job.id]["finished_min"] if job.id in completed else None
                for job in released if job.priority == 1
            },
            "deadhead_min": sum(vehicle.deadhead_min for vehicle in vehicles.values()),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--variant", default="normal")
    args = parser.parse_args()
    try:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
        result = plan_shift(scenario, args.variant)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
