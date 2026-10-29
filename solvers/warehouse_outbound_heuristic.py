"""先固定尾板/FIFO选货数量，再按最近邻生成货主隔离的一托作业路线。"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from time import monotonic


def solve(inventory, orders, capacities, layout, flag, deadline):
    stock = deepcopy(inventory)
    slots = defaultdict(list)
    for index, row in enumerate(stock):
        row["inventory_index"] = index
        if not row.get("staging") and row["base_quantity"] > 0:
            slots[row["slot_id"]].append(row)
    warnings = [f"现存混托货位{slot}不在第1层，按现存库存执行拣货；需另行移库处理"
                for slot, rows in slots.items()
                if len({r["sku_id"] for r in rows}) > 1 and layout.slots[slot]["level"] != 1]
    # 混托不判满；其中各SKU均参与尾板优先选择。
    required_skus = {(r["owner_code"], r["sku_id"]) for r in orders}
    tail = {slot: len({r["sku_id"] for r in rows}) > 1 or
            sum(r["boxes"] for r in rows) < capacities[(rows[0]["owner_code"], rows[0]["sku_id"])]
            for slot, rows in slots.items()
            if any((r["owner_code"], r["sku_id"]) in required_skus for r in rows)}
    order_owners = defaultdict(set)
    for row in orders:
        order_owners[row["order_no"]].add(row["owner_code"])
    if flag == 0 and any(len(owners) > 1 for owners in order_owners.values()):
        raise ValueError("按单模式的同一订单必须属于同一货主")
    selected, shortages = [], []
    for order in orders:
        need = order["base_quantity"]
        sources = [r for rows in slots.values() for r in rows if r["owner_code"] == order["owner_code"] and r["sku_id"] == order["sku_id"]]
        if any(r["base_unit"] != order["base_unit"] or r["conversion_ratio"] != order["conversion_ratio"] for r in sources):
            raise ValueError(f"{order['sku_id']}库存与订单的单位换算不一致")
        sources.sort(key=lambda r: (not tail[r["slot_id"]], r["timestamp"], layout.leg("STAGING", r["slot_id"])["distance"], r["slot_id"]))
        for row in sources:
            if monotonic() >= deadline:
                raise TimeoutError("出库库存选择达到时间上限")
            take = min(need, row["available_base"])
            if take <= 0:
                continue
            selected.append({**row, "order_no": order["order_no"], "base_quantity": take,
                             "boxes": take / row["conversion_ratio"], "tail": tail[row["slot_id"]]})
            row["available_base"] -= take
            row["base_quantity"] -= take
            need -= take
            if not need:
                break
        if need:
            shortages.append(dict(order_no=order["order_no"], owner_code=order["owner_code"], sku_code=order["sku_id"],
                                  sku_name=order.get("name", ""), requested_quantity=order["base_quantity"],
                                  fulfilled_quantity=order["base_quantity"] - need, shortage_quantity=need, unit=order["base_unit"]))
    tasks = defaultdict(list)
    for row in selected:
        tasks["WAVE" if flag else row["order_no"]].append(deepcopy(row))
    legs, picks, trips = [], [], []
    for task, pending in tasks.items():
        current, owner, load, limit = "STAGING", None, Decimal(0), None
        loaded_skus = set()
        trip_start = len(legs)

        def unload():
            nonlocal current, owner, load, limit, trip_start
            if load:
                leg = layout.leg(current, "STAGING")
                leg.update(task=task, action="unload", owner_code=owner, load_before=load, load_after=Decimal(0))
                legs.append(leg)
                trips.append(dict(task=task, owner_code=owner, loaded_boxes=load,
                                  legs=deepcopy(legs[trip_start:]), distance=sum(l["distance"] for l in legs[trip_start:])))
            current, owner, load, limit = "STAGING", None, Decimal(0), None
            loaded_skus.clear()
            trip_start = len(legs)

        while pending:
            if monotonic() >= deadline:
                raise TimeoutError("出库路径规划达到时间上限，未发布不完整方案")
            same_owner = [r for r in pending if owner is None or r["owner_code"] == owner]
            if not same_owner:
                unload()
                continue
            row = min(same_owner, key=lambda r: (layout.leg(current, r["slot_id"])["distance"], r["slot_id"], r["sku_id"], r["order_no"]))
            cap = capacities[(row["owner_code"], row["sku_id"])]
            new_limit = min(limit, cap) if limit is not None else cap
            # 本次取货量先按空托容量确定；新SKU使上限降低且超限时先卸货。
            take_boxes = min(row["boxes"], cap)
            if row["sku_id"] in loaded_skus:
                take_boxes = min(take_boxes, new_limit - load)
            if load and load + take_boxes > new_limit:
                unload()
                continue
            owner, limit = row["owner_code"], new_limit
            leg = layout.leg(current, row["slot_id"])
            detail = {**row, **layout.slots[row["slot_id"]], "task": task, "trip": len(trips) + 1,
                      "boxes": take_boxes, "base_quantity": row["base_quantity"] if take_boxes == row["boxes"] else take_boxes * row["conversion_ratio"],
                      "quantity": take_boxes, "unit": "箱"}
            picks.append(detail)
            leg.update(task=task, action="pick", owner_code=owner, order_no=row["order_no"],
                       sku_id=row["sku_id"], boxes=take_boxes, load_before=load, load_after=load + take_boxes, capacity=limit)
            legs.append(leg)
            load += take_boxes
            loaded_skus.add(row["sku_id"])
            current = row["slot_id"]
            row["boxes"] -= take_boxes
            row["base_quantity"] -= detail["base_quantity"]
            if row["boxes"] == 0:
                pending.remove(row)
            if load == limit:
                unload()
        unload()
    for row in stock:
        row["boxes"] = row["base_quantity"] / row["conversion_ratio"]
        row["quantity"] = row["boxes"]
        row["unit"] = "箱"
        if "available_base" in row:
            row["available_base_quantity"] = row["available_base"]
            row["available_quantity"] = row["available_base"] / row["conversion_ratio"]
    cleared = [slot for slot, rows in slots.items() if all(r["base_quantity"] == 0 for r in rows)]
    return dict(status="partial" if shortages else "feasible", storeBatchFlag=flag, strategy="FIFO",
                warnings=warnings,
                picks=picks, trips=trips, legs=legs, shortages=shortages, remaining_inventory=stock,
                cleared_slots=cleared, total_distance=sum(l["distance"] for l in legs),
                message="存在缺货，已输出可满足部分" if shortages else "出库启发式规划完成")
