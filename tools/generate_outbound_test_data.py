"""生成需求V0.13的出库测试数据，不覆盖业务样例。"""
import json
from decimal import Decimal as D
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def row(sku="A", qty=10, slot="C15-1-1", owner="O", timestamp="2026-10-01", order="SO-1", **extra):
    return dict(sku_id=sku, name="货品" + sku, owner_code=owner, owner_name=owner, warehouse_code="WH",
                timestamp=timestamp, order_no=order, base_quantity=str(qty), base_unit="瓶",
                conversion_ratio="1", quantity=str(qty), unit="箱", slot_id=slot,
                staging=False, available_base_quantity=str(qty), **extra)


def scenarios():
    specs = [dict(编码=sku, 所属客户=owner, 货品名称="货品" + sku, 托含量=str(cap))
             for owner in ("O", "P") for sku, cap in (("A", 40), ("B", 60), ("C", 20))]
    cases = []

    def add(name, rule, stock, orders, expected=None, flag=1):
        cases.append(dict(id=name, rule=rule, inventory=stock, orders=orders, specs=specs,
                          storeBatchFlag=flag, layer_weight=1, expected=expected or {}))

    for qty in ("0", "0.001", "1", "9.999", "10", "10.001", "40", "41"):
        add("tail_demand_" + qty, "BR-05/6.2", [row()], [row(qty=qty)],
            {"fulfilled": str(min(D(qty), 10)), "shortage": str(max(D(qty) - 10, 0))})
    for flag in (0, 1):
        add("empty_inventory_" + str(flag), "6.2/6.4", [], [row(qty=5)],
            {"fulfilled": "0", "shortage": "5", "trips": 0}, flag)
        add("multi_trip_" + str(flag), "BR-06", [row(qty=40), row(qty=40, slot="C15-2-1"), row(qty=10, slot="C15-3-1")],
            [row(qty=90)], {"trip_loads": ["40", "40", "10"], "cleared": ["C15-1-1", "C15-2-1", "C15-3-1"]}, flag)
        add("order_boundary_" + str(flag), "6.4", [row(qty=20)],
            [row(qty=5), row(qty=5, order="SO-2")], {"trips": 2 if flag == 0 else 1}, flag)
        add("shortage_order_attribution_" + str(flag), "6.4", [row(qty=10)],
            [row(qty=8), row(qty=8, order="SO-2")], {"shortage": "6", "shortage_orders": ["SO-2"]}, flag)
    add("tail_before_old_full", "BR-05", [row(qty=40, timestamp="2025-01-01"), row(qty=10, slot="C15-3-1")],
        [row(qty=5)], {"sources": {"1": "5"}})
    add("old_tail_before_near_tail", "BR-05", [row(qty=10, slot="C15-45-1", timestamp="2025-01-01"), row(qty=10)],
        [row(qty=12)], {"sources": {"0": "10", "1": "2"}, "first_slot": "C15-1-1"})
    add("old_full_before_near_full", "BR-05", [row(qty=40, slot="C15-45-2", timestamp="2025-01-01"), row(qty=40, slot="C15-1-2")],
        [row(qty=1)], {"sources": {"0": "1"}})
    add("same_time_nearest_tail", "6.4", [row(qty=10, slot="C15-45-1"), row()], [row(qty=1)], {"sources": {"1": "1"}})
    add("same_time_nearest_full", "6.4", [row(qty=40, slot="C15-45-2"), row(qty=40, slot="C15-1-2")],
        [row(qty=1)], {"sources": {"1": "1"}})
    add("same_slot_multiple_timestamps", "3/7.1", [row(qty=5, timestamp="2025-01-01"), row(qty=5)],
        [row(qty=6)], {"sources": {"0": "5", "1": "1"}})
    add("batch_not_order_filter", "6.4", [row(qty=5, timestamp="2025-01-01", batch_no="OLD"), row(qty=5, batch_no="NEW")],
        [row(qty=1, batch_no="NEW")], {"sources": {"0": "1"}})
    add("frozen_old_tail_skipped", "7.4", [{**row(timestamp="2025-01-01"), "available_base_quantity": "0"}, row(slot="C15-2-1")],
        [row(qty=5)], {"sources": {"1": "5"}})
    add("partial_available", "7.1", [{**row(), "available_base_quantity": "3"}], [row(qty=8)],
        {"fulfilled": "3", "shortage": "5", "cleared": []})
    add("zero_stock", "7.1", [row(qty=0)], [row(qty=1)], {"shortage": "1", "trips": 0})
    add("staging_not_pickable", "7.1", [{**row(), "slot_id": None, "staging": True}], [row(qty=1)], {"shortage": "1"})
    add("other_owner_no_substitution", "BR-07", [row(owner="P")], [row(qty=1)], {"shortage": "1"})
    add("wave_owner_unload", "BR-07/6.4", [row(), row(owner="P", slot="C15-2-1")],
        [row(qty=1), row(qty=1, owner="P", order="SO-2")], {"trips": 2})
    add("wave_same_order_owners_isolated", "BR-07/6.4", [row(), row(owner="P", slot="C15-2-1")],
        [row(qty=1), row(qty=1, owner="P")], {"trips": 2})
    add("wave_three_owner_runs", "BR-07", [row(qty=40), row(qty=40, slot="C15-3-1"), row(owner="P", slot="C15-2-1")],
        [row(qty=80), row(qty=1, owner="P", order="SO-2")], {"trips": 3})
    for qty, loads in (("9.999", ["39.999"]), ("10", ["40"]), ("10.001", ["30", "10.001"]), ("15", ["30", "15"])):
        add("new_sku_capacity_" + qty, "6.4", [row("B", 30), row("A", qty, "C15-2-1")],
            [row("B", 30), row("A", qty)], {"trip_loads": loads})
    add("same_sku_continue_until_full", "BR-06", [row(qty=20), row(qty=30, slot="C15-2-1")],
        [row(qty=50)], {"trip_loads": ["40", "10"]})
    add("same_sku_orders_share_capacity", "BR-06/6.4", [row(qty=40), row(qty=10, slot="C15-2-1")],
        [row(qty=25), row(qty=25, order="SO-2")], {"trip_loads": ["40", "10"]})
    add("third_sku_lowers_capacity", "6.4", [row("B", 10), row("A", 10, "C15-2-1"), row("C", 1, "C15-3-1")],
        [row("B", 10), row("A", 10), row("C", 1)], {"trips": 2, "max_distance": 34.7})
    add("mixed_partial", "BR-04/6.4", [row(qty=10), row("B", 10)], [row(qty=3)], {"cleared": [], "sources": {"0": "3"}})
    add("mixed_one_sku_empty", "6.4", [row(qty=10), row("B", 10)], [row(qty=10)], {"cleared": []})
    add("mixed_all_empty", "6.4", [row(qty=10), row("B", 10)], [row(qty=10), row("B", 10)], {"cleared": ["C15-1-1"]})
    add("mixed_full_total_still_tail", "6.4", [row(qty=30), row("B", 10), row(qty=40, slot="C15-2-2", timestamp="2025-01-01")],
        [row(qty=1)], {"sources": {"0": "1"}})
    add("upper_existing_mix_warning", "6.4", [row(slot="C15-11-2"), row("B", slot="C15-11-2")],
        [row(qty=1)], {"warning": True})
    add("vertical_same_column", "6.2/7.4", [row(qty=1, slot="C15-1-2"), row("B", 1, "C15-1-4")],
        [row(qty=1), row("B", 1)], {"trips": 1, "distance": 24.1})
    add("single_full_route_distance", "6.2", [row(qty=40, slot="C15-1-2")], [row(qty=40)], {"distance": 17.3})
    add("same_lane_continue_distance", "6.2", [row(qty=1), row("B", 1, "C15-2-1")],
        [row(qty=1), row("B", 1)], {"distance": 16.2})
    add("cross_rack_detour", "6.2/7.4", [row(qty=1, slot="C14-30-1"), row("B", 1, "C15-30-1")],
        [row(qty=1), row("B", 1)], {"trips": 1})
    add("same_workpoint_other_rack", "6.3", [row(qty=1), row("B", 1, "C16-1-1")],
        [row(qty=1), row("B", 1)], {"distance": 13.9})
    add("rounded_boxes_exact_base", "7.1", [{**row(qty=1), "conversion_ratio": "6", "quantity": "0.167"}],
        [{**row(qty=2), "conversion_ratio": "6", "quantity": "0.333"}], {"fulfilled": "1", "shortage": "1"})
    add("unrelated_sku_untouched", "BR-07", [row(), row("C", 10, "C15-2-1")], [row(qty=1)], {"sources": {"0": "1"}})
    add("different_sku_units", "6.1", [row(), {**row("B", slot="C15-2-1"), "base_unit": "套", "conversion_ratio": "2"}],
        [row(qty=1), {**row("B", qty=1), "base_unit": "套", "conversion_ratio": "2"}], {"trips": 1})
    precise_specs = [dict(编码="A", 所属客户="O", 货品名称="货品A", 托含量="1")]
    add("fractional_full_not_tail", "BR-05/7.1", [row(qty=1, timestamp="2025-01-01") for _ in range(3)] +
        [row(qty=1, slot="C15-45-1")], [row(qty=1)], {"sources": {"3": "1"}})
    cases[-1]["specs"] = precise_specs
    for r in cases[-1]["inventory"] + cases[-1]["orders"]:
        r["conversion_ratio"] = "3"
    add("fractional_exact_full_load", "BR-06/7.1", [row(qty=1, slot=f"C15-{i}-1") for i in range(1, 5)],
        [row(qty=4)], {"trips": 2})
    cases[-1]["specs"] = precise_specs
    for r in cases[-1]["inventory"] + cases[-1]["orders"]:
        r["conversion_ratio"] = "3"
    add("fractional_capacity_validation", "BR-01/7.1", [row(qty=1) for _ in range(18)], [row(qty=18)],
        {"trips": 1, "cleared": ["C15-1-1"]})
    cases[-1]["specs"] = precise_specs
    for r in cases[-1]["inventory"] + cases[-1]["orders"]:
        r["conversion_ratio"] = "18"
    add("partial_available_full_stays_full", "BR-05/7.1", [{**row(qty=40, timestamp="2025-01-01"), "available_base_quantity": "1"},
        row(qty=5, slot="C15-45-1")], [row(qty=1)], {"sources": {"1": "1"}})
    add("repeated_order_sku_no_double_allocation", "6.2/6.4", [row(qty=10)],
        [row(qty=8), row(qty=8)], {"fulfilled": "10", "shortage": "6"})
    add("fractional_same_sku_fills_multiple_trips", "BR-06", [row(qty=25), row(qty=25, slot="C15-2-1")],
        [row(qty=50)], {"trip_loads": ["40", "10"]})
    add("leading_zero_sku_identity", "7.2", [row("001", 5)], [row("001", 1)], {"fulfilled": "1"})
    cases[-1]["specs"] = [dict(编码="001", 所属客户="O", 托含量="40")]
    return cases


if __name__ == "__main__":
    path = ROOT / "data/testing/outbound_requirement_cases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scenarios(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(scenarios())} cases: {path}")
