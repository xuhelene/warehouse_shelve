"""先固定尾板/FIFO选货数量，再用后悔值插入和VNS优化完整运输路线。"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from time import monotonic

from solvers.warehouse_outbound_vns import search


def _decimal(value):
    return Decimal(value.numerator) / Decimal(value.denominator)


def _route(rows, capacities, layout, task, distance, deadline=None, emit=False):
    """按任务序列推演取货、分托和卸货；搜索与输出共用同一容量规则。"""
    legs, picks, trips = [], [], []
    current, load, limit = "STAGING", Fraction(0), None
    owner = rows[0]["owner_code"] if rows else None
    loaded_skus, total, trip_start = set(), 0., 0

    def unload():
        nonlocal current, load, limit, total, trip_start
        if load:
            total += distance(current, "STAGING")
            if emit:
                leg = layout.leg(current, "STAGING")
                leg.update(task=task, action="unload", owner_code=owner, load_before=_decimal(load), load_after=Decimal(0))
                legs.append(leg)
                trips.append(dict(task=task, owner_code=owner, loaded_boxes=_decimal(load),
                                  legs=deepcopy(legs[trip_start:]), distance=sum(l["distance"] for l in legs[trip_start:])))
        current, load, limit = "STAGING", Fraction(0), None
        loaded_skus.clear()
        trip_start = len(legs)

    for row in rows:
        remaining = row["base_quantity"]
        cap = Fraction(capacities[(owner, row["sku_id"])])
        ratio = Fraction(row["conversion_ratio"])
        while remaining:
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("出库路径规划达到时间上限，未发布不完整方案")
            new_limit = min(limit, cap) if limit is not None else cap
            take_boxes = min(Fraction(remaining) / ratio, cap)
            if row["sku_id"] in loaded_skus:
                take_boxes = min(take_boxes, new_limit - load)
            if load and load + take_boxes > new_limit:
                unload()
                continue
            limit = new_limit
            full = load + take_boxes == limit
            take_base = take_boxes * ratio
            quantity = remaining if take_base == remaining else _decimal(take_base)
            if Fraction(quantity) > take_base:
                quantity = quantity.next_minus()
            actual_boxes = Fraction(quantity) / ratio
            total += distance(current, row["slot_id"])
            if emit:
                boxes = quantity / row["conversion_ratio"]
                detail = {**row, **layout.slots[row["slot_id"]], "task": task, "trip": len(trips) + 1,
                          "boxes": boxes, "base_quantity": quantity, "quantity": boxes, "unit": "箱"}
                picks.append(detail)
                leg = layout.leg(current, row["slot_id"])
                leg.update(task=task, action="pick", owner_code=owner, order_no=row["order_no"],
                           sku_id=row["sku_id"], boxes=boxes, load_before=_decimal(load),
                           load_after=_decimal(load + actual_boxes), capacity=_decimal(limit))
                legs.append(leg)
            load += actual_boxes
            loaded_skus.add(row["sku_id"])
            current = row["slot_id"]
            # quantity可能含28位循环小数近似；减法不能再次舍入并丢掉剩余主单位。
            with localcontext() as context:
                context.prec = max(context.prec, remaining.adjusted() - min(remaining.as_tuple().exponent, quantity.as_tuple().exponent) + 2)
                remaining -= quantity
            if full:
                unload()
    unload()
    return dict(distance=total, legs=legs, picks=picks, trips=trips)


def solve(inventory, orders, capacities, layout, flag, deadline, *, search_method=None, route_strategy="VNS",
          route_optimizer=None):
    search_method = search if search_method is None else search_method
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
            sum(Fraction(r["base_quantity"]) / Fraction(r["conversion_ratio"]) for r in rows) < capacities[(rows[0]["owner_code"], rows[0]["sku_id"])]
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
        tasks[("WAVE" if flag else row["order_no"], row["owner_code"])].append(row)

    @lru_cache(maxsize=None)
    def distance(start, end):
        return layout.leg(start, end)["distance"]

    # 先构造全部任务的完整可行解，之后才在剩余时限内优化。
    plans = []
    initial_distance = 0.
    for (task, owner), rows in tasks.items():
        initial = sorted(range(len(rows)), key=lambda i: (rows[i]["slot_id"], rows[i]["sku_id"], rows[i]["order_no"], i))
        baseline = _route([rows[i] for i in initial], capacities, layout, task, distance, deadline,
                          emit=route_optimizer is not None)
        plans.append((task, rows, initial, baseline))
        initial_distance += baseline["distance"]
    legs, picks, trips = [], [], []
    stats = dict(evaluations=0, improvements=0, perturbations=0, time_limit_reached=False)
    search_details = []
    def append_route(route):
        for detail in route["picks"]:
            detail["trip"] += len(trips)
        legs.extend(route["legs"])
        picks.extend(route["picks"])
        trips.extend(route["trips"])

    for task, rows, initial, baseline in plans:
        if route_optimizer is not None:
            for route in route_optimizer(task, distance, baseline):
                append_route(route)
            continue
        value = baseline["distance"]

        def evaluate(sequence):
            return _route([rows[i] for i in sequence], capacities, layout, task, distance, deadline)["distance"]

        def tie_key(sequence):
            return tuple((distance("STAGING", rows[i]["slot_id"]), rows[i]["slot_id"],
                          rows[i]["sku_id"], rows[i]["order_no"], i) for i in sequence)

        if len({(r["slot_id"], r["sku_id"]) for r in rows}) == 1:
            # 同货位同SKU只有一条物理路线，时间/批次明细的排列不会改善距离。
            sequence, info = initial, dict(evaluations=0, improvements=0, perturbations=0, time_limit_reached=False)
        else:
            sequence, value, info = search_method(evaluate, initial, value, deadline, tie_key)
        for key in stats:
            if key == "time_limit_reached":
                stats[key] = stats[key] or info[key]
            else:
                stats[key] += info[key]
        search_details.append(info)
        # 搜索耗尽时返回已有完整解；输出重建不再启动新的搜索。
        route = _route([rows[i] for i in sequence], capacities, layout, task, distance, emit=True)
        append_route(route)
    stats["initial_distance"] = initial_distance
    if route_strategy == "ALNS":
        stats["groups"] = search_details
        stats["iterations"] = sum(info.get("iterations", 0) for info in search_details)
    if stats["time_limit_reached"]:
        warnings.append(f"{route_strategy}达到搜索时间上限，返回已有完整可行方案，未证明全局最优")
    for row in stock:
        row["boxes"] = row["base_quantity"] / row["conversion_ratio"]
        row["quantity"] = row["boxes"]
        row["unit"] = "箱"
        if "available_base" in row:
            row["available_base_quantity"] = row["available_base"]
            row["available_quantity"] = row["available_base"] / row["conversion_ratio"]
    cleared = [slot for slot, rows in slots.items() if all(r["base_quantity"] == 0 for r in rows)]
    return dict(status="partial" if shortages else "feasible", storeBatchFlag=flag, strategy="FIFO",
                warnings=warnings, route_strategy=route_strategy, search=stats,
                picks=picks, trips=trips, legs=legs, shortages=shortages, remaining_inventory=stock,
                cleared_slots=cleared, total_distance=sum(l["distance"] for l in legs),
                message="存在缺货，已输出可满足部分" if shortages else
                "出库COPT规划完成" if route_optimizer is not None else "出库启发式规划完成")
