"""出库需求的独立选货核对、路线不变量及有限组合穷举。"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal as D
from fractions import Fraction as F
import itertools
import json
from pathlib import Path
from time import monotonic

import pytest

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from modules.picking_path.planning_service import plan as dispatch
from solvers.warehouse_outbound_heuristic import solve
from tools.benchmark_outbound_routes import SAMPLES, evaluate
from tools.generate_outbound_test_data import row, scenarios


CASES = json.loads((Path(__file__).resolve().parents[1] / "data/testing/outbound_requirement_cases.json").read_text(encoding="utf-8"))


def fifo_oracle(stock, orders, caps, layout):
    """用有理数按业务规则分配可用库存，完全不参与路线构造。"""
    physical = defaultdict(list)
    for i, r in enumerate(stock):
        if not r.get("staging") and r["base_quantity"] > 0:
            physical[r["slot_id"]].append(i)
    tails = {}
    for slot, indexes in physical.items():
        skus = {stock[i]["sku_id"] for i in indexes}
        relevant = any(any(o["owner_code"] == stock[i]["owner_code"] and o["sku_id"] == stock[i]["sku_id"] for o in orders) for i in indexes)
        if relevant:
            r = stock[indexes[0]]
            tails[slot] = len(skus) > 1 or sum(F(stock[i]["base_quantity"]) / F(stock[i]["conversion_ratio"]) for i in indexes) < F(caps[r["owner_code"], r["sku_id"]])
    available = [F(r.get("available_base", 0)) for r in stock]
    picked, missing = defaultdict(F), defaultdict(F)
    for order in orders:
        key = (order["order_no"], order["owner_code"], order["sku_id"])
        need = F(order["base_quantity"])
        candidates = [i for ids in physical.values() for i in ids
                      if (stock[i]["owner_code"], stock[i]["sku_id"]) == key[1:]]
        candidates.sort(key=lambda i: (0 if tails[stock[i]["slot_id"]] else 1, stock[i]["timestamp"],
                                       layout.leg("STAGING", stock[i]["slot_id"])["distance"], stock[i]["slot_id"], i))
        for i in candidates:
            take = min(need, available[i])
            if take:
                picked[key + (i,)] += take
                available[i] -= take
                need -= take
        if need:
            missing[key] += need
    return dict(picked), dict(missing)


def execute(case):
    layout = WarehouseLayout(case.get("layer_weight", 1))
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "picking_path")
    original = deepcopy((stock, orders, caps))
    result = solve(stock, orders, caps, layout, case["storeBatchFlag"], monotonic() + 10)
    assert original == (stock, orders, caps), "修改了输入快照"
    validate_result(case, result, stock, orders, caps, layout)
    return result


def validate_result(case, result, stock, orders, caps, layout, *, check_route_expectations=True):
    oracle_picks, oracle_shortages = fifo_oracle(stock, orders, caps, layout)
    picked, missing, by_source = defaultdict(F), defaultdict(F), defaultdict(F)
    for p in result["picks"]:
        source = stock[p["inventory_index"]]
        assert p["base_quantity"] > 0 and p["boxes"] > 0
        assert all(p[k] == source.get(k) for k in ("owner_code", "sku_id", "slot_id", "timestamp", "base_unit", "batch_no") if k in source)
        picked[(p["order_no"], p["owner_code"], p["sku_id"], p["inventory_index"])] += F(p["base_quantity"])
        by_source[p["inventory_index"]] += F(p["base_quantity"])
        assert abs(p["boxes"] - p["base_quantity"] / p["conversion_ratio"]) <= D("1e-24")
    for s in result["shortages"]:
        assert {"order_no", "owner_code", "sku_code", "sku_name", "requested_quantity", "fulfilled_quantity", "shortage_quantity", "unit"} <= s.keys()
        assert s["requested_quantity"] == s["fulfilled_quantity"] + s["shortage_quantity"]
        assert s["shortage_quantity"] > 0
        missing[s["order_no"], s["owner_code"], s["sku_code"]] += F(s["shortage_quantity"])
    assert dict(picked) == oracle_picks, "尾板/FIFO/货主或可用数量不符合需求"
    assert dict(missing) == oracle_shortages
    assert result["status"] == ("partial" if missing else "feasible")
    assert len(stock) == len(result["remaining_inventory"])
    for i, (before, after) in enumerate(zip(stock, result["remaining_inventory"])):
        assert F(before["base_quantity"]) - by_source[i] == F(after["base_quantity"])
        assert after["base_quantity"] >= 0
        assert all(after.get(k) == before.get(k) for k in ("slot_id", "sku_id", "owner_code", "timestamp", "batch_no", "base_unit"))
        if "available_base" in before:
            assert F(before["available_base"]) - by_source[i] == F(after["available_base"])
            assert 0 <= after["available_base"] <= after["base_quantity"]
    physical = defaultdict(list)
    for r in result["remaining_inventory"]:
        if not r.get("staging") and r["slot_id"] and any(s["slot_id"] == r["slot_id"] and s["base_quantity"] > 0 for s in stock):
            physical[r["slot_id"]].append(r)
    assert set(result["cleared_slots"]) == {s for s, rs in physical.items() if all(r["base_quantity"] == 0 for r in rs)}
    flattened = []
    for number, trip in enumerate(result["trips"], 1):
        assert trip["legs"][0]["start"] == "STAGING" and trip["legs"][-1]["end"] == "STAGING"
        assert trip["legs"][-1]["action"] == "unload"
        assert {leg["owner_code"] for leg in trip["legs"]} == {trip["owner_code"]}
        load, limit, current = F(0), None, "STAGING"
        details = [p for p in result["picks"] if p["trip"] == number]
        assert len(details) == sum(l["action"] == "pick" for l in trip["legs"])
        if case["storeBatchFlag"] == 0:
            assert len({p["order_no"] for p in details}) == 1
        detail_iter = iter(details)
        for leg in trip["legs"]:
            assert leg["start"] == current
            expected_leg = layout.leg(leg["start"], leg["end"])
            assert leg["distance"] == pytest.approx(expected_leg["distance"])
            assert leg["path"] == expected_leg["path"]
            assert abs(F(leg["load_before"]) - load) <= F(D("1e-24"))
            if leg["action"] == "pick":
                p = next(detail_iter)
                assert p["slot_id"] == leg["end"] and p["sku_id"] == leg["sku_id"] and p["order_no"] == leg["order_no"]
                cap = F(caps[p["owner_code"], p["sku_id"]])
                limit = min(limit, cap) if limit is not None else cap
                load += F(p["base_quantity"]) / F(p["conversion_ratio"])
                assert load <= limit, "按实际主单位数量换算后超载"
                assert F(leg["capacity"]) == limit
                assert abs(F(leg["load_after"]) - load) <= F(D("1e-24"))
            else:
                assert leg["load_after"] == 0
                assert abs(F(trip["loaded_boxes"]) - load) <= F(D("1e-24"))
            current = leg["end"]
        assert trip["distance"] == pytest.approx(sum(l["distance"] for l in trip["legs"]))
        flattened.extend(trip["legs"])
    assert flattened == result["legs"]
    assert result["total_distance"] == pytest.approx(sum(t["distance"] for t in result["trips"]))
    e = case.get("expected", {})
    for field, rows, column in (("fulfilled", result["picks"], "base_quantity"), ("shortage", result["shortages"], "shortage_quantity")):
        if field in e:
            assert sum((r[column] for r in rows), D(0)) == D(e[field])
    if "sources" in e:
        assert {str(i): v for i, v in by_source.items() if v} == {k: F(v) for k, v in e["sources"].items()}
    if check_route_expectations and "trips" in e:
        assert len(result["trips"]) == e["trips"]
    if check_route_expectations and "trip_loads" in e:
        assert [t["loaded_boxes"] for t in result["trips"]] == [D(q) for q in e["trip_loads"]]
    if "cleared" in e:
        assert set(result["cleared_slots"]) == set(e["cleared"])
    if check_route_expectations and "first_slot" in e:
        assert result["picks"][0]["slot_id"] == e["first_slot"]
    if check_route_expectations and "distance" in e:
        assert result["total_distance"] == pytest.approx(e["distance"])
    if check_route_expectations and "max_distance" in e:
        assert result["total_distance"] <= e["max_distance"] + 1e-9
    if "shortage_orders" in e:
        assert [s["order_no"] for s in result["shortages"]] == e["shortage_orders"]
    if e.get("warning"):
        assert result["warnings"]


def test_generated_data_is_current():
    assert CASES == scenarios()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_business_scenarios(case):
    execute(case)


@pytest.mark.parametrize("qty1,qty2,mask,need,time_order,flag", list(itertools.product(
    [0, 2, 5], [0, 2, 5], range(4), [0, 1, 4, 5, 6, 10, 11], [-1, 0, 1], [0, 1])))
def test_fifo_availability_combinations(qty1, qty2, mask, need, time_order, flag):
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=qty1, slot="C15-3-1", timestamp="2026-01-02"),
                           row(qty=qty2, timestamp={-1: "2026-01-01", 0: "2026-01-02", 1: "2026-01-03"}[time_order])],
                orders=[row(qty=need)], storeBatchFlag=flag, expected={})
    case["specs"] = [dict(编码="A", 所属客户="O", 托含量="5")]
    for i, stock in enumerate(case["inventory"]):
        stock["available_base_quantity"] = stock["base_quantity"] if mask & (1 << i) else "0"
    execute(case)


CAPACITY_COMBINATIONS = [(a, b, x, y, owner, flag) for a, b in itertools.product([2, 3, 5], repeat=2)
                         for x in range(1, a + 1) for y in range(1, b + 1)
                         for owner, flag in (("O", 0), ("O", 1), ("P", 1))]


@pytest.mark.parametrize("cap_a,cap_b,qty_a,qty_b,owner,flag", CAPACITY_COMBINATIONS)
def test_two_sku_capacity_and_owner_combinations(cap_a, cap_b, qty_a, qty_b, owner, flag):
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=qty_a), row("B", qty_b, "C15-2-1", owner=owner)],
                orders=[row(qty=qty_a), row("B", qty_b, owner=owner)], storeBatchFlag=flag, expected={})
    case["specs"] = [dict(编码="A", 所属客户="O", 托含量=str(cap_a)), dict(编码="B", 所属客户=owner, 托含量=str(cap_b))]
    result = execute(case)
    can_share = owner == "O" and qty_a + qty_b <= min(cap_a, cap_b)
    assert [t["loaded_boxes"] for t in result["trips"]] == ([D(qty_a + qty_b)] if can_share else [D(qty_a), D(qty_b)])


@pytest.mark.parametrize("ratio,need", list(itertools.product([3, 6, 7], [1, 2, 5, 7, 40, 41, 119])))
def test_exact_base_units_across_multiple_sources(ratio, need):
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=1), row(qty=119, slot="C15-2-1")], orders=[row(qty=need)], expected={})
    for r in case["inventory"] + case["orders"]:
        r["conversion_ratio"] = str(ratio)
    case["specs"] = [dict(编码="A", 所属客户="O", 托含量="40")]
    execute(case)


@pytest.mark.parametrize("flag", [None, True, False, "0", "1", -1, 2, 0.0, 1.0])
def test_invalid_mode_is_rejected(flag):
    with pytest.raises(ValueError, match="storeBatchFlag"):
        dispatch([], [], {}, WarehouseLayout(), {"storeBatchFlag": flag}, {"time_limit_s": 10})


def test_per_order_multiple_owners_is_rejected():
    case = deepcopy(CASES[0])
    case.update(inventory=[], orders=[row(owner="O"), row(owner="P")], storeBatchFlag=0)
    with pytest.raises(ValueError, match="同一货主"):
        execute(case)


@pytest.mark.parametrize("field,value", [(field, value) for field in ("base_quantity", "conversion_ratio", "available_base_quantity")
    for value in ("-1", "NaN", "Infinity", "bad")] + [("conversion_ratio", "0"), ("available_base_quantity", "11")])
def test_invalid_inventory_numbers(field, value):
    case = deepcopy(CASES[0])
    case.update(inventory=[row()], orders=[row(qty=1)])
    case["inventory"][0][field] = value
    with pytest.raises(ValueError):
        execute(case)


@pytest.mark.parametrize("kind", ["empty_orders", "missing_available", "unknown_slot", "missing_order_no", "missing_owner",
    "unit_mismatch", "ratio_mismatch", "cross_owner_slot", "overcapacity", "warehouse_mismatch", "invalid_timestamp",
    "missing_spec", "ambiguous_spec"])
def test_invalid_data_consistency(kind):
    case = deepcopy(CASES[0])
    case.update(inventory=[row()], orders=[row(qty=1)])
    if kind == "empty_orders":
        case["orders"] = []
    elif kind == "missing_available":
        del case["inventory"][0]["available_base_quantity"]
    elif kind == "unknown_slot":
        case["inventory"][0]["slot_id"] = "BAD"
    elif kind == "missing_order_no":
        case["orders"][0]["order_no"] = ""
    elif kind == "missing_owner":
        case["orders"][0]["owner_code"] = ""
    elif kind == "unit_mismatch":
        case["orders"][0]["base_unit"] = "套"
    elif kind == "ratio_mismatch":
        case["orders"][0]["conversion_ratio"] = "6"
    elif kind == "cross_owner_slot":
        case["inventory"].append(row(owner="P"))
    elif kind == "overcapacity":
        case["inventory"][0]["base_quantity"] = "40.001"
    elif kind == "warehouse_mismatch":
        case["orders"][0]["warehouse_code"] = "OTHER"
    elif kind == "invalid_timestamp":
        case["orders"][0]["timestamp"] = "not-a-date"
    elif kind == "missing_spec":
        case["specs"] = []
    else:
        case["specs"].append(dict(编码="A", 所属客户="O", 托含量="41"))
    with pytest.raises(ValueError):
        execute(case)


def test_available_box_fallback_preserves_exact_base():
    case = deepcopy(CASES[0])
    case.update(inventory=[{**row(qty=1), "available_base_quantity": "", "available_quantity": "0.167", "quantity": "0.167", "conversion_ratio": "6"}],
                orders=[{**row(qty=1), "conversion_ratio": "6"}], expected={"fulfilled": "1"})
    execute(case)


@pytest.mark.parametrize("weight", [0, 0.5, 1, 3])
def test_known_route_distance_with_layer_weight(weight):
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=1, slot="C15-1-3")], orders=[row(qty=1)], layer_weight=weight,
                expected={"distance": 13.9 + 6.8 * weight})
    execute(case)


@pytest.mark.parametrize("stage", ["selection", "routing"])
def test_timeout_raises_without_partial_plan(stage, monkeypatch):
    from solvers import warehouse_outbound_heuristic as backend
    layout = WarehouseLayout()
    stock, orders, caps = prepare([row()], [row()], CASES[0]["specs"], layout, "picking_path")
    clock = iter([2] if stage == "selection" else [0, 2])
    monkeypatch.setattr(backend, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="时间上限"):
        solve(stock, orders, caps, layout, 1, 1)


def test_unreachable_route_fails():
    layout = WarehouseLayout()
    stock, orders, caps = prepare([row()], [row()], CASES[0]["specs"], layout, "picking_path")
    layout.graph["STAGING"].clear()
    with pytest.raises(ValueError, match="不可达"):
        solve(stock, orders, caps, layout, 1, monotonic() + 10)


@pytest.mark.parametrize("ratio_a,ratio_b", [(3, 7), (7, 3)])
def test_mixed_ratios_at_nonterminating_base_split(ratio_a, ratio_b):
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=1), row("B", 1, "C15-2-1"), row(qty=ratio_a, slot="C15-3-1")],
                orders=[row(qty=ratio_a + 1), row("B", 1)], expected={"trips": 2})
    case["specs"] = [dict(编码=sku, 所属客户="O", 托含量="1") for sku in ("A", "B")]
    for r in case["inventory"] + case["orders"]:
        r["conversion_ratio"] = str(ratio_a if r["sku_id"] == "A" else ratio_b)
    execute(case)


def test_fractional_full_load_has_no_phantom_pick():
    result = execute(next(c for c in CASES if c["id"] == "fractional_exact_full_load"))
    assert len(result["picks"]) == 4
    assert all(p["base_quantity"] == 1 for p in result["picks"])


@pytest.mark.parametrize("slots", SAMPLES)
def test_distance_against_small_exact_route_oracle(slots):
    quality = evaluate(slots)
    assert len(quality["result"]["trips"]) == 1
    assert len(quality["result"]["picks"]) == len(slots)
    assert quality["heuristic_distance"] + 1e-9 >= quality["optimal_distance"]


def test_tiny_real_capacity_overflow_is_not_rounded_away():
    case = deepcopy(CASES[0])
    case.update(inventory=[row(qty=1), row(qty="0.0000000000000000000000000001")], orders=[row(qty=1)])
    case["specs"] = [dict(编码="A", 所属客户="O", 托含量="1")]
    with pytest.raises(ValueError, match="超过单托容量"):
        execute(case)
