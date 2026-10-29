from decimal import Decimal as D
import json
from time import monotonic

import pytest

from core.warehouse_layout import WarehouseLayout
from apps.warehouse_service import plan
from apps.warehouse_data import prepare
from solvers.warehouse_inbound_heuristic import solve as inbound
from solvers.warehouse_outbound_heuristic import solve as outbound


def row(sku="A", qty=10, slot="C15-1-1", owner="O", timestamp="2026-01-01", order="SO"):
    return dict(sku_id=sku, name=sku, owner_code=owner, owner_name=owner, warehouse_code="WH",
                timestamp=timestamp, order_no=order, base_quantity=str(qty), base_unit="瓶",
                conversion_ratio="1", quantity=str(qty), unit="箱", slot_id=slot,
                staging=False, available_base_quantity=str(qty))


def prepared_inputs(inventory, orders, caps=None):
    layout = WarehouseLayout()
    caps = caps or {"A": 40, "B": 60}
    specs = [dict(编码=sku, 所属客户=owner, 货品名称=sku, 托含量=str(cap)) for owner in ("O", "P") for sku, cap in caps.items()]
    inv, demand, capacities = prepare(inventory, orders, specs, layout, "picking_path")
    return inv, demand, capacities, layout


def pick(inventory, orders, caps=None, flag=1):
    inv, demand, capacities, layout = prepared_inputs(inventory, orders, caps)
    return outbound(inv, demand, capacities, layout, flag, monotonic() + 10)


def test_inbound_only_requires_master_data_for_current_orders():
    specs = [dict(编码="A", 所属客户="O", 托含量="40")]
    layout = WarehouseLayout()
    inv, orders, caps = prepare([row(sku="UNRELATED")], [row()], specs, layout, "slotting")
    result = inbound(inv, orders, caps, layout, {}, monotonic() + 10)
    assert result["status"] == "feasible"
    assert result["assignments"][0]["slot_id"] != inv[0]["slot_id"]
    with pytest.raises(ValueError, match="请先维护该sku主数据信息"):
        prepare(inv, [row(sku="MISSING")], specs, layout, "slotting")


@pytest.mark.parametrize("shared_slot", [False, True])
def test_outbound_only_requires_master_data_for_current_orders(shared_slot):
    specs = [dict(编码="A", 所属客户="O", 托含量="40")]
    layout = WarehouseLayout()
    stock = [row(), row("UNRELATED", slot="C15-1-1" if shared_slot else "C15-2-1")]
    inv, orders, caps = prepare(stock, [row(qty=1)], specs, layout, "picking_path")
    result = outbound(inv, orders, caps, layout, 1, monotonic() + 10)
    assert result["status"] == "feasible"
    assert {r["sku_id"] for r in result["picks"]} == {"A"}
    assert result["remaining_inventory"][1]["base_quantity"] == D(10)
    assert caps == {("O", "A"): D(40)}
    with pytest.raises(ValueError, match="缺少有效SKU托含量"):
        prepare(stock, [row("UNRELATED")], specs, layout, "picking_path")


@pytest.mark.parametrize("sku", ["A", "B"])
def test_outbound_existing_upper_floor_mix_is_warning(sku):
    stock = [row("A", slot="C15-11-2"), row("B", slot="C15-11-2"),
             row("A", slot="C15-2-1")]
    result = pick(stock, [row(sku, qty=1)])
    assert result["status"] == "feasible"
    assert sum(r["base_quantity"] for r in result["picks"]) == D(1)
    assert any("C15-11-2" in warning for warning in result["warnings"])
    assert sum(r["base_quantity"] for r in result["remaining_inventory"]) == D(29)


def test_fifo_tail_before_full_and_not_route_order():
    result = pick([row(qty=40, slot="C15-1-2", timestamp="2025-01-01"),
                   row(qty=10, slot="C14-45-1", timestamp="2026-01-01"),
                   row(qty=10, slot="C15-1-1", timestamp="2026-02-01")], [row(qty=15)])
    quantities = {r["slot_id"]: r["base_quantity"] for r in result["picks"]}
    assert quantities == {"C14-45-1": D(10), "C15-1-1": D(5)}
    assert result["picks"][0]["slot_id"] == "C15-1-1"
    assert "C14-45-1" in result["cleared_slots"]


