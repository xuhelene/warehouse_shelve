from __future__ import annotations

import math
from typing import Dict, Tuple
import pandas as pd


def solve_with_ortools(
    slots: pd.DataFrame,
    items: pd.DataFrame,
    cand: pd.DataFrame,
    time_limit_s: float = 60.0,
    alpha_slots: float = 0.01,
    use_demand_if_present: bool = True,
    num_search_workers: int = 8,
    alpha_proximity: float = 0.0,
    proximity_max_distance: float = 0.0,
    alpha_proximity_excess: float = 50.0,
    mip_gap: float = 0.005,
    proximity_geometry=None,
) -> Tuple[pd.DataFrame, Dict]:
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    demand_available = use_demand_if_present and ("monthly_outbound_boxes" in items.columns)

    qty_map = items.set_index("item_id")["qty_boxes"].astype(int).to_dict()
    bpp_map = items.set_index("item_id")["boxes_per_pallet"].astype(int).to_dict()
    pallet_cap_map = slots.set_index("slot_id")["pallet_cap"].astype(int).to_dict()
    slot_cost_map = slots.set_index("slot_id")["slot_cost"].astype(float).to_dict()
    demand_map = items.set_index("item_id")["monthly_outbound_boxes"].astype(float).to_dict() if demand_available else {iid: 1.0 for iid in items["item_id"]}
    need_pallets_map = {
        item_id: int(math.ceil(int(qty_map[item_id]) / max(int(bpp_map[item_id]), 1)))
        for item_id in items["item_id"]
    }

    # 只需要 1 个货位的 item：P_i ≤ 它所有候选货位里最小的托盘容量，说明任何一个候选
    # 都装得下整件。此时拆开放永远不会更优（多付 alpha_slots、要占更贵的第二个货位、
    # 邻近性惩罚从 0 变正），所以可以直接强制 Σ_s y[i,s] = 1 —— 这既砍掉了"同一个 item
    # 在多货位间摊开"的大量对称解，也让它们不需要 partial / 邻近性变量。
    min_cap_by_item = cand.groupby("item_id")["pallet_cap"].min().to_dict()
    single_slot_items = {
        str(item_id)
        for item_id in items["item_id"].astype(str)
        if int(need_pallets_map[item_id]) <= int(min_cap_by_item.get(item_id, 0))
    }

    y, q, p, partial = {}, {}, {}, {}
    for r in cand.itertuples(index=False):
        key = (r.item_id, str(r.slot_id))
        # 上界收紧为 min(货位容量, 该 item 实际需要的量)：item 一共只要 P_i 个托盘 /
        # qty_i 个箱，在任何单个货位上都不可能超过这个数。
        cap_boxes = min(int(r.cap_boxes), int(qty_map[r.item_id]))
        cap_p = min(int(r.pallet_cap), int(need_pallets_map[r.item_id]))
        y[key] = model.NewBoolVar(f"y[{r.item_id},{r.slot_id}]")
        q[key] = model.NewIntVar(0, cap_boxes, f"q[{r.item_id},{r.slot_id}]")
        p[key] = model.NewIntVar(0, cap_p, f"p[{r.item_id},{r.slot_id}]")
        if str(r.item_id) not in single_slot_items:
            partial[key] = model.NewBoolVar(f"partial[{r.item_id},{r.slot_id}]")

    for slot_id, sub in cand.groupby("slot_id"):
        sid = str(slot_id)
        model.Add(sum(y[(iid, sid)] for iid in sub["item_id"].tolist()) <= 1)

    # Soft tie-breaker on same-cost slot selection: rank each slot within its
    # (slot_cost, pallet_cap) equivalence class by slot_id, then charge rank(s) per
    # y[i,s] in the objective. Small enough to only break exact ties (see scale
    # sizing below), so it never cuts a solution that is better on real cost or
    # proximity, but it collapses the same-cost permutation symmetry.
    slot_group_key = {
        str(row.slot_id): (float(row.slot_cost), int(row.pallet_cap))
        for row in slots.itertuples(index=False)
    }
    _tmp_groups: Dict[Tuple[float, int], list] = {}
    for sid, gkey in slot_group_key.items():
        _tmp_groups.setdefault(gkey, []).append(sid)
    slot_rank: Dict[str, int] = {}
    for group_slots in _tmp_groups.values():
        for r, sid in enumerate(sorted(group_slots)):
            slot_rank[sid] = r

    for item_id, sub in cand.groupby("item_id"):
        keys = [(item_id, str(sid)) for sid in sub["slot_id"].tolist()]
        if str(item_id) in single_slot_items:
            model.Add(sum(y[k] for k in keys) == 1)
        model.Add(sum(q[k] for k in keys) == int(qty_map[item_id]))
        # Valid inequality mirrored from COPT backend: total pallets per item >= ceil(qty/bpp).
        # Redundant on the integer program (implied by sum q == qty and q <= bpp*p) but
        # tightens the LP relaxation that CP-SAT's linear subsolver uses for bounding.
        model.Add(sum(p[k] for k in keys) >= int(need_pallets_map[item_id]))

    for r in cand.itertuples(index=False):
        key = (r.item_id, str(r.slot_id))
        bpp = int(bpp_map[r.item_id])
        # big-M 用 min(货位托盘容量, 该 item 需要的托盘数 P_i)，而不是恒取货位容量：
        # 原来 y ≥ p / cap_p_s 允许松弛把一个货位摊给多个 item，而整数解里一个货位只放
        # 一个 item，线性松弛界因此偏低 5% 以上；收紧后松弛界基本贴合最优。
        cap_p_phys = int(pallet_cap_map[str(r.slot_id)])
        cap_p = min(cap_p_phys, int(need_pallets_map[r.item_id]))
        cap_boxes = min(int(r.cap_boxes), int(qty_map[r.item_id]))
        model.Add(p[key] <= cap_p * y[key])
        model.Add(p[key] >= y[key])
        model.Add(q[key] <= bpp * p[key])
        # 箱数上界同样挂到 y 上，未开启该 item-slot 对时强制 q=0
        model.Add(q[key] <= cap_boxes * y[key])
        model.Add(q[key] >= y[key])
        # Tighten the pallet-count relationship to reduce symmetric, weak intermediate states.
        model.Add(q[key] >= bpp * p[key] - (bpp - 1)).OnlyEnforceIf(y[key])
        if key not in partial:
            continue
        # partial=1 iff slot opened but not full; combined with sum<=1 below, forces
        # "fill-then-open-next" semantics without the shared-slot infeasibility of static lex.
        model.Add(partial[key] <= y[key])
        # 注意：这里必须用货位的物理托盘容量，不能用上面收紧后的 cap_p。
        # partial 的语义是"开了货位但没装满"，若换成 min(cap, P_i)，则被拆分的 item
        # 每个分片都会判定成 partial，触发下面 Σ partial ≤ 1 而把可行解切掉。
        model.Add(p[key] + cap_p_phys * partial[key] >= cap_p_phys * y[key])

    for item_id, sub in cand.groupby("item_id"):
        keys = [(item_id, str(sid)) for sid in sub["slot_id"].tolist() if (item_id, str(sid)) in partial]
        if keys:
            model.Add(sum(partial[k] for k in keys) <= 1)

    # scale bumped from 100 to 10_000 so slot_rank tie-breaker (per-y coefficient
    # <= max group size ~4, total <= ~1e3) stays well below one real-cost unit
    # (min = 1 * demand_min * scale = 1e4, since preprocess clamps demand >= 1).
    scale = 10_000
    alpha_int = int(round(alpha_slots * scale))
    obj_terms = []
    for (item_id, slot_id), yvar in y.items():
        coef = float(slot_cost_map[slot_id]) * float(demand_map[item_id])
        coef_int = int(round(coef * scale))
        obj_terms.append(coef_int * p[(item_id, slot_id)])
        if alpha_int > 0:
            obj_terms.append(alpha_int * yvar)
        rank_int = int(slot_rank.get(slot_id, 0))
        if rank_int > 0:
            obj_terms.append(rank_int * yvar)

    # ---- 邻近性惩罚：同一个 SKU 被拆到多个货位时，惩罚它们之间的实际拣货绕行 ----
    # 只有被拆开的 item 才可能有非零惩罚，所以只给 multi-slot item 建变量。
    cand_slots_by_item: Dict[str, list[str]] = {}
    for r in cand.itertuples(index=False):
        if str(r.item_id) not in single_slot_items:
            cand_slots_by_item.setdefault(str(r.item_id), []).append(str(r.slot_id))

    geometry_covers_all = proximity_geometry is not None and all(
        sid in proximity_geometry.slot_aisle and sid in proximity_geometry.slot_pick_x
        for slot_list in cand_slots_by_item.values()
        for sid in slot_list
    )
    has_xy = ("x" in slots.columns) and ("y" in slots.columns)
    if alpha_proximity <= 0 and proximity_max_distance <= 0:
        proximity_metric = "off"
    elif not cand_slots_by_item:
        # 没有任何 item 会被拆到多个货位，邻近性恒为 0，不必建变量
        proximity_metric = "not_applicable"
    elif geometry_covers_all:
        proximity_metric = "picking_aisle"
    elif has_xy:
        proximity_metric = "slot_l1"
    else:
        proximity_metric = "off"

    max_distance = int(round(proximity_max_distance)) if proximity_max_distance > 0 else None

    def add_proximity_terms(item_id: str, span_parts: list, span_ub: int) -> None:
        """把某个 item 的拣货绕行 span 折算成目标项。

        span_parts 求和即该 item 占用货位的绕行距离。设置了弹性上限 K 时，K 以内不罚、
        只对超出部分按 alpha_proximity_excess 计价：既保留"必须挨着"的业务语义，又不会
        在货位紧张时把问题变成不可行，而是退化成"尽量近"。
        """
        demand = float(demand_map[item_id])
        linear_coef = int(round(float(alpha_proximity) * demand * scale))
        excess_coef = int(round(float(alpha_proximity_excess) * demand * scale))
        if linear_coef > 0:
            obj_terms.append(linear_coef * sum(span_parts))
        if max_distance is not None and excess_coef > 0:
            excess = model.NewIntVar(0, max(span_ub - max_distance, 0), f"excess[{item_id}]")
            model.Add(excess >= sum(span_parts) - max_distance)
            obj_terms.append(excess_coef * excess)

    if proximity_metric == "picking_aisle":
        # 拣货网络是"两条主通道 + 一条横通道"的 H 形，货位间的实际步行距离等于树上距离：
        #   同通道：|Δpick_x|
        #   跨通道：|x_a − x_cross| + |x_b − x_cross| + Δ通道y   （必须绕唯一的横通道）
        # 把它写成线性形式：按通道分别取被占用货位的 pick_x 跨度，跨通道时把每条通道的
        # 跨度强制延伸到横通道，再加上通道之间的 y 跨度。两个货位的情形下与真实距离精确相等。
        geo = proximity_geometry
        cross_x = int(round(geo.cross_x))
        for item_id, slot_list in cand_slots_by_item.items():
            span_parts = []
            slots_by_aisle: Dict[str, list[str]] = {}
            for sid in slot_list:
                slots_by_aisle.setdefault(geo.slot_aisle[sid], []).append(sid)

            ys = [int(round(geo.aisle_y[geo.slot_aisle[sid]])) for sid in slot_list]
            y_min = model.NewIntVar(min(ys), max(ys), f"ymin[{item_id}]")
            y_max = model.NewIntVar(min(ys), max(ys), f"ymax[{item_id}]")
            model.Add(y_max >= y_min)
            cross = model.NewBoolVar(f"cross[{item_id}]")
            # 只要占用的货位横跨两条通道（y 跨度 > 0），cross 必须为 1
            model.Add(y_max - y_min <= (max(ys) - min(ys)) * cross)

            for aisle_id, aisle_slots in slots_by_aisle.items():
                xs = [int(round(geo.slot_pick_x[sid])) for sid in aisle_slots]
                lb, ub = min(xs + [cross_x]), max(xs + [cross_x])
                x_min = model.NewIntVar(lb, ub, f"xmin[{item_id},{aisle_id}]")
                x_max = model.NewIntVar(lb, ub, f"xmax[{item_id},{aisle_id}]")
                used = model.NewBoolVar(f"used[{item_id},{aisle_id}]")
                model.Add(x_max >= x_min)
                ay = int(round(geo.aisle_y[aisle_id]))
                for sid in aisle_slots:
                    yk = y[(item_id, sid)]
                    px = int(round(geo.slot_pick_x[sid]))
                    model.Add(x_min <= px).OnlyEnforceIf(yk)
                    model.Add(x_max >= px).OnlyEnforceIf(yk)
                    model.Add(y_min <= ay).OnlyEnforceIf(yk)
                    model.Add(y_max >= ay).OnlyEnforceIf(yk)
                    model.Add(used >= yk)
                # 没用到这条通道时跨度归零；跨通道时跨度必须包含横通道的位置
                model.Add(x_max == x_min).OnlyEnforceIf(used.Not())
                model.Add(x_min <= cross_x).OnlyEnforceIf([cross, used])
                model.Add(x_max >= cross_x).OnlyEnforceIf([cross, used])
                span_parts.append(x_max - x_min)
            span_parts.append(y_max - y_min)
            all_x = [int(round(geo.slot_pick_x[sid])) for sid in slot_list] + [cross_x]
            span_ub = 2 * (max(all_x) - min(all_x)) + (max(ys) - min(ys))
            add_proximity_terms(item_id, span_parts, span_ub)

    elif proximity_metric == "slot_l1":
        # 退化度量：没有拣货几何时只能按货架坐标的 L1 跨度近似，会把背靠背的两排算成"远"。
        slot_x_map = slots.set_index("slot_id")["x"].astype(float).to_dict()
        slot_y_map = slots.set_index("slot_id")["y"].astype(float).to_dict()
        for item_id, slot_list in cand_slots_by_item.items():
            xs = [int(round(slot_x_map[s])) for s in slot_list]
            ys = [int(round(slot_y_map[s])) for s in slot_list]
            x_min = model.NewIntVar(min(xs), max(xs), f"x_min[{item_id}]")
            x_max = model.NewIntVar(min(xs), max(xs), f"x_max[{item_id}]")
            y_min = model.NewIntVar(min(ys), max(ys), f"y_min[{item_id}]")
            y_max = model.NewIntVar(min(ys), max(ys), f"y_max[{item_id}]")
            model.Add(x_max >= x_min)
            model.Add(y_max >= y_min)
            for slot_id in slot_list:
                sx = int(round(slot_x_map[slot_id]))
                sy = int(round(slot_y_map[slot_id]))
                yk = y[(item_id, slot_id)]
                model.Add(x_min <= sx).OnlyEnforceIf(yk)
                model.Add(x_max >= sx).OnlyEnforceIf(yk)
                model.Add(y_min <= sy).OnlyEnforceIf(yk)
                model.Add(y_max >= sy).OnlyEnforceIf(yk)
            add_proximity_terms(
                item_id,
                [x_max - x_min, y_max - y_min],
                (max(xs) - min(xs)) + (max(ys) - min(ys)),
            )

    model.Minimize(sum(obj_terms))

    # 热启动：直接复用 heuristic 后端的解。它按"托盘块权重降序 × 货位成本升序"配对，
    # 在纯成本目标下就是最优解，作为 hint 能让 CP-SAT 一开始就拿到接近最优的上界。
    from solvers.slotting_heuristic_backend import solve_with_heuristic

    hint_values = {}
    try:
        hint_sol, _ = solve_with_heuristic(
            slots=slots, items=items, cand=cand,
            time_limit_s=time_limit_s, alpha_slots=alpha_slots,
            use_demand_if_present=use_demand_if_present,
        )
    except RuntimeError:
        hint_sol = None  # 启发式在 forbidden/fixed 很紧时可能失败，此时不给 hint
    if hint_sol is not None:
        for r in hint_sol.itertuples(index=False):
            key = (str(r.item_id), str(r.slot_id))
            if key not in y:
                continue
            cap_hint = int(pallet_cap_map.get(str(r.slot_id), 0))
            hint_values[y[key]] = 1
            hint_values[q[key]] = int(r.qty_boxes_in_slot)
            hint_values[p[key]] = int(r.pallets_used_in_slot)
            if key in partial:
                hint_values[partial[key]] = 0 if int(r.pallets_used_in_slot) >= cap_hint else 1

    if hint_values:
        for var, value in hint_values.items():
            model.AddHint(var, value)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = int(num_search_workers)
    solver.parameters.random_seed = 42
    # 目标曲面平坦，最后千分之几的 gap 要花掉绝大部分时间，默认容忍 0.5%。
    if mip_gap > 0:
        solver.parameters.relative_gap_limit = float(mip_gap)
    solver.parameters.log_search_progress = True
    solver.parameters.log_subsolver_statistics = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"No feasible solution from OR-Tools. status={status}")

    out_rows = []
    for (item_id, slot_id), yvar in y.items():
        if solver.Value(yvar) > 0:
            out_rows.append((
                item_id,
                slot_id,
                int(solver.Value(q[(item_id, slot_id)])),
                int(solver.Value(p[(item_id, slot_id)])),
                float(slot_cost_map[slot_id]),
            ))

    sol = pd.DataFrame(out_rows, columns=["item_id", "slot_id", "qty_boxes_in_slot", "pallets_used_in_slot", "slot_cost"])
    meta = {
        "backend": "ortools",
        "status": int(status),
        "objective_value": solver.ObjectiveValue(),
        "best_objective_bound": solver.BestObjectiveBound(),
        "wall_time": solver.WallTime(),
        "alpha_proximity": float(alpha_proximity),
        "proximity_metric": proximity_metric,
        "proximity_max_distance": float(proximity_max_distance),
        "alpha_proximity_excess": float(alpha_proximity_excess),
        "single_slot_items": len(single_slot_items),
        "mip_gap_limit": float(mip_gap),
        "proximity_enabled": proximity_metric in ("picking_aisle", "slot_l1"),
    }
    return sol, meta
