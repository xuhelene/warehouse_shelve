from __future__ import annotations

import math
from typing import Dict, Tuple

import pandas as pd


def solve_with_heuristic(
    slots: pd.DataFrame,
    items: pd.DataFrame,
    cand: pd.DataFrame,
    time_limit_s: float = 60.0,
    alpha_slots: float = 0.01,
    use_demand_if_present: bool = True,
) -> Tuple[pd.DataFrame, Dict]:
    demand_available = use_demand_if_present and ("monthly_outbound_boxes" in items.columns)

    qty_map = items.set_index("item_id")["qty_boxes"].astype(int).to_dict()
    bpp_map = items.set_index("item_id")["boxes_per_pallet"].astype(int).to_dict()
    pallet_cap_map = slots.set_index("slot_id")["pallet_cap"].astype(int).to_dict()
    slot_cost_map = slots.set_index("slot_id")["slot_cost"].astype(float).to_dict()
    demand_map = (
        items.set_index("item_id")["monthly_outbound_boxes"].astype(float).to_dict()
        if demand_available
        else {item_id: 1.0 for item_id in items["item_id"]}
    )

    slot_options_by_item = {
        str(item_id): sorted(
            [str(slot_id) for slot_id in cand.loc[cand["item_id"].eq(item_id), "slot_id"].tolist()],
            key=lambda slot_id: (float(slot_cost_map[slot_id]), slot_id),
        )
        for item_id in items["item_id"]
    }

    need_pallets_map = {
        str(item_id): int(math.ceil(int(qty_map[item_id]) / max(int(bpp_map[item_id]), 1)))
        for item_id in items["item_id"]
    }
    # 把每个 item 先按货位托盘容量切成若干"托盘块"，再按块分配，而不是整件 item 依次抢货位。
    # 目标函数 Σ c_s · d_i · p 是乘积形式，按排列不等式：块权重 (d_i × 块内托盘数) 降序
    # 配货位成本升序才是最优配对。整件 item 抢货位会把"33 托盘 item 的 3 托盘余数"也塞进
    # 最便宜的货位，把便宜货位让给了小块，实测比最优差 3.6%。
    chunks = []  # (item_id, 托盘数, 箱数)
    for item_id in items["item_id"].astype(str):
        options = slot_options_by_item[item_id]
        if not options:
            raise RuntimeError(f"No candidate slot for item_id={item_id}.")
        chunk_cap = max(int(pallet_cap_map[slot_id]) for slot_id in options)
        bpp = max(int(bpp_map[item_id]), 1)
        remaining_pallets = int(need_pallets_map[item_id])
        remaining_qty = int(qty_map[item_id])
        while remaining_pallets > 0:
            take_pallets = min(remaining_pallets, chunk_cap)
            take_boxes = min(remaining_qty, take_pallets * bpp)
            chunks.append((item_id, take_pallets, take_boxes))
            remaining_pallets -= take_pallets
            remaining_qty -= take_boxes

    # 候选数少的块优先（forbidden/fixed 限制得最死），仍然放在第一位当可行性兜底。
    chunks.sort(key=lambda c: (len(slot_options_by_item[c[0]]), -float(demand_map[c[0]]) * c[1], c[0]))

    used_slots: set[str] = set()
    out_rows = []
    objective_value = 0.0

    for item_id, chunk_pallets, chunk_boxes in chunks:
        for slot_id in slot_options_by_item[item_id]:
            if slot_id in used_slots or int(pallet_cap_map[slot_id]) < chunk_pallets:
                continue
            used_slots.add(slot_id)
            slot_cost = float(slot_cost_map[slot_id])
            p_assign = int(math.ceil(chunk_boxes / max(int(bpp_map[item_id]), 1)))
            objective_value += slot_cost * float(demand_map[item_id]) * p_assign + float(alpha_slots)
            out_rows.append((item_id, slot_id, chunk_boxes, p_assign, slot_cost))
            break
        else:
            raise RuntimeError(
                f"No feasible heuristic slotting assignment for item_id={item_id}; "
                f"unplaced chunk of {chunk_pallets} pallet(s) / {chunk_boxes} box(es)."
            )

    sol = pd.DataFrame(
        out_rows,
        columns=["item_id", "slot_id", "qty_boxes_in_slot", "pallets_used_in_slot", "slot_cost"],
    )
    meta = {
        "backend": "heuristic",
        "status": "feasible",
        "objective_value": objective_value,
        "used_slots": len(used_slots),
        "time_limit_s": float(time_limit_s),
    }
    return sol, meta
