"""立体库入库 MIP：拆托后整组指派，不拆分尾托、不移动已有库存。"""
from copy import deepcopy
from decimal import Decimal
from time import monotonic


def check_deadline(deadline):
    if monotonic() >= deadline:
        raise TimeoutError("入库建模达到时间上限，未生成可行方案")


def prepare_groups(inventory, orders, capacities, layout, grades, deadline=float("inf")):
    occupied = {}
    for row in inventory:
        if not row.get("staging") and row["base_quantity"] > 0:
            occupied.setdefault(row["slot_id"], []).append(row)
    groups = []
    for row in orders:
        cap = capacities[row["owner_code"], row["sku_id"]]
        remaining = row["base_quantity"]
        while remaining:
            check_deadline(deadline)
            quantity = min(remaining, cap * row["conversion_ratio"])
            detail = {**row, "base_quantity": quantity, "boxes": quantity / row["conversion_ratio"]}
            grade = grades.get((row["warehouse_code"], row["owner_code"], row["sku_id"]), "低")
            groups.append(dict(row=detail, cap=cap, tail=detail["boxes"] < cap, grade=grade))
            remaining -= quantity
    empty = set(layout.slots) - occupied.keys()
    allow_mix = len(groups) > len(empty) or sum(g["tail"] for g in groups) > sum(layout.slots[s]["level"] == 1 for s in empty)
    return groups, occupied, empty, allow_mix


def build_model(cp, COPT, model, groups, occupied, empty, allow_mix, capacities, layout, deadline=float("inf"), *, slot_usage=None):
    return build_assignment_model(
        lambda name: model.addVar(vtype=COPT.BINARY, name=name), model.addConstr,
        cp.quicksum, groups, occupied, empty, allow_mix, capacities, layout, deadline, slot_usage=slot_usage,
    )


def aggregate_groups(groups, empty, layout, enabled, deadline):
    """只聚合同货主同SKU且数量、容量、等级、候选完全相同的整托。"""
    classes, modeled, candidates = {}, [], {}
    for index, group in enumerate(groups):
        check_deadline(deadline)
        row = group["row"]
        key = None
        if enabled and not group["tail"]:
            identity = (row["warehouse_code"], row["owner_code"], row["sku_id"])
            if identity not in candidates:
                candidates[identity] = tuple(s for s, info in layout.slots.items()
                    if s in empty and info["level"] > 1)
            key = (identity, row["base_quantity"], row["boxes"], row["conversion_ratio"], row["base_unit"],
                   group["cap"], group["grade"], candidates[identity])
        if key is not None and key in classes:
            modeled[classes[key]]["members"].append(index)
        else:
            if key is not None:
                classes[key] = len(modeled)
            modeled.append({**group, "members": [index]})
    return modeled


def expand_selection(selected, modeled):
    """将类的货位分配稳定展开回原始拆托；不改订单、批次、时间及数量。"""
    next_member, expanded = {}, []
    for index, slot in sorted(selected):
        position = next_member.get(index, 0)
        expanded.append((modeled[index]["members"][position], slot))
        next_member[index] = position + 1
    return expanded


def model_statistics(groups, modeled, x, enabled):
    before = sum(len(modeled[i]["members"]) for i, slot in x)
    return dict(aggregate_full=enabled, original_groups=len(groups), model_groups=len(modeled),
                aggregated_classes=sum(len(g["members"]) > 1 for g in modeled),
                assignment_variables=len(x), baseline_assignment_variables=before,
                removed_assignment_variables=before - len(x))


