from __future__ import annotations

import heapq
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from solvers.picking_path_ortools_backend import solve_picking_path_with_ortools

WAVE_ROUTE_ID = "WAVE_001"


def _require_columns(df: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = columns - set(df.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _add_edge(graph: dict[str, dict[str, float]], left: str, right: str, distance: float, directed: int) -> None:
    graph[left][right] = min(graph[left].get(right, float("inf")), float(distance))
    if int(directed) == 0:
        graph[right][left] = min(graph[right].get(left, float("inf")), float(distance))


def _shortest_path(graph: dict[str, dict[str, float]], start: str, end: str) -> tuple[float, list[str]]:
    queue: list[tuple[float, str]] = [(0.0, start)]
    dist = {start: 0.0}
    prev: dict[str, str] = {}
    while queue:
        current_dist, node = heapq.heappop(queue)
        if node == end:
            break
        if current_dist > dist.get(node, float("inf")):
            continue
        for nxt, weight in graph.get(node, {}).items():
            cand = current_dist + weight
            if cand < dist.get(nxt, float("inf")):
                dist[nxt] = cand
                prev[nxt] = node
                heapq.heappush(queue, (cand, nxt))
    if end not in dist:
        return float("inf"), []
    return dist[end], _reconstruct_path(prev, start, end)


def _all_shortest_paths(
    graph: dict[str, dict[str, float]], start: str
) -> tuple[dict[str, float], dict[str, str]]:
    queue: list[tuple[float, str]] = [(0.0, start)]
    dist: dict[str, float] = {start: 0.0}
    prev: dict[str, str] = {}
    while queue:
        current_dist, node = heapq.heappop(queue)
        if current_dist > dist.get(node, float("inf")):
            continue
        for nxt, weight in graph.get(node, {}).items():
            cand = current_dist + weight
            if cand < dist.get(nxt, float("inf")):
                dist[nxt] = cand
                prev[nxt] = node
                heapq.heappush(queue, (cand, nxt))
    return dist, prev


def _reconstruct_path(prev: dict[str, str], start: str, end: str) -> list[str]:
    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _nearest_station_node(stations: pd.DataFrame, station_id: str) -> str:
    match = stations[stations["station_id"].astype(str).eq(str(station_id))]
    if match.empty:
        raise ValueError(f"Station not found: {station_id}")
    return str(match.iloc[0]["node_id"])


def _build_graph(nodes: pd.DataFrame, network: pd.DataFrame, pick_points: pd.DataFrame) -> dict[str, dict[str, float]]:
    graph: dict[str, dict[str, float]] = defaultdict(dict)
    node_xy = {
        str(row.node_id): (float(row.x), float(row.y))
        for row in nodes.itertuples(index=False)
    }
    for row in network.itertuples(index=False):
        if int(getattr(row, "passable", 1)) != 1:
            continue
        _add_edge(graph, str(row.from_node), str(row.to_node), float(row.distance), int(row.directed))

    aisle_nodes_by_y: dict[float, list[tuple[float, str]]] = defaultdict(list)
    for node_id, (x, y) in node_xy.items():
        if node_id.startswith("N_A"):
            aisle_nodes_by_y[round(y, 3)].append((x, node_id))
    for y in aisle_nodes_by_y:
        aisle_nodes_by_y[y].sort()

    for row in pick_points.itertuples(index=False):
        node_id = str(row.pick_node_id)
        x = float(row.pick_x)
        y = round(float(row.pick_y), 3)
        candidates = aisle_nodes_by_y.get(y, [])
        if not candidates:
            continue
        left = None
        right = None
        for candidate_x, candidate_node in candidates:
            if candidate_x <= x:
                left = (candidate_x, candidate_node)
            if candidate_x >= x and right is None:
                right = (candidate_x, candidate_node)
        for candidate in {left, right}:
            if candidate is None:
                continue
            candidate_x, candidate_node = candidate
            _add_edge(graph, node_id, candidate_node, abs(x - candidate_x), 0)
    return graph


def _prepare_pick_tasks(order: pd.DataFrame, inventory: pd.DataFrame, pick_points: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory = inventory.copy().reset_index(drop=True)
    inventory["sku_id"] = inventory["sku_id"].astype(str)
    inventory["slot_id"] = inventory["slot_id"].astype(str)
    inventory["qty_boxes_in_slot"] = pd.to_numeric(inventory["qty_boxes_in_slot"], errors="coerce").fillna(0)
    if "slot_cost" in inventory.columns:
        inventory["slot_cost"] = pd.to_numeric(inventory["slot_cost"], errors="coerce").fillna(0)
    else:
        inventory["slot_cost"] = 0

    pick_points = pick_points.copy()
    pick_points["slot_id"] = pick_points["slot_id"].astype(str)
    point_by_slot = pick_points.set_index("slot_id").to_dict(orient="index")

    # 全局共享库存：按订单顺序扣减，同一箱子不会被两个订单同时拣走。
    inventory_sorted = inventory.sort_values(["slot_cost", "slot_id"])
    candidates_by_sku: dict[str, list[int]] = {}
    for inv_idx, sku in inventory_sorted["sku_id"].items():
        candidates_by_sku.setdefault(str(sku), []).append(int(inv_idx))
    available_by_row: dict[int, float] = {
        int(idx): float(qty) for idx, qty in inventory["qty_boxes_in_slot"].items()
    }
    slot_by_row: dict[int, str] = {
        int(idx): str(slot) for idx, slot in inventory["slot_id"].items()
    }

    tasks: list[dict[str, Any]] = []
    unfulfilled: list[dict[str, Any]] = []
    for line in order.itertuples(index=False):
        order_no = str(line.order_no)
        sku_code = str(line.sku_code)
        sku_name = str(getattr(line, "sku_name", ""))
        remaining = float(line.quantity)
        for inv_idx in candidates_by_sku.get(sku_code, []):
            if remaining <= 0:
                break
            available = available_by_row.get(inv_idx, 0.0)
            if available <= 0:
                continue
            slot_id = slot_by_row[inv_idx]
            point = point_by_slot.get(slot_id)
            if point is None:
                continue
            pick_qty = min(remaining, available)
            if pick_qty <= 0:
                continue
            tasks.append(
                {
                    "order_no": order_no,
                    "sku_code": sku_code,
                    "sku_name": sku_name,
                    "slot_id": slot_id,
                    "pick_qty": pick_qty,
                    "pick_node_id": str(point["pick_node_id"]),
                    "pick_x": float(point["pick_x"]),
                    "pick_y": float(point["pick_y"]),
                    "aisle_id": str(point["aisle_id"]),
                }
            )
            available_by_row[inv_idx] = available - pick_qty
            remaining -= pick_qty
        if remaining > 0:
            unfulfilled.append(
                {
                    "order_no": order_no,
                    "sku_code": sku_code,
                    "sku_name": sku_name,
                    "unfulfilled_qty": remaining,
                }
            )
    task_columns = [
        "order_no",
        "sku_code",
        "sku_name",
        "slot_id",
        "pick_qty",
        "pick_node_id",
        "pick_x",
        "pick_y",
        "aisle_id",
    ]
    unfulfilled_columns = ["order_no", "sku_code", "sku_name", "unfulfilled_qty"]
    return pd.DataFrame(tasks, columns=task_columns), pd.DataFrame(unfulfilled, columns=unfulfilled_columns)


def _prepare_pick_tasks_optimized(
    order: pd.DataFrame,
    inventory: pd.DataFrame,
    pick_points: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory = inventory.copy().reset_index(drop=True)
    inventory["sku_id"] = inventory["sku_id"].astype(str)
    inventory["slot_id"] = inventory["slot_id"].astype(str)
    if "batch_id" not in inventory.columns:
        inventory["batch_id"] = ""
    inventory["batch_id"] = inventory["batch_id"].astype(str)
    inventory["qty_boxes_in_slot"] = pd.to_numeric(inventory["qty_boxes_in_slot"], errors="coerce").fillna(0)
    if "slot_cost" in inventory.columns:
        inventory["slot_cost"] = pd.to_numeric(inventory["slot_cost"], errors="coerce").fillna(0)
    else:
        inventory["slot_cost"] = 0

    pick_points = pick_points.copy()
    pick_points["slot_id"] = pick_points["slot_id"].astype(str)
    point_by_slot = pick_points.set_index("slot_id").to_dict(orient="index")
    valid_inventory = inventory[inventory["qty_boxes_in_slot"].gt(0)].copy()
    valid_inventory = valid_inventory[valid_inventory["slot_id"].isin(point_by_slot)].copy()

    sku_name_col = "sku_name" if "sku_name" in order.columns else None
    group_cols = ["order_no", "sku_code"] + ([sku_name_col] if sku_name_col else [])
    order_lines = order.groupby(group_cols, dropna=False, as_index=False)["quantity"].sum()
    if sku_name_col is None:
        order_lines["sku_name"] = ""
    order_lines["quantity"] = pd.to_numeric(order_lines["quantity"], errors="coerce").fillna(0)
    order_lines = order_lines[order_lines["quantity"].gt(0)].reset_index(drop=True)

    task_columns = [
        "order_no",
        "sku_code",
        "sku_name",
        "slot_id",
        "pick_qty",
        "pick_node_id",
        "pick_x",
        "pick_y",
        "aisle_id",
    ]
    unfulfilled_columns = ["order_no", "sku_code", "sku_name", "unfulfilled_qty"]
    if order_lines.empty:
        return pd.DataFrame(columns=task_columns), pd.DataFrame(columns=unfulfilled_columns)

    tasks: list[dict[str, Any]] = []
    unfulfilled: list[dict[str, Any]] = []

    for sku_code, sku_lines in order_lines.groupby("sku_code", sort=True):
        remaining_by_line = {
            int(line_idx): float(line.quantity)
            for line_idx, line in sku_lines.iterrows()
        }
        sku_inventory = valid_inventory[valid_inventory["sku_id"].eq(str(sku_code))]
        fifo_columns = [
            col
            for col in ["inbound_time", "inbound_date", "入库时间", "batch_id"]
            if col in sku_inventory.columns
        ]
        # 约束1：FIFO batch constraint: 先入库批次需要先出库.
        # 目标：在满足FIFO的情况下，最小化 sum(slot_cost * z): 优先拣货低成本货位.
        sku_inventory = sku_inventory.sort_values(fifo_columns + ["slot_cost", "slot_id"])

        for inv_idx, inv in sku_inventory.iterrows():
            available = float(inv["qty_boxes_in_slot"])
            if available <= 0:
                continue
            point = point_by_slot.get(str(inv["slot_id"]))
            if point is None:
                continue

            for line_idx in sorted(remaining_by_line):
                if available <= 0:
                    break
                remaining = remaining_by_line[line_idx]
                if remaining <= 0:
                    continue

                pick_qty = min(remaining, available)
                if pick_qty <= 0:
                    continue
                # 约束2：非负约束: every allocation z(order, sku, slot) is strictly positive here.
                line = order_lines.loc[line_idx]
                tasks.append(
                    {
                        "order_no": str(line["order_no"]),
                        "sku_code": str(line["sku_code"]),
                        "sku_name": str(line.get("sku_name", "")),
                        "slot_id": str(inv["slot_id"]),
                        "pick_qty": pick_qty,
                        "pick_node_id": str(point["pick_node_id"]),
                        "pick_x": float(point["pick_x"]),
                        "pick_y": float(point["pick_y"]),
                        "aisle_id": str(point["aisle_id"]),
                    }
                )

                # Demand-balance constraint: allocated quantity plus remaining unmet quantity equals this order-SKU demand.
                remaining_by_line[line_idx] = remaining - pick_qty
                # Inventory-capacity constraint: total allocated quantity from this slot never exceeds its available stock.
                available -= pick_qty

        for line_idx, remaining in remaining_by_line.items():
            if remaining <= 0:
                continue
            line = order_lines.loc[line_idx]
            unfulfilled.append(
                {
                    "order_no": str(line["order_no"]),
                    "sku_code": str(line["sku_code"]),
                    "sku_name": str(line.get("sku_name", "")),
                    "unfulfilled_qty": remaining,
                }
            )

    return pd.DataFrame(tasks, columns=task_columns), pd.DataFrame(unfulfilled, columns=unfulfilled_columns)


def _stop_detail_rows(tasks: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """波次模式下，把每个拣货点按 (sku, order_no) 展开成明细行，供下游二次分拣使用。"""
    detail = (
        tasks.groupby(["pick_node_id", "slot_id", "sku_code", "sku_name", "order_no"], as_index=False)["pick_qty"]
        .sum()
        .sort_values(["pick_node_id", "sku_code", "order_no"])
    )
    by_node: dict[str, list[dict[str, Any]]] = {}
    for row in detail.itertuples(index=False):
        by_node.setdefault(str(row.pick_node_id), []).append(
            {
                "order_no": str(row.order_no),
                "sku_code": str(row.sku_code),
                "sku_name": str(row.sku_name),
                "pick_qty": float(row.pick_qty),
            }
        )
    return by_node


def _route_group(
    route_id: str,
    tasks: pd.DataFrame,
    graph: dict[str, dict[str, float]],
    start_node: str,
    end_node: str,
    visit_order: list[str] | None = None,
    expand_orders: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """为一条拣货路线（per-order 模式=一个订单，wave 模式=整个波次）排序拣货点并生成路径明细。

    expand_orders=True 时，同一停靠点按 (sku, order_no) 展开成多行，
    travel_distance 只记在该停靠点的第一行，避免整列求和重复计距。
    """
    grouped = (
        tasks.groupby(["pick_node_id", "slot_id", "pick_x", "pick_y", "aisle_id"], as_index=False)
        .agg(
            pick_qty=("pick_qty", "sum"),
            sku_code=("sku_code", lambda x: "|".join(sorted(set(map(str, x))))),
            sku_name=("sku_name", lambda x: "|".join(sorted(set(map(str, x))))),
        )
    )
    remaining = set(grouped["pick_node_id"].astype(str).tolist())
    if visit_order is not None and (set(visit_order) != remaining or len(visit_order) != len(remaining)):
        raise ValueError(f"Invalid picking node visit order for route {route_id}")
    node_rows = grouped.set_index("pick_node_id").to_dict(orient="index")
    detail_by_node = _stop_detail_rows(tasks) if expand_orders else {}
    default_order_no = route_id if not expand_orders else None
    route_rows: list[dict[str, Any]] = []
    current = start_node
    cumulative = 0.0
    sequence = 0
    route_rows.append(
        {
            "route_id": route_id,
            "order_no": default_order_no,
            "sequence": sequence,
            "action": "start",
            "node_id": start_node,
            "slot_id": None,
            "sku_code": None,
            "sku_name": None,
            "pick_qty": 0,
            "x": None,
            "y": None,
            "travel_distance": 0.0,
            "cumulative_distance": 0.0,
            "path_nodes": start_node,
        }
    )
    while remaining:
        if visit_order is None:
            # 单源全图 Dijkstra：一次拿到 current 到所有 reachable node 的距离，
            # 再在 remaining 里选最近的。把 O(N) 次单源单目标 Dijkstra 降成 1 次单源全图。
            dist_map, prev_map = _all_shortest_paths(graph, current)
            best_node: str | None = None
            best_distance = float("inf")
            for cand_node in sorted(remaining):
                cand_dist = dist_map.get(cand_node, float("inf"))
                if cand_dist < best_distance:
                    best_distance = cand_dist
                    best_node = cand_node
            if best_node is None or best_distance == float("inf"):
                raise ValueError(f"Cannot connect picking nodes for route {route_id}")
            node = best_node
            distance = best_distance
            path = _reconstruct_path(prev_map, current, node)
        else:
            node = visit_order[len(grouped) - len(remaining)]
            distance, path = _shortest_path(graph, current, node)
            if distance == float("inf"):
                raise ValueError(f"Cannot connect picking nodes for route {route_id}")
        cumulative += distance
        sequence += 1
        info = node_rows[node]
        stop_details = detail_by_node.get(node) if expand_orders else None
        if not stop_details:
            stop_details = [
                {
                    "order_no": default_order_no,
                    "sku_code": info["sku_code"],
                    "sku_name": info["sku_name"],
                    "pick_qty": info["pick_qty"],
                }
            ]
        for offset, detail in enumerate(stop_details):
            route_rows.append(
                {
                    "route_id": route_id,
                    "order_no": detail["order_no"],
                    "sequence": sequence,
                    "action": "pick",
                    "node_id": node,
                    "slot_id": info["slot_id"],
                    "sku_code": detail["sku_code"],
                    "sku_name": detail["sku_name"],
                    "pick_qty": detail["pick_qty"],
                    "x": info["pick_x"],
                    "y": info["pick_y"],
                    "travel_distance": round(distance, 3) if offset == 0 else 0.0,
                    "cumulative_distance": round(cumulative, 3),
                    "path_nodes": "->".join(path) if offset == 0 else "",
                }
            )
        current = node
        remaining.remove(node)
    distance, path = _shortest_path(graph, current, end_node)
    if distance == float("inf"):
        raise ValueError(f"Cannot connect end station for route {route_id}")
    cumulative += distance
    sequence += 1
    route_rows.append(
        {
            "route_id": route_id,
            "order_no": default_order_no,
            "sequence": sequence,
            "action": "end",
            "node_id": end_node,
            "slot_id": None,
            "sku_code": None,
            "sku_name": None,
            "pick_qty": 0,
            "x": None,
            "y": None,
            "travel_distance": round(distance, 3),
            "cumulative_distance": round(cumulative, 3),
            "path_nodes": "->".join(path),
        }
    )
    order_list = sorted(set(tasks["order_no"].astype(str)))
    summary = {
        "route_id": route_id,
        "order_no": "|".join(order_list) if expand_orders else route_id,
        "order_count": len(order_list),
        "pick_stop_count": int(len(grouped)),
        "picked_qty": float(grouped["pick_qty"].sum()) if not grouped.empty else 0.0,
        "total_distance": round(cumulative, 3),
    }
    return pd.DataFrame(route_rows), summary


def solve_picking_path(
    order_path: str,
    inventory_path: str,
    nodes_path: str,
    network_path: str,
    stations_path: str,
    pick_points_path: str,
    backend: str = "heuristic",
    mode: str = "per-order",
    start_station: str = "PICK_START",
    end_station: str = "OUTBOUND",
    time_limit_s: float = 60.0,
    num_search_workers: int = 8,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]:
    if backend not in {"heuristic", "ortools"}:
        raise ValueError("backend must be 'heuristic' or 'ortools'")
    if mode not in {"per-order", "wave"}:
        raise ValueError("mode must be 'per-order' or 'wave'")

    order = pd.read_csv(order_path)
    inventory = pd.read_csv(inventory_path)
    nodes = pd.read_csv(nodes_path)
    network = pd.read_csv(network_path)
    stations = pd.read_csv(stations_path)
    pick_points = pd.read_csv(pick_points_path)

    _require_columns(order, {"order_no", "sku_code", "quantity"}, "order.csv")
    _require_columns(inventory, {"sku_id", "slot_id", "qty_boxes_in_slot"}, "slotting_solution.csv")
    _require_columns(nodes, {"node_id", "x", "y"}, "picking_nodes.csv")
    _require_columns(network, {"from_node", "to_node", "distance", "directed", "passable"}, "picking_network.csv")
    _require_columns(stations, {"station_id", "node_id"}, "picking_stations.csv")
    _require_columns(pick_points, {"slot_id", "pick_node_id", "aisle_id", "pick_x", "pick_y"}, "picking_points.csv")

    order["order_no"] = order["order_no"].astype(str)
    order["sku_code"] = order["sku_code"].astype(str)
    order["quantity"] = pd.to_numeric(order["quantity"], errors="coerce").fillna(0)

    tasks, unfulfilled = _prepare_pick_tasks_optimized(order, inventory, pick_points)
    # route_id 决定一趟拣货走多少订单：per-order 一单一趟，wave 把整份订单表合并成一个波次。
    tasks["route_id"] = tasks["order_no"] if mode == "per-order" else WAVE_ROUTE_ID
    graph = _build_graph(nodes, network, pick_points)
    start_node = _nearest_station_node(stations, start_station)
    end_node = _nearest_station_node(stations, end_station)

    route_sequences: dict[str, list[str]] = {}
    if backend == "ortools":
        route_sequences, solver_meta = solve_picking_path_with_ortools(
            tasks=tasks,
            graph=graph,
            start_node=start_node,
            end_node=end_node,
            all_shortest_paths=_all_shortest_paths,
            time_limit_s=time_limit_s,
            num_search_workers=num_search_workers,
        )
    else:
        solver_meta = {"backend": "heuristic"}

    route_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for route_id, sub in tasks.groupby("route_id", sort=True):
        route, summary = _route_group(
            str(route_id),
            sub.copy(),
            graph,
            start_node,
            end_node,
            visit_order=route_sequences.get(str(route_id)) if backend == "ortools" else None,
            expand_orders=mode == "wave",
        )
        route_frames.append(route)
        summaries.append(summary)

    route_df = pd.concat(route_frames, ignore_index=True) if route_frames else pd.DataFrame()
    summary_df = pd.DataFrame(summaries)
    if summary_df.empty:
        pass
    elif unfulfilled.empty:
        summary_df["unfulfilled_qty"] = 0.0
    elif mode == "wave":
        summary_df["unfulfilled_qty"] = float(unfulfilled["unfulfilled_qty"].sum())
    else:
        unfulfilled_by_order = unfulfilled.groupby("order_no")["unfulfilled_qty"].sum()
        summary_df["unfulfilled_qty"] = summary_df["order_no"].map(unfulfilled_by_order).fillna(0.0)

    reports = {"order_summary": summary_df, "unfulfilled": unfulfilled}
    meta = {
        **solver_meta,
        "time_limit_s": time_limit_s,
        "num_search_workers": num_search_workers,
        "mode": mode,
        "order_count": int(order["order_no"].nunique()),
        "route_count": int(len(summary_df)),
        "pick_task_count": int(len(tasks)),
        "route_row_count": int(len(route_df)),
    }
    return route_df, reports, meta