def test_full_fifo_and_same_timestamp_distance_tie():
    result = pick([row(qty=40, slot="C14-45-3", timestamp="2025-01-01"),
                   row(qty=40, slot="C15-1-2")], [row(qty=1)])
    assert result["picks"][0]["slot_id"] == "C14-45-3"
    result = pick([row(slot="C14-45-1"), row(slot="C15-1-1")], [row(qty=1)])
    assert result["picks"][0]["slot_id"] == "C15-1-1"


@pytest.mark.parametrize("second,expected", [(15, 2), (10, 1)])
def test_lower_capacity_before_overload_and_exact_fill(second, expected):
    result = pick([row("B", 30), row("A", second, "C15-2-1")], [row("B", 30), row("A", second)])
    assert len(result["trips"]) == expected
    assert all(l["load_after"] <= l["capacity"] for l in result["legs"] if l["action"] == "pick")
    assert sum(l["distance"] for l in result["legs"]) == result["total_distance"]
    assert all(trip["legs"][-1]["end"] == "STAGING" for trip in result["trips"])


def test_same_sku_fill_remaining_capacity_across_fifo_records():
    result = pick([row(qty=20), row(qty=30, slot="C15-2-1")], [row(qty=50)])
    assert [t["loaded_boxes"] for t in result["trips"]] == [40, 10]


def test_owner_isolation_shortage_and_order_boundaries():
    result = pick([row(qty=10), row(qty=20, owner="P", slot="C15-2-1")],
                  [row(qty=15), row(qty=5, owner="P", order="SO2")])
    assert result["shortages"][0]["shortage_quantity"] == 5
    assert result["shortages"][0]["order_no"] == "SO"
    assert len(result["trips"]) == 2
    assert all(len({l["owner_code"] for l in t["legs"]}) == 1 for t in result["trips"])
    result = pick([row(qty=20)], [row(qty=5), row(qty=5, order="SO2")], flag=0)
    assert len(result["trips"]) == 2


def test_mixed_partial_does_not_clear_other_sku_or_change_original_quantity():
    result = pick([row(qty=10), row("B", 10)], [row(qty=3)])
    assert not result["cleared_slots"]
    assert [r["base_quantity"] for r in result["remaining_inventory"]] == [7, 10]


def test_exact_base_quantity_and_available_not_total():
    inventory, demand = row(qty=1), row(qty=2)
    inventory.update(conversion_ratio="6", quantity="0.167", available_base_quantity="1")
    demand.update(conversion_ratio="6", quantity="0.333")
    result = pick([inventory], [demand])
    assert result["picks"][0]["base_quantity"] == 1
    assert result["shortages"][0]["shortage_quantity"] == 1


def test_layout_network_distance_and_lower_crossing():
    layout = WarehouseLayout()
    assert len(layout.slots) == 657
    leg = layout.leg("STAGING", "C15-1-3")
    assert leg["vertical_distance"] == pytest.approx(3.4)
    assert all(a["x"] == b["x"] or a["y"] == b["y"] for a, b in zip(leg["path"], leg["path"][1:]))
    leg = layout.leg("C14-30-1", "C15-30-1")
    assert any(p["y"] == 23.575 for p in leg["path"])
    assert leg["horizontal_distance"] > 5.75
    layout.graph["STAGING"].clear()
    with pytest.raises(ValueError, match="不可达"):
        layout.leg("STAGING", "C14-1-1")


def store(inv, orders, slots=None, grades=None):
    layout = WarehouseLayout()
    specs = [dict(编码=sku, 所属客户=owner, 托含量=str(cap))
             for owner in ("O", "P") for sku, cap in {"A": 40, "B": 60}.items()]
    inv, orders, caps = prepare(inv, orders, specs, layout, "slotting")
    if slots is not None:
        layout.slots = {s: layout.slots[s] for s in slots}
    return inbound(inv, orders, caps, layout, grades or {}, monotonic() + 10)


def test_inbound_tail_priority_and_full_grade_preference():
    result = store([], [row(qty=40), row("B", 1)], grades={("WH", "O", "A"): "高"})
    by_sku = {r["sku_id"]: r for r in result["assignments"]}
    assert by_sku["A"]["level"] == 2
    assert by_sku["B"]["level"] == 1
    assert not any(r["mixed"] for r in result["assignments"])


