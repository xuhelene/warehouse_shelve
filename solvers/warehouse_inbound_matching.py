"""精确整数费用的一托一位匹配。"""
from fractions import Fraction
from math import lcm

from modules.slotting.inbound_model import check_deadline, preferred_levels


def match(groups, empty, layout, deadline):
    from ortools.graph.python import min_cost_flow

    has_tail = any(group["tail"] for group in groups)
    slots = [slot for slot, info in layout.slots.items()
             if slot in empty and (has_tail or info["level"] != 1)]
    legs, distances, horizontal_by_node = {}, {}, {}
    vertical_step = Fraction("1.7") * Fraction(str(layout.layer_weight))
    for slot in slots:
        check_deadline(deadline)
        leg = legs[slot] = layout.leg("STAGING", slot)
        info = layout.slots[slot]
        node = info["node"]
        if node not in horizontal_by_node:
            points = [(Fraction(str(p["x"])), Fraction(str(p["y"]))) for p in leg["path"]]
            horizontal_by_node[node] = sum(
                abs(x - a) + abs(y - b) for (x, y), (a, b) in zip(points, points[1:])
            )
        distances[slot] = horizontal_by_node[node] + (info["level"] - 1) * vertical_step

    quantities = [Fraction(g["row"]["boxes"]) for g in groups]
    quantity_scale = lcm(*(q.denominator for q in quantities))
    distance_scale = lcm(*(d.denominator for d in distances.values()))
    distance_costs = {s: int(d * distance_scale) for s, d in distances.items()}
    count = len(groups)
    distance_weight = count + 1
    # 一单位箱数优先于全部距离和层位代价，一单位距离优先于全部层位代价。
    quantity_weight = count * (max(distance_costs.values(), default=0) * distance_weight + 1) + 1
    skipped_costs = [int(q * quantity_scale) * quantity_weight for q in quantities]
    # OR-Tools 的弧费用和总费用使用有符号 64 位整数。
    if sum(skipped_costs) + quantity_weight >= 1 << 60:
        return None

    flow = min_cost_flow.SimpleMinCostFlow()
    sink = count + len(slots)
    assignment_arcs = {}
    for i, group in enumerate(groups):
        check_deadline(deadline)
        flow.set_node_supply(i, 1)
        flow.add_arc_with_capacity_and_unit_cost(i, sink, 1, skipped_costs[i])
        preferred = preferred_levels(group)
        for j, slot in enumerate(slots, count):
            level = layout.slots[slot]["level"]
            if group["tail"] or level != 1:
                cost = distance_costs[slot] * distance_weight + (level not in preferred)
                arc = flow.add_arc_with_capacity_and_unit_cost(i, j, 1, cost)
                assignment_arcs[arc] = (i, slot)
    for j in range(count, sink):
        flow.add_arc_with_capacity_and_unit_cost(j, sink, 1, 0)
    flow.set_node_supply(sink, -count)
    check_deadline(deadline)
    status = flow.solve()
    if status != flow.OPTIMAL:
        return None
    selected = [key for arc, key in assignment_arcs.items() if flow.flow(arc)]
    values = (
        sum((groups[i]["row"]["boxes"] for i, _ in selected), 0),
        sum(legs[s]["distance"] for _, s in selected),
        sum(layout.slots[s]["level"] not in preferred_levels(groups[i]) for i, s in selected),
    )
    stages = [dict(name=name, status=0, objective=float(value), best_bound=float(value), mip_gap=0)
              for name, value in zip(("quantity", "distance", "preference"), values)]
    return selected, stages
