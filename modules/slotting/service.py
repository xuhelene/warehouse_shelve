from __future__ import annotations

from typing import Optional, Tuple, Dict
import pandas as pd

from core.models import SolverConfig, SolveOptions
from modules.slotting.preprocess import prepare_slotting_data, load_picking_geometry
from modules.slotting.postprocess import enrich_solution
from core.constants import boxes_per_pallet, default_slot_pallet_cap
from solvers.slotting_copt_backend import solve_with_copt
from solvers.slotting_heuristic_backend import solve_with_heuristic
from solvers.slotting_ortools_backend import solve_with_ortools


def read_existing_inventory(path: str, slots_path: str) -> pd.DataFrame:
    inventory = pd.read_csv(path, dtype={"slot_id": str, "sku_id": str, "batch_id": str})
    required = {"slot_id", "sku_id", "batch_id", "qty_boxes", "box_l", "box_w", "box_h"}
    if required - set(inventory.columns):
        raise ValueError(f"Existing inventory missing columns: {sorted(required - set(inventory.columns))}")
    slots = pd.read_csv(slots_path, dtype={"slot_id": str}).set_index("slot_id")
    if inventory[list(required)].isna().any().any() or inventory["slot_id"].duplicated().any():
        raise ValueError("Existing inventory must have complete fields and one row per occupied slot")
    if not inventory["slot_id"].isin(slots.index).all():
        raise ValueError("Existing inventory refers to unknown slots")
    selected = slots.loc[inventory["slot_id"]].reset_index(drop=True)
    if "is_storage" in selected and not selected["is_storage"].eq(1).all():
        raise ValueError("Existing inventory must use storage slots")
    qty = pd.to_numeric(inventory["qty_boxes"], errors="raise")
    if not ((qty > 0) & (qty % 1 == 0)).all():
        raise ValueError("Existing inventory box quantities must be positive integers")
    dimensions = inventory[["box_l", "box_w", "box_h"]].apply(pd.to_numeric, errors="raise")
    if not ((dimensions > 0) & (dimensions < float("inf"))).all().all():
        raise ValueError("Existing inventory box dimensions must be positive and finite")
    inventory["boxes_per_pallet"] = inventory.apply(
        lambda r: boxes_per_pallet(float(r.box_l), float(r.box_w), float(r.box_h)), axis=1
    )
    capacity = selected["pallet_cap"] if "pallet_cap" in selected else default_slot_pallet_cap()
    if (inventory["boxes_per_pallet"] <= 0).any() or (qty > inventory["boxes_per_pallet"] * capacity).any():
        raise ValueError("Existing inventory exceeds slot capacity")
    inventory["item_id"] = inventory["sku_id"] + "||" + inventory["batch_id"]
    inventory["qty_boxes_in_slot"] = qty.astype(int)
    inventory["qty_boxes"] = inventory.groupby("item_id")["qty_boxes_in_slot"].transform("sum")
    inventory["assigned_boxes"] = inventory["qty_boxes"]
    inventory["pallets_used_in_slot"] = ((qty + inventory["boxes_per_pallet"] - 1) // inventory["boxes_per_pallet"]).astype(int)
    inventory["slot_cost"] = selected["slot_cost"]
    inventory["inventory_type"] = "已有货物"
    return inventory


def solve_slotting(
    slots_path: str,
    items_virtual_path: str,
    cfg: Optional[SolverConfig] = None,
    options: Optional[SolveOptions] = None,
    existing_inventory_path: Optional[str] = None,
    picking_points_path: Optional[str] = None,
    picking_aisles_path: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict]:
    if options is None:
        options = SolveOptions()

    existing = read_existing_inventory(existing_inventory_path, slots_path) if existing_inventory_path else pd.DataFrame()
    # 邻近性惩罚会让"更贵但挨着"的货位变成最优选择，交换论证对需要拆到多个货位的 item
    # 不再成立，这些 item 保留全部候选货位；单货位 item 的 span 恒为 0，照常裁剪。
    proximity_on = options.backend == "ortools" and (
        options.alpha_proximity > 0 or options.proximity_max_distance > 0
    )
    geometry, geometry_note = (
        load_picking_geometry(picking_points_path, picking_aisles_path) if proximity_on else (None, "proximity off")
    )
    slots, items, cand = prepare_slotting_data(
        slots_path=slots_path,
        items_virtual_path=items_virtual_path,
        cfg=cfg,
        use_demand_if_present=options.use_demand_if_present,
        candidate_slots_per_item=options.candidate_slots_per_item,
        occupied_slot_ids=set(existing["slot_id"]) if not existing.empty else None,
        full_candidates_for_multi_slot=proximity_on,
    )
    if not existing.empty and items["item_id"].isin(existing["item_id"]).any():
        raise ValueError("Inbound goods must use new batch IDs, distinct from existing inventory")

    if options.backend == "heuristic":
        sol, meta = solve_with_heuristic(
            slots=slots,
            items=items,
            cand=cand,
            time_limit_s=options.time_limit_s,
            alpha_slots=options.alpha_slots,
            use_demand_if_present=options.use_demand_if_present,
        )
    elif options.backend == "copt":
        sol, meta = solve_with_copt(
            slots=slots,
            items=items,
            cand=cand,
            time_limit_s=options.time_limit_s,
            alpha_slots=options.alpha_slots,
            use_demand_if_present=options.use_demand_if_present,
            mip_gap=options.mip_gap,
        )
    elif options.backend == "ortools":
        sol, meta = solve_with_ortools(
            slots=slots,
            items=items,
            cand=cand,
            time_limit_s=options.time_limit_s,
            alpha_slots=options.alpha_slots,
            use_demand_if_present=options.use_demand_if_present,
            num_search_workers=options.num_search_workers,
            alpha_proximity=options.alpha_proximity,
            proximity_max_distance=options.proximity_max_distance,
            alpha_proximity_excess=options.alpha_proximity_excess,
            mip_gap=options.mip_gap,
            proximity_geometry=geometry,
        )
    else:
        raise ValueError("options.backend must be 'heuristic', 'copt' or 'ortools'")

    sol = enrich_solution(sol, items)
    sol["inventory_type"] = "新增入库"
    if not existing.empty:
        sol = pd.concat([existing.reindex(columns=sol.columns), sol], ignore_index=True)
    meta["existing_boxes"] = int(existing["qty_boxes_in_slot"].sum()) if not existing.empty else 0
    meta["candidate_slots"] = int(cand["slot_id"].nunique())
    if proximity_on:
        meta["picking_geometry"] = geometry_note
    return sol, meta