def test_inbound_preserves_existing_upper_floor_mix_without_appending():
    layout = WarehouseLayout()
    specs = [dict(编码=sku, 所属客户="O", 托含量="40") for sku in ("A", "B")]
    stock = [row(qty=10, slot="C15-11-2"), row("B", 10, slot="C15-11-2")]
    inv, orders, caps = prepare(stock, [row(qty=1)], specs, layout, "slotting")
    result = inbound(inv, orders, caps, layout, {}, monotonic() + 10)
    assert result["status"] == "feasible"
    assert result["assignments"][0]["slot_id"] != "C15-11-2"
    assert result["remaining_inventory"][:2] == inv
    assert any("C15-11-2" in warning for warning in result["warnings"])
    layout.slots = {"C15-11-2": layout.slots["C15-11-2"]}
    result = inbound(inv, orders, caps, layout, {}, monotonic() + 10)
    assert result["unassigned"] and not result["assignments"]


def test_independent_first_and_mix_only_shortage():
    result = store([], [row(qty=10), row("B", 10)], ["C15-1-1", "C15-2-1"])
    assert len({r["slot_id"] for r in result["assignments"]}) == 2
    result = store([], [row(qty=10), row("B", 10)], ["C15-1-1"])
    assert len(result["assignments"]) == 2
    assert all(r["mixed"] for r in result["assignments"])
    assert len(result["legs"]) == 1
    assert sum(r["boxes"] for r in result["assignments"]) == 20


def test_append_single_sku_not_existing_mixed_and_owner_isolation():
    result = store([row(qty=10)], [row("B", 10)], ["C15-1-1"])
    assert result["assignments"][0]["appended"]
    result = store([row(qty=10), row("B", 10)], [row(qty=1)], ["C15-1-1"])
    assert result["unassigned"]
    result = store([row(qty=10)], [row("B", 10, owner="P")], ["C15-1-1"])
    assert result["unassigned"]


def test_full_forbidden_floor_one_and_tail_fallback():
    assert store([], [row(qty=40)], ["C15-1-1"])["unassigned"]
    result = store([], [row(qty=1)], ["C15-1-3"])
    assert result["assignments"][0]["level"] == 3
    assert result["warnings"]


def test_no_new_mix_on_upper_floors_and_released_single_can_append():
    result = store([], [row(qty=10), row("B", 10)], ["C15-1-3"])
    assert len(result["assignments"]) == 1
    assert not result["assignments"][0]["mixed"]
    # 快照只剩一个SKU时按单SKU尾板处理，不依赖历史混托标签。
    stock = row(qty=10)
    stock["was_mixed"] = True
    assert store([stock], [row("B", 10)], ["C15-1-1"])["assignments"][0]["appended"]


def test_frozen_inventory_occupies_slot_but_is_not_pickable():
    stock = row(qty=10)
    stock["available_base_quantity"] = "0"
    result = pick([stock], [row(qty=5)])
    assert not result["picks"]
    assert result["shortages"][0]["shortage_quantity"] == 5
    assert result["remaining_inventory"][0]["base_quantity"] == 10


def test_missing_owner_and_ambiguous_specs_fail_without_fallback():
    stock = row()
    stock["owner_code"] = ""
    with pytest.raises(ValueError, match="owner_code"):
        prepared_inputs([stock], [row()])
    specs = [dict(编码="A", 所属客户="O", 托含量=str(cap)) for cap in [40, 60]]
    with pytest.raises(ValueError, match="托含量"):
        prepare([row()], [row()], specs, WarehouseLayout(), "picking_path")


def test_persistence_success_failure_and_unique_runs(tmp_path):
    settings = {key: dict(backend="heuristic", time_limit_s=10, threads=8) for key in ("slotting", "picking_path")}
    specs = [dict(编码="A", 所属客户="O", 货品名称="A", 托含量="40")]
    payload = dict(inventory=[row()], orders=[row(qty=3)], specs=specs)
    first = plan("picking_path", payload, output_root=tmp_path, settings=settings)
    second = plan("picking_path", payload, output_root=tmp_path, settings=settings)
    assert first["status"] == "feasible" and first["run_id"] != second["run_id"]
    assert payload["inventory"][0]["base_quantity"] == "10"
    assert json.loads((tmp_path / first["run_id"] / "result.json").read_text(encoding="utf-8")) == first
    payload["specs"][0]["托含量"] = ""
    failure = plan("picking_path", payload, output_root=tmp_path, settings=settings)
    assert failure["status"] == "failed" and "托含量" in failure["message"]
    assert (tmp_path / failure["run_id"] / "input.json").exists()


def test_time_limit_does_not_publish_incomplete_plan():
    inv, orders, caps, layout = prepared_inputs([row()], [row()])
    with pytest.raises(TimeoutError):
        outbound(inv, orders, caps, layout, 0, monotonic() - 1)
