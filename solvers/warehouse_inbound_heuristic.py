"""先按层级偏好贪心分配；空位不足才追加单SKU尾板或组新混托。"""
from copy import deepcopy
from time import monotonic


def solve(inventory, orders, capacities, layout, grades, deadline):
    stock = deepcopy(inventory)
    occupied = {}
    for row in stock:
        if not row.get("staging") and row["base_quantity"] > 0:
            occupied.setdefault(row["slot_id"], []).append(row)
    empty = set(layout.slots) - occupied.keys()
    groups = []
    for row in orders:
        remaining = row["base_quantity"]
        cap = capacities[(row["owner_code"], row["sku_id"])] * row["conversion_ratio"]
        while remaining:
            if monotonic() >= deadline:
                raise TimeoutError("入库规划达到时间上限，未发布不完整方案")
            quantity = min(remaining, cap)
            detail = {**row, "base_quantity": quantity, "boxes": quantity / row["conversion_ratio"]}
            grade = grades.get((row["warehouse_code"], row["owner_code"], row["sku_id"]), "低")
            groups.append(dict(details=[detail], boxes=detail["boxes"], cap=cap / row["conversion_ratio"],
                               tail=quantity < cap, grade=grade, target=None))
            remaining -= quantity
    groups.sort(key=lambda g: (0 if g["tail"] and g["boxes"] < 1 else 1 if g["tail"] else 2,
                               {"高": 0, "中": 1, "低": 2}[g["grade"]]))
    # 只有空位不足才考虑混放；已混托（多SKU）永远不作为追加目标。
    if len(groups) > len(empty):
        retained = []
        required = len(groups)
        for group in groups:
            if monotonic() >= deadline:
                raise TimeoutError("入库规划达到时间上限")
            row = group["details"][0]
            if group["tail"] and required > len(empty):
                candidates = []
                for slot, rows in occupied.items():
                    if layout.slots[slot]["level"] != 1 or len({r["sku_id"] for r in rows}) != 1:
                        continue
                    if rows[0]["owner_code"] != row["owner_code"]:
                        continue
                    total = sum(r["boxes"] for r in rows)
                    old_cap = capacities.get((rows[0]["owner_code"], rows[0]["sku_id"]))
                    if old_cap is None:
                        continue
                    if total < old_cap and total + group["boxes"] <= min(old_cap, group["cap"]):
                        candidates.append(slot)
                if candidates:
                    target = min(candidates, key=lambda s: (layout.leg("STAGING", s)["distance"], s))
                    group["target"] = target
                    occupied[target] = occupied[target] + deepcopy(group["details"])
                    retained.append(group)
                    required -= 1
                    continue
                # 本次多个不同SKU尾板可以组托，不移动已有库存。
                merged = False
                for other in retained:
                    if not any(layout.slots[s]["level"] == 1 for s in empty):
                        break
                    if other["target"] or not other["tail"] or other["details"][0]["owner_code"] != row["owner_code"]:
                        continue
                    if any(r["sku_id"] == row["sku_id"] for r in other["details"]):
                        continue
                    limit = min(other["cap"], group["cap"])
                    if other["boxes"] + group["boxes"] <= limit:
                        other["details"].extend(group["details"])
                        other["boxes"] += group["boxes"]
                        other["cap"] = limit
                        required -= 1
                        merged = True
                        break
                if not merged:
                    retained.append(group)
                continue
            retained.append(group)
        groups = retained
    assignments, unassigned, warnings, legs = [], [], [], []
    for slot, rows in occupied.items():
        if len({r["sku_id"] for r in rows}) > 1 and layout.slots[slot]["level"] != 1:
            warnings.append(f"现存混托货位{slot}不在第1层，保留占用且不追加货物；需另行移库处理")
    for group in groups:
        if monotonic() >= deadline:
            raise TimeoutError("入库规划达到时间上限，未发布不完整方案")
        mixed = len({r["sku_id"] for r in group["details"]}) > 1
        legal = [s for s in empty if (not mixed or layout.slots[s]["level"] == 1) and
                 (group["tail"] or layout.slots[s]["level"] > 1)]
        preferred_levels = {1} if group["tail"] else {2} if group["grade"] == "高" else {2, 3, 4} if group["grade"] == "中" else {5}
        preferred = [s for s in legal if layout.slots[s]["level"] in preferred_levels]
        candidates = preferred or legal
        slot = group["target"] or (min(candidates, key=lambda s: (layout.leg("STAGING", s)["distance"], s)) if candidates else None)
        if slot is None:
            unassigned.extend({**r, "reason": "库位不足，需要移库"} for r in group["details"])
            continue
        if slot in empty:
            empty.remove(slot)
        if layout.slots[slot]["level"] not in preferred_levels:
            warnings.append(f"{slot}偏好层不足，按距离退让")
        leg = layout.leg("STAGING", slot)
        legs.append(leg)
        for row in group["details"]:
            detail = {**row, **layout.slots[slot], "tail": group["tail"],
                      "grade": grades.get((row["warehouse_code"], row["owner_code"], row["sku_id"]), "低"),
                      "mixed": mixed or len({r["sku_id"] for r in occupied.get(slot, [])} | {row["sku_id"]}) > 1,
                      "appended": bool(group["target"])}
            detail["quantity"] = detail["boxes"]
            detail["unit"] = "箱"
            assignments.append(detail)
            stock.append(deepcopy(detail))
    return dict(status="partial" if unassigned else "feasible", assignments=assignments,
                remaining_inventory=stock, unassigned=unassigned, warnings=warnings, legs=legs,
                total_distance=sum(leg["distance"] for leg in legs),
                message="库位不足，需要移库" if unassigned else "入库启发式规划完成")
