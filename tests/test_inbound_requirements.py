"""需求规则、边界及有限组合穷举；数据可独立重放。"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal as D
import itertools
import json
from pathlib import Path
from time import monotonic

import pytest

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from solvers.warehouse_inbound_heuristic import solve
from tools.generate_inbound_test_data import row, scenarios


CASES = json.loads((Path(__file__).resolve().parents[1] / "data/testing/inbound_requirement_cases.json").read_text(encoding="utf-8"))


def execute(case):
    layout = WarehouseLayout()
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in case["slots"]}
    grades = {tuple(g[:3]): g[3] for g in case["grades"]}
    original = deepcopy((stock, orders, caps))
    result = solve(stock, orders, caps, layout, grades, monotonic() + 10)
    assert (stock, orders, caps) == original, "算法修改了输入快照"
    assert result["remaining_inventory"][:len(stock)] == stock, "搬动或修改了已有库存"
    quantities = lambda rows: sum((r["base_quantity"] for r in rows), D(0))
    assert quantities(orders) == quantities(result["assignments"]) + quantities(result["unassigned"])
    # 按货主、SKU、订单、批次及时间逐项守恒，避免总量相等掩盖错货。
    def totals(rows):
        values = defaultdict(D)
        for r in rows:
            values[tuple(r.get(k) for k in ("owner_code", "sku_id", "order_no", "batch_no", "timestamp"))] += r["base_quantity"]
        return {k: v for k, v in values.items() if v}
    assert totals(orders) == totals(result["assignments"] + result["unassigned"])
    assert result["status"] == ("partial" if result["unassigned"] else "feasible")
    by_slot = defaultdict(list)
    for r in result["remaining_inventory"]:
        if r["base_quantity"] and not r.get("staging"):
            by_slot[r["slot_id"]].append(r)
    for slot, rows in by_slot.items():
        assert len({r["owner_code"] for r in rows}) == 1
        assert sum(r["boxes"] for r in rows) <= min(caps[r["owner_code"], r["sku_id"]] for r in rows)
    for r in result["assignments"]:
        assert r["slot_id"] in layout.slots
        assert r["tail"] or r["level"] > 1
        assert not r["mixed"] or r["level"] == 1
    assert all(leg["start"] == "STAGING" for leg in result["legs"])
    assert result["total_distance"] == pytest.approx(sum(layout.leg("STAGING", leg["end"])["distance"] for leg in result["legs"]))
    expected, assigned = case["expected"], result["assignments"]
    if "levels" in expected:
        assert [r["level"] for r in assigned] == expected["levels"]
    if "assigned_slots" in expected:
        assert [r["slot_id"] for r in assigned] == expected["assigned_slots"]
    if "sku_levels" in expected:
        assert {r["sku_id"]: r["level"] for r in assigned} == expected["sku_levels"]
    if "count" in expected:
        assert len(assigned) == expected["count"]
    if "tails" in expected:
        assert sum(r["tail"] for r in assigned) == expected["tails"]
    if "mixed" in expected:
        assert assigned and all(r["mixed"] == expected["mixed"] for r in assigned)
    if "unassigned" in expected:
        assert quantities(result["unassigned"]) == D(expected["unassigned"])
    if expected.get("warning"):
        assert result["warnings"]
    if result["unassigned"]:
        assert result["message"] == "库位不足，需要移库"
    return result


def test_generated_data_is_current():
    assert CASES == scenarios()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_requirement_case(case):
    execute(case)


@pytest.mark.parametrize("grade,quantity,levels", list(itertools.product(
    ["高", "中", "低"], ["0.1", "39.999", "40", "40.001", "80"],
    [list(levels) for n in range(1, 6) for levels in itertools.combinations(range(1, 6), n)])))
def test_single_sku_exhaustive_floor_subsets(grade, quantity, levels):
    case = deepcopy(CASES[0])
    case.update(orders=[row(qty=quantity)], slots=[f"C15-1-{level}" for level in levels],
                grades=[["WH", "O", "A", grade]], expected={})
    result = execute(case)
    # 独立算每个拆托顺序的合法层与最短距离，不调用求解器内部排序。
    remaining = set(levels)
    chunks = [D(40)] * int(D(quantity) // 40)
    if D(quantity) % 40:
        chunks.insert(0, D(quantity) % 40)
    layout = WarehouseLayout()
    chosen = []
    for q in chunks:
        legal = remaining if q < 40 else remaining - {1}
        preferred = ({1} if q < 40 else {2} if grade == "高" else {2, 3, 4} if grade == "中" else {5}) & legal
        if preferred or legal:
            level = min(preferred or legal, key=lambda l: layout.leg("STAGING", f"C15-1-{l}")["distance"])
            chosen.append(level)
            remaining.remove(level)
    assert [r["level"] for r in result["assignments"]] == chosen


@pytest.mark.parametrize("field,value", [(f, v) for f in ("base_quantity", "conversion_ratio")
    for v in ("-1", "NaN", "Infinity", "bad", "")] + [("conversion_ratio", "0")])
def test_invalid_numeric_input(field, value):
    case = deepcopy(CASES[0])
    case["orders"][0][field] = value
    with pytest.raises(ValueError, match="数值无效"):
        execute(case)


@pytest.mark.parametrize("field", ["warehouse_code", "owner_code", "sku_id", "timestamp", "base_unit", "order_no"])
def test_required_fields(field):
    case = deepcopy(CASES[0])
    case["orders"][0][field] = ""
    with pytest.raises(ValueError, match="缺少"):
        execute(case)


@pytest.mark.parametrize("cap", ["", "0", "-1", "NaN", "Infinity"])
def test_missing_master_capacity(cap):
    case = deepcopy(CASES[0])
    for spec in case["specs"]:
        spec["托含量"] = cap
    with pytest.raises(ValueError, match="请先维护该sku主数据信息"):
        execute(case)


def test_timeout_and_unreachable():
    case = deepcopy(CASES[0])
    layout = WarehouseLayout()
    stock, orders, caps = prepare([], case["orders"], case["specs"], layout, "slotting")
    with pytest.raises(TimeoutError):
        solve(stock, orders, caps, layout, {}, monotonic() - 1)
    layout.graph["STAGING"].clear()
    with pytest.raises(ValueError, match="不可达"):
        solve(stock, orders, caps, layout, {}, monotonic() + 10)


@pytest.mark.parametrize("kind", ["empty_orders", "warehouse", "unknown_slot", "cross_owner_stock",
    "stock_over_capacity", "ratio_mismatch", "unit_mismatch", "available_exceeds_stock", "invalid_time", "timezone"])
def test_invalid_consistency(kind):
    case = deepcopy(CASES[0])
    case["inventory"] = [row()]
    if kind == "empty_orders":
        case["orders"] = []
    elif kind == "warehouse":
        case["inventory"][0]["warehouse_code"] = "OTHER"
    elif kind == "unknown_slot":
        case["inventory"][0]["slot_id"] = "UNKNOWN"
    elif kind == "cross_owner_stock":
        case["inventory"].append(row(owner="P"))
    elif kind == "stock_over_capacity":
        case["inventory"][0]["base_quantity"] = "40.001"
    elif kind == "ratio_mismatch":
        case["inventory"][0]["conversion_ratio"] = "6"
    elif kind == "unit_mismatch":
        case["inventory"][0]["base_unit"] = "个"
    elif kind == "available_exceeds_stock":
        case["inventory"][0]["available_base_quantity"] = "11"
    elif kind == "invalid_time":
        case["orders"][0]["timestamp"] = "not-a-date"
    else:
        case["orders"][0]["timestamp"] = "2026-10-01T00:00:00+08:00"
    with pytest.raises(ValueError):
        execute(case)


@pytest.mark.parametrize("weight", [0, 0.5, 1, 3])
def test_known_single_trip_distance_and_layer_weight(weight):
    layout = WarehouseLayout(weight)
    stock, orders, caps = prepare([], [row(qty=80)], CASES[0]["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in ("C15-1-2", "C15-2-2")}
    result = solve(stock, orders, caps, layout, {}, monotonic() + 10)
    # 每托 STAGING 到地面停靠点为 6.95/8.10 米，加一层1.7米；不计返程。
    assert result["total_distance"] == pytest.approx(6.95 + 8.10 + 2 * 1.7 * weight)
    assert len(result["legs"]) == 2


def test_fractional_boxes_preserve_exact_base_units():
    case = deepcopy(CASES[0])
    case.update(orders=[{**row(qty=1), "conversion_ratio": "6", "quantity": "0.167"}],
                slots=["C15-1-1"], expected={"levels": [1]})
    result = execute(case)
    assert result["assignments"][0]["base_quantity"] == D(1)
    assert result["assignments"][0]["boxes"] == D(1) / 6


def test_nearest_append_target_and_original_timestamps():
    case = deepcopy(CASES[0])
    case.update(inventory=[row(slot="C15-45-1"), row(slot="C15-1-1")],
                orders=[row("B", 1)], slots=["C15-45-1", "C15-1-1"],
                expected={"assigned_slots": ["C15-1-1"]})
    assert execute(case)["assignments"][0]["appended"]


def test_grade_is_scoped_by_warehouse_owner_and_sku():
    case = deepcopy(CASES[0])
    case.update(grades=[["OTHER", "O", "A", "高"], ["WH", "P", "A", "高"]], expected={"levels": [5]})
    execute(case)
