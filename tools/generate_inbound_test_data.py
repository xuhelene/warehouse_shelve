"""生成需求 V0.13 入库测试场景；运行时不访问平台、不改业务样例。"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def row(sku="A", qty="10", slot="C15-1-1", owner="O", **extra):
    return dict(sku_id=sku, name=sku, owner_code=owner, owner_name=owner,
                warehouse_code="WH", timestamp="2026-10-01T08:00:00", order_no="IN-1",
                base_quantity=str(qty), conversion_ratio="1", quantity=str(qty), unit="箱",
                base_unit="瓶", slot_id=slot, staging=False, **extra)


def scenarios():
    specs = [dict(编码=sku, 所属客户=owner, 货品名称=sku, 托含量=str(cap))
             for owner in ("O", "P") for sku, cap in (("A", 40), ("B", 60), ("C", 20))]
    cases = []

    def add(name, rule, orders, slots, inventory=None, expected=None, grades=None):
        cases.append(dict(id=name, rule=rule, inventory=inventory or [], orders=orders,
                          specs=specs, slots=slots, grades=grades or [], expected=expected or {}))

    for grade, level in (("高", 2), ("中", 2), ("低", 5)):
        add("grade_" + str(level) + grade, "BR-03/5.4", [row(qty=40)],
            ["C15-1-1", "C15-1-2", "C15-1-3", "C15-1-5"],
            expected={"levels": [level]}, grades=[["WH", "O", "A", grade]])
        add("tail_overrides_" + grade, "BR-02/BR-03", [row(qty=1)],
            ["C15-1-1", "C15-1-2", "C15-1-5"], expected={"levels": [1]},
            grades=[["WH", "O", "A", grade]])
    add("default_low", "BR-03", [row(qty=40)], ["C15-1-2", "C15-1-5"], expected={"levels": [5]})
    add("preference_before_distance", "5.4", [row(qty=40)], ["C15-1-3", "C15-45-2"],
        expected={"assigned_slots": ["C15-45-2"]}, grades=[["WH", "O", "A", "高"]])
    add("fallback_by_distance", "5.4", [row(qty=40)], ["C15-45-3", "C15-1-4"],
        expected={"assigned_slots": ["C15-1-4"], "warning": True}, grades=[["WH", "O", "A", "高"]])
    add("medium_all_preferred", "BR-03", [row(qty=40)], ["C15-45-2", "C15-1-4"],
        expected={"levels": [4]}, grades=[["WH", "O", "A", "中"]])
    for qty, count, tails in (("0", 0, 0), ("0.01", 1, 1), ("39.999", 1, 1),
                              ("40", 1, 0), ("40.001", 2, 1), ("80", 2, 0), ("81", 3, 1)):
        add("quantity_" + qty, "BR-01/5.2/7.2", [row(qty=qty)],
            ["C15-1-1", "C15-1-2", "C15-1-5"], expected={"count": count, "tails": tails})
    add("fractional_first", "5.4", [row(qty=39.9), row("B", "0.5")],
        ["C15-1-1", "C15-1-2"], expected={"sku_levels": {"A": 2, "B": 1}})
    add("full_floor_one_forbidden", "5.4", [row(qty=40)], ["C15-1-1"], expected={"unassigned": "40"})
    add("tail_upper_fallback", "BR-02", [row(qty=1)], ["C15-1-3"], expected={"levels": [3], "warning": True})
    add("no_slots", "5.3/5.4", [row(qty=41)], [], expected={"unassigned": "41"})
    add("partial_capacity", "5.3/5.4", [row(qty=81)], ["C15-1-2"], expected={"unassigned": "80"})
    add("independent_tails", "5.4", [row(), row("B")], ["C15-1-1", "C15-2-1"], expected={"mixed": False})
    add("new_mix", "BR-04", [row(), row("B")], ["C15-1-1"], expected={"count": 2, "mixed": True})
    add("mix_three", "BR-04", [row(qty=5), row("B", 5), row("C", 5)], ["C15-1-1"],
        expected={"count": 3, "mixed": True})
    for qty, unassigned in (("30", "0"), ("30.001", "30.001")):
        add("mix_boundary_" + qty, "BR-04/7.4", [row(), row("B", qty)], ["C15-1-1"],
            expected={"unassigned": unassigned})
        add("append_boundary_" + qty, "BR-04/7.4", [row("B", qty)], ["C15-1-1"],
            inventory=[row()], expected={"unassigned": unassigned})
    for name, stock, order in (
        ("same_owner_append", [row()], row("B")),
        ("same_sku_append", [row()], row(qty=1)),
        ("other_owner", [row()], row("B", owner="P")),
        ("existing_mix", [row(), row("B")], row("C", 1)),
        ("existing_full", [row(qty=40)], row("B", 1))):
        add(name, "BR-01/BR-04/BR-07", [order], ["C15-1-1"], stock,
            expected={"unassigned": "0" if name.endswith("append") else order["base_quantity"]})
    add("cross_owner_new_mix", "BR-07", [row(), row("B", owner="P")], ["C15-1-1"], expected={"unassigned": "10"})
    add("upper_no_mix", "5.4", [row(), row("B")], ["C15-1-3"], expected={"count": 1, "mixed": False, "unassigned": "10"})
    add("upper_no_append", "5.4", [row("B")], ["C15-1-3"], [row(slot="C15-1-3")], expected={"unassigned": "10"})
    add("no_relocation", "5.4/5.5", [row(qty=40)], ["C15-1-1", "C15-1-2"],
        [row(slot="C15-1-2")], expected={"unassigned": "40"})
    add("zero_inventory_not_occupied", "BR-01", [row()], ["C15-1-1"], [row(qty=0)], expected={"count": 1})
    add("frozen_still_occupied", "7.4", [row(qty=40)], ["C15-1-2"],
        [row(available_base_quantity="0", slot="C15-1-2")], expected={"unassigned": "40"})
    add("staging_not_occupied", "7.1", [row()], ["C15-1-1"],
        [{**row(), "staging": True, "slot_id": None}], expected={"count": 1})
    add("batch_time_preserved", "3/5.3", [row(qty=2, batch_no="NEW")], ["C15-1-1"],
        [{**row(qty=3, batch_no="OLD"), "timestamp": "2025-01-01"}], expected={"count": 1})
    add("multiple_order_lines", "3/5.3", [row(qty=2), {**row(qty=3), "order_no": "IN-2"}],
        ["C15-1-1", "C15-2-1"], expected={"count": 2})
    # 总空位足够但只有一个一层空位，混托能使全部尾板满足一层偏好。
    add("legal_floor_shortage_mix", "5.4", [row(), row("B")], ["C15-1-1", "C15-1-3"],
        expected={"count": 2, "levels": [1, 1]})
    add("floor_shortage_append", "5.4", [row("B")], ["C15-1-1", "C15-1-3"], [row()],
        expected={"assigned_slots": ["C15-1-1"], "levels": [1]})
    add("floor_shortage_capacity_fallback", "5.4/BR-04", [row(qty=39.9), row("B", 1)],
        ["C15-1-1", "C15-1-3"], expected={"levels": [1, 3], "mixed": False})
    add("floor_shortage_owner_fallback", "5.4/BR-07", [row(), row("B", owner="P")],
        ["C15-1-1", "C15-1-3"], expected={"levels": [1, 3], "mixed": False})
    return cases


if __name__ == "__main__":
    target = ROOT / "data/testing/inbound_requirement_cases.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scenarios(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(scenarios())} cases: {target}")
