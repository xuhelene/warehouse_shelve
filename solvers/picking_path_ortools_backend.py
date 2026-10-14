from __future__ import annotations

from typing import Callable

import pandas as pd


def solve_picking_path_with_ortools(
    tasks: pd.DataFrame,
    graph: dict[str, dict[str, float]],
    start_node: str,
    end_node: str,
    all_shortest_paths: Callable[[dict[str, dict[str, float]], str], tuple[dict[str, float], dict[str, str]]],
    time_limit_s: float = 60.0,
    num_search_workers: int = 8,
) -> tuple[dict[str, list[str]], dict]:
    from ortools.sat.python import cp_model

    sequences: dict[str, list[str]] = {}
    objective_value = 0.0
    best_objective_bound = 0.0
    wall_time = 0.0
    statuses: list[int] = []
    distance_scale = 1000

    for route_id, sub in tasks.groupby("route_id", sort=True):
        pick_nodes = sorted(set(sub["pick_node_id"].astype(str)))
        if not pick_nodes:
            sequences[str(route_id)] = []
            continue

        nodes = [start_node, *pick_nodes, end_node]
        end_index = len(nodes) - 1
        # 每个节点一次单源全图 Dijkstra，取代按弧逐对求最短路：
        # 波次模式下停靠点可能上百个，O(N^2) 次 Dijkstra 会拖垮建模阶段。
        dist_from = {node: all_shortest_paths(graph, node)[0] for node in dict.fromkeys(nodes)}
        model = cp_model.CpModel()
        arcs: list[tuple[int, int, object]] = []
        arc_vars: dict[tuple[int, int], object] = {}
        arc_costs: dict[tuple[int, int], int] = {}

        return_arc = model.NewBoolVar("end_to_start")
        model.Add(return_arc == 1)
        arcs.append((end_index, 0, return_arc))

        for source in range(end_index):
            for target in range(1, end_index + 1):
                if source == target:
                    continue
                distance = dist_from[nodes[source]].get(nodes[target], float("inf"))
                if distance == float("inf"):
                    continue
                key = (source, target)
                arc_vars[key] = model.NewBoolVar(f"arc_{source}_{target}")
                arc_costs[key] = int(round(distance * distance_scale))
                arcs.append((source, target, arc_vars[key]))

        model.AddCircuit(arcs)
        model.Minimize(sum(arc_costs[key] * var for key, var in arc_vars.items()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit_s)
        solver.parameters.num_search_workers = int(num_search_workers)
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"No feasible picking route from OR-Tools for route {route_id}. status={status}")

        selected = {
            source: target
            for (source, target), var in arc_vars.items()
            if solver.Value(var) > 0
        }
        sequence: list[str] = []
        current = 0
        while current != end_index:
            if current not in selected:
                raise RuntimeError(f"Invalid OR-Tools picking route for route {route_id}")
            current = selected[current]
            if current != end_index:
                sequence.append(nodes[current])
        sequences[str(route_id)] = sequence

        statuses.append(int(status))
        objective_value += solver.ObjectiveValue() / distance_scale
        best_objective_bound += solver.BestObjectiveBound() / distance_scale
        wall_time += solver.WallTime()

    status_value = min(statuses) if statuses else None
    meta = {
        "backend": "ortools",
        "status": status_value,
        "objective_value": objective_value,
        "best_objective_bound": best_objective_bound,
        "wall_time": wall_time,
    }
    return sequences, meta
