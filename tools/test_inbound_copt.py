"""python -m tools.test_inbound_copt：真实许可下的跨平台入库回归入口。"""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path

from apps.warehouse_data import prepare
from core.algorithm_settings import SolverSettings
from core.warehouse_layout import WarehouseLayout
from solvers.warehouse_inbound_copt import solve


def objective_cases():
    """与启发式偏好顺序无关的COPT目标回归。"""
    from tools.generate_inbound_test_data import scenarios, row
    base = scenarios()[0]
    definitions = [
        ("copt_boxes_first", [row(qty=1), row("B", 40)], ["C15-1-3"], [], "40", 10.35),
        ("copt_distance_first", [row(qty=40)], ["C15-45-2", "C15-1-3"], [["WH", "O", "A", "高"]], "40", 10.35),
        ("copt_global_mix", [row(qty=1), row("B", 30), row("C", 19)], ["C15-1-1"], [], "31", 6.95),
    ]
    return [{**deepcopy(base), "id": name, "orders": orders, "slots": slots, "grades": grades,
             "copt_expected": dict(assigned_boxes=boxes, total_distance=distance)}
            for name, orders, slots, grades, boxes, distance in definitions]


def validate(inventory, orders, capacities, layout, result):
    """独立检查数量守恒、库存不变、货主、容量、层位和距离。"""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def totals(rows):
        quantities = defaultdict(Decimal)
        for row in rows:
            key = tuple(row.get(k) for k in ("warehouse_code", "owner_code", "sku_id", "order_no", "batch_no", "timestamp", "base_unit", "conversion_ratio"))
            quantities[key] += row["base_quantity"]
        return {k: v for k, v in quantities.items() if v}

    assigned, unassigned = result["assignments"], result["unassigned"]
    require(totals(orders) == totals(assigned + unassigned), "订单数量或批次不守恒")
    require(result["remaining_inventory"] == inventory + assigned, "原库存被修改或新增库存不一致")
    require(result["status"] == ("partial" if unassigned else "feasible"), "结果状态不一致")
    occupied = defaultdict(list)
    for row in inventory:
        if not row.get("staging") and row["base_quantity"]:
            occupied[row["slot_id"]].append(row)
    groups = sum(int(r["base_quantity"] // (capacities[r["owner_code"], r["sku_id"]] * r["conversion_ratio"])) + bool(r["base_quantity"] % (capacities[r["owner_code"], r["sku_id"]] * r["conversion_ratio"])) for r in orders)
    tails = sum(bool(r["base_quantity"] % (capacities[r["owner_code"], r["sku_id"]] * r["conversion_ratio"])) for r in orders)
    empty = set(layout.slots) - occupied.keys()
    allow_mix = groups > len(empty) or tails > sum(layout.slots[s]["level"] == 1 for s in empty)
    by_slot = defaultdict(list)
    for row in assigned:
        slot = row["slot_id"]
        require(slot in layout.slots, "未知货位")
        require(row["base_quantity"] > 0 and row["boxes"] == row["base_quantity"] / row["conversion_ratio"], "新增数量无效")
        require(row["tail"] == (row["boxes"] < capacities[row["owner_code"], row["sku_id"]]), "尾托标志错误")
        require(row["tail"] or layout.slots[slot]["level"] > 1, "整托进入一层")
        require(row["appended"] == (slot in occupied), "追加标志错误")
        by_slot[slot].append(row)
    for slot, rows in by_slot.items():
        old = occupied.get(slot, [])
        all_rows = old + rows
        mixed = len({r["sku_id"] for r in all_rows}) > 1
        require(all(r["mixed"] == mixed for r in rows), "混托标志错误")
        require(len({r["owner_code"] for r in all_rows}) == 1, "跨货主混托")
        require(sum(r["boxes"] for r in all_rows) <= min(capacities[r["owner_code"], r["sku_id"]] for r in all_rows), "托容量超限")
        if old or len(rows) > 1:
            require(allow_mix and layout.slots[slot]["level"] == 1 and all(r["tail"] for r in rows), "非法混托/追加触发或层位")
            require(len({r["sku_id"] for r in rows}) == len(rows), "新增同SKU尾托合并")
        if old:
            require(len({r["sku_id"] for r in old}) == 1, "向已有混托追加")
            require(sum(r["boxes"] for r in old) < capacities[old[0]["owner_code"], old[0]["sku_id"]], "向已有满托追加")
    require(len(result["legs"]) == len(by_slot) and {leg["end"] for leg in result["legs"]} == set(by_slot), "搬运次数不一致")
    require(all(leg == layout.leg("STAGING", leg["end"]) for leg in result["legs"]), "路径不一致")
    require(abs(result["total_distance"] - sum(layout.leg("STAGING", s)["distance"] for s in by_slot)) < 1e-7, "距离不一致")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="场景JSON对象或场景数组；默认内置需求场景")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--time-limit", type=float, default=60)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--mip-gap", type=float, default=0.005)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    folder = args.output or root / "results/testing" / datetime.now().strftime("copt_inbound_%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True, exist_ok=True)
    reports = []
    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        config = SolverSettings(backend="copt", time_limit_s=args.time_limit, threads=args.threads, mip_gap=args.mip_gap).model_dump()
        cases = json.loads((args.input or root / "data/testing/inbound_requirement_cases.json").read_text(encoding="utf-8-sig"))
        if args.input is None:
            cases += objective_cases()
        if isinstance(cases, dict):
            cases = [cases]
        if not isinstance(cases, list) or not cases:
            raise ValueError("输入必须为非空场景对象或数组")
        for index, case in enumerate(cases):
            target = folder / f"{index:03d}"
            target.mkdir(exist_ok=True)
            save(target / "input.json", dict(case=case, config=config))
            report = dict(id=case.get("id", str(index)), passed=False)
            result = None
            try:
                weight = float(case.get("layer_weight", 1))
                if not math.isfinite(weight) or weight < 0:
                    raise ValueError("层间距离权重必须是非负有限数值")
                layout = WarehouseLayout(weight)
                stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
                if "slots" in case:
                    layout.slots = {s: layout.slots[s] for s in case["slots"]}
                if any(r["base_quantity"] and not r.get("staging") and r["slot_id"] not in layout.slots for r in stock):
                    raise ValueError("受限布局缺少已有库存货位")
                original = deepcopy((stock, orders, caps))
                result = solve(stock, orders, caps, layout, {tuple(g[:3]): g[3] for g in case.get("grades", [])}, config)
                save(target / "result.json", dict(result, backend="copt", config=config))
                if original != (stock, orders, caps):
                    raise ValueError("算法修改了输入")
                validate(stock, orders, caps, layout, result)
                expected = case.get("copt_expected", {})
                boxes = sum((r["boxes"] for r in result["assignments"]), Decimal(0))
                if "assigned_boxes" in expected and boxes != Decimal(str(expected["assigned_boxes"])):
                    raise ValueError("上架箱数不符合期望")
                if "total_distance" in expected and abs(result["total_distance"] - float(expected["total_distance"])) > 1e-7:
                    raise ValueError("距离不符合期望")
                report.update(passed=True, status=result["status"], assigned_boxes=str(boxes), stages=result["solver_stages"])
            except Exception as error:
                report["error"] = str(error)
                if result is None:
                    save(target / "result.json", dict(status="failed", message=str(error), backend="copt"))
            reports.append(report)
            print(f"{'PASS' if report['passed'] else 'FAIL'} {report['id']}: {report.get('error', report.get('status'))}")
    except Exception as error:
        reports.append(dict(id="setup", passed=False, error=str(error)))
    save(folder / "summary.json", reports)
    print(f"Results: {folder.resolve()}")
    return 0 if reports and all(r["passed"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