def build_assignment_model(binary, add, sum_terms, groups, occupied, empty, allow_mix,
                           capacities, layout, deadline, *, slot_usage=None):
    x, by_slot = {}, {}
    for i, group in enumerate(groups):
        check_deadline(deadline)
        row = group["row"]
        candidates = []
        for slot, info in layout.slots.items():
            rows = occupied.get(slot, [])
            if slot not in empty:
                if not (allow_mix and group["tail"] and info["level"] == 1 and rows
                        and len({r["sku_id"] for r in rows}) == 1
                        and rows[0]["owner_code"] == row["owner_code"]):
                    continue
                old_cap = capacities[rows[0]["owner_code"], rows[0]["sku_id"]]
                if sum(r["boxes"] for r in rows) >= old_cap:
                    continue
            if not group["tail"] and info["level"] == 1:
                continue
            x[i, slot] = binary(f"x_{i}_{slot}")
            candidates.append(x[i, slot])
            by_slot.setdefault(slot, []).append(i)
        add(sum_terms(candidates) <= len(group.get("members", [i])))
    distance_terms = []
    for slot, indices in by_slot.items():
        check_deadline(deadline)
        variables = [x[i, slot] for i in indices]
        slot_distance = layout.leg("STAGING", slot)["distance"]
        can_share = allow_mix and layout.slots[slot]["level"] == 1
        if not can_share:
            assigned = sum_terms(variables)
            if slot_usage is not None:
                slot_usage[slot] = assigned
            add(assigned <= 1)
            distance_terms.append(slot_distance * assigned)
            continue
        used = binary(f"used_{slot}")
        if slot_usage is not None:
            slot_usage[slot] = used
        add(used <= sum_terms(variables))
        for v in variables:
            add(v <= used)
        distance_terms.append(slot_distance * used)
        # 同一货主，新增托之间仅不同SKU尾托可以合并。
        for pos, i in enumerate(indices):
            check_deadline(deadline)
            a = groups[i]["row"]
            for j in indices[pos + 1:]:
                b = groups[j]["row"]
                if a["owner_code"] != b["owner_code"] or a["sku_id"] == b["sku_id"]:
                    add(x[i, slot] + x[j, slot] <= 1)
        rows = occupied.get(slot, [])
        stock_boxes = sum((r["boxes"] for r in rows), Decimal(0))
        total = sum_terms(float(groups[i]["row"]["boxes"]) * x[i, slot] for i in indices)
        max_cap = max(groups[i]["cap"] for i in indices)
        if rows:
            add(total <= float(capacities[rows[0]["owner_code"], rows[0]["sku_id"]] - stock_boxes))
        for i in indices:
            add(total <= float(groups[i]["cap"] - stock_boxes) + float(max_cap + stock_boxes) * (1 - x[i, slot]))
    return x, sum_terms(distance_terms)



def preferred_levels(group):
    if group["tail"]:
        return {1}
    return {"高": {2}, "中": {2, 3, 4}}.get(group["grade"], {5})


def build_result(inventory, layout, groups, occupied, allow_mix, selected, stages, warnings, started, backend):
    assignments, legs = [], []
    for slot in sorted({s for i, s in selected}):
        indices = [i for i, s in selected if s == slot]
        mixed = len({groups[i]["row"]["sku_id"] for i in indices} | {r["sku_id"] for r in occupied.get(slot, [])}) > 1
        legs.append(layout.leg("STAGING", slot))
        for i in indices:
            group = groups[i]
            row = {**group["row"], **layout.slots[slot], "tail": group["tail"], "grade": group["grade"], "mixed": mixed,
                   "appended": slot in occupied, "quantity": group["row"]["boxes"], "unit": "箱"}
            assignments.append(row)
            if row["level"] not in preferred_levels(group):
                warnings.append(f"{slot}未采用偏好层，按上架量及距离目标分配")
    chosen = {i for i, s in selected}
    unassigned = [{**g["row"], "reason": "库位不足，需要移库"} for i, g in enumerate(groups) if i not in chosen]
    for slot, rows in occupied.items():
        if len({r["sku_id"] for r in rows}) > 1 and layout.slots[slot]["level"] != 1:
            warnings.append(f"现存混托货位{slot}不在第1层，保留占用且不追加货物；需另行移库处理")
    optimal = len(stages) == 3 and all(stage.get("proven_optimal", False) for stage in stages)
    return dict(status="partial" if unassigned else "feasible",
                optimality="optimal" if optimal else "not_proven", assignments=assignments,
                remaining_inventory=deepcopy(inventory) + deepcopy(assignments), unassigned=unassigned,
                warnings=warnings, legs=legs, total_distance=sum(leg["distance"] for leg in legs),
                message="库位不足，需要移库" if unassigned else f"入库{backend}规划完成", solver_stages=stages,
                solve_time_s=monotonic() - started, allow_mix=allow_mix)
