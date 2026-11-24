from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from modules.slotting.inbound_model import aggregate_groups
from solvers import warehouse_inbound_copt, warehouse_inbound_ortools
from tests.test_inbound_copt import engine
from tools.generate_inbound_test_data import scenarios, row
from tools.test_inbound_copt import validate


def run(case, solver, aggregate, read_mode="scalar", objectives="sequential", distance_cache=None):
    layout = WarehouseLayout(case.get("layer_weight", 1))
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in case["slots"]}
    original = deepcopy((stock, orders, caps))
    kwargs = dict(use_matching=False) if solver is warehouse_inbound_ortools else {}
    if distance_cache is not None:
        kwargs["distance_cache"] = distance_cache
    result = solver.solve(stock, orders, caps, layout, {tuple(g[:3]): g[3] for g in case["grades"]},
                          dict(time_limit_s=10, threads=1, mip_gap=0, aggregate_full=aggregate,
                               copt_read_mode=read_mode, copt_objectives=objectives,
                               copt_distance_cache="batch" if distance_cache is not None else "off"), **kwargs)
    assert original == (stock, orders, caps)
    validate(stock, orders, caps, layout, result)
    assert result["optimality"] == "optimal"
    return result


@pytest.mark.parametrize("solver", [warehouse_inbound_copt, warehouse_inbound_ortools])
@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["id"])
def test_all_business_cases_equivalent(engine, solver, case):
    before, after = run(case, solver, False), run(case, solver, True)
    assert [s["name"] for s in after["solver_stages"]] == ["quantity", "distance", "preference"]
    assert [s["objective"] for s in after["solver_stages"]] == pytest.approx(
        [s["objective"] for s in before["solver_stages"]], abs=1e-7)


@pytest.mark.parametrize("solver", [warehouse_inbound_copt, warehouse_inbound_ortools])
@pytest.mark.parametrize("slot_count", [0, 1, 3, 6])
def test_aggregate_multi_order_full_pallets_and_preserve_details(engine, solver, slot_count):
    case = scenarios()[0]
    case.update(orders=[row(qty=80, batch_no="A"), {**row(qty=120, batch_no="B"), "order_no": "IN-2"}],
                slots=[f"C15-{i + 1}-2" for i in range(slot_count)], grades=[])
    before, after = run(case, solver, False), run(case, solver, True)
    stats = after["model_stats"]
    assert stats["original_groups"] == 5
    assert stats["model_groups"] == 1
    assert stats["assignment_variables"] == slot_count
    assert stats["baseline_assignment_variables"] == 5 * slot_count
    assert stats["removed_assignment_variables"] == 4 * slot_count
    assert [s["objective"] for s in after["solver_stages"]] == pytest.approx(
        [s["objective"] for s in before["solver_stages"]])
    assert len(after["assignments"]) == min(slot_count, 5)
    assert {r["order_no"] for r in after["assignments"] + after["unassigned"]} == {"IN-1", "IN-2"}


def test_grouping_excludes_tails_and_non_equivalent_full_pallets():
    def group(**changes):
        return dict(row=dict(warehouse_code="W", owner_code="O", sku_id="A", base_quantity=Decimal(40),
                             boxes=Decimal(40), conversion_ratio=Decimal(1), base_unit="瓶"),
                    tail=False, cap=Decimal(40), grade="低", **changes)
    same = group()
    groups = [same, deepcopy(same)]
    for field, value in (("owner_code", "P"), ("sku_id", "B"), ("conversion_ratio", Decimal(2)), ("base_unit", "个")):
        changed = deepcopy(same)
        changed["row"][field] = value
        groups.append(changed)
    changed = deepcopy(same)
    changed["grade"] = "高"
    groups.append(changed)
    tail = deepcopy(same)
    tail["tail"] = True
    groups.extend([tail, deepcopy(tail)])
    layout = SimpleNamespace(slots={"S": dict(level=2)})
    original = deepcopy(groups)
    modeled = aggregate_groups(groups, {"S"}, layout, True, float("inf"))
    assert groups == original
    assert len(modeled) == len(groups) - 1
    assert modeled[0]["members"] == [0, 1]


@pytest.mark.parametrize("solver", [warehouse_inbound_copt, warehouse_inbound_ortools])
def test_aggregation_with_mix_and_nonzero_layer_weight(engine, solver):
    case = scenarios()[0]
    case.update(orders=[row(qty=81), row("B", 1)], slots=["C15-1-1", "C15-1-2", "C15-2-2"], grades=[], layer_weight=3)
    before, after = run(case, solver, False), run(case, solver, True)
    assert after["allow_mix"]
    assert after["model_stats"]["model_groups"] == 3
    assert [s["objective"] for s in after["solver_stages"]] == pytest.approx(
        [s["objective"] for s in before["solver_stages"]])
