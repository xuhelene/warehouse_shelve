# 文件说明：Web 结果汇总、CSV 读取和接口返回数据整理。
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd
from fastapi import HTTPException

def normalize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in meta.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[key] = value
        elif isinstance(value, Path):
            normalized[key] = str(value)
        else:
            normalized[key] = str(value)
    return normalized


def build_slotting_summary(solution: pd.DataFrame) -> dict[str, Any]:
    if solution.empty:
        return {"assigned_records": 0, "distinct_slots": 0, "distinct_skus": 0, "assigned_boxes": 0.0}
    return {
        "assigned_records": int(len(solution)),
        "distinct_slots": int(solution["slot_id"].nunique()),
        "distinct_skus": int(solution["sku_id"].nunique()),
        "assigned_boxes": float(solution["qty_boxes_in_slot"].sum()) if "qty_boxes_in_slot" in solution else 0.0,
        "existing_boxes": int(solution.loc[solution["inventory_type"].eq("已有货物"), "qty_boxes_in_slot"].sum()) if "inventory_type" in solution else 0,
        "inbound_boxes": int(solution.loc[solution["inventory_type"].eq("新增入库"), "qty_boxes_in_slot"].sum()) if "inventory_type" in solution else 0,
    }


def none_if_nan(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: none_if_nan(value) for key, value in record.items()}


def dataframe_records(df: pd.DataFrame, limit: Optional[int] = None) -> List[dict[str, Any]]:
    if limit is not None:
        df = df.head(limit)
    return [normalize_record(record) for record in df.to_dict(orient="records")]


def read_slotting_layout(job: dict[str, Any]) -> dict[str, Any]:
    slots_path = Path(job["params"]["files"]["slots"]["path"])
    solution_path = Path(job["result_files"]["solution"])
    slots = pd.read_csv(slots_path).copy()
    solution = pd.read_csv(solution_path).copy()

    required = {"slot_id", "slot_cost", "x", "y"}
    missing = required - set(slots.columns)
    if missing:
        raise HTTPException(status_code=500, detail=f"Slots file missing columns: {sorted(missing)}")

    slots["slot_id"] = slots["slot_id"].astype(str)
    if "is_storage" not in slots.columns:
        slots["is_storage"] = 1
    slots["is_storage"] = pd.to_numeric(slots["is_storage"], errors="coerce").fillna(1).astype(int)
    solution["slot_id"] = solution["slot_id"].astype(str)
    alloc_map = solution.drop_duplicates("slot_id").set_index("slot_id").to_dict(orient="index")

    payload_slots = []
    payload_areas = []
    for _, row in slots.iterrows():
        slot_id = str(row["slot_id"])
        pick_endpoint = pd.to_numeric(row.get("is_pick_endpoint", 0), errors="coerce")
        if pd.isna(pick_endpoint):
            pick_endpoint = 0
        base = {
            "slot_id": slot_id,
            "slot_name": none_if_nan(row.get("slot_name")),
            "area_type": none_if_nan(row.get("area_type")),
            "is_storage": int(row["is_storage"]),
            "is_pick_endpoint": int(pick_endpoint),
            "slot_cost": none_if_nan(row.get("slot_cost")),
            "pallet_cap": none_if_nan(row.get("pallet_cap")),
            "x": float(row["x"]),
            "y": float(row["y"]),
            "length": none_if_nan(row.get("length")),
            "width": none_if_nan(row.get("width", row.get("widht"))),
            "height": none_if_nan(row.get("height")),
        }
        if int(row["is_storage"]) == 1:
            allocation = alloc_map.get(slot_id)
            base["allocation"] = normalize_record(allocation) if allocation else None
            payload_slots.append(base)
        else:
            payload_areas.append(base)

    occupied = solution["slot_id"].nunique() if not solution.empty else 0
    total_slots = int(slots["is_storage"].sum())
    top_skus = (
        solution.groupby("sku_id")["qty_boxes_in_slot"].sum().sort_values(ascending=False).head(8).index.tolist()
        if not solution.empty and "qty_boxes_in_slot" in solution.columns
        else []
    )
    return {
        "summary": {
            "solution_name": solution_path.name,
            "total_slots": int(total_slots),
            "occupied_slots": int(occupied),
            "occupancy_rate": round((occupied / total_slots) * 100, 2) if total_slots else 0.0,
            "distinct_skus": int(solution["sku_id"].nunique()) if not solution.empty else 0,
            "top_skus": top_skus,
        },
        "slots": payload_slots,
        "areas": payload_areas,
    }


def build_picking_path_summary(route: pd.DataFrame, reports: dict[str, pd.DataFrame]) -> dict[str, Any]:
    order_summary = reports["order_summary"]
    unfulfilled = reports["unfulfilled"]
    total_orders = (
        int(order_summary["order_count"].sum())
        if not order_summary.empty and "order_count" in order_summary.columns
        else int(len(order_summary))
    )
    return {
        "route_rows": int(len(route)),
        "total_orders": total_orders,
        "route_count": int(len(order_summary)),
        "total_pick_stops": int(order_summary["pick_stop_count"].sum()) if not order_summary.empty else 0,
        "total_distance": float(order_summary["total_distance"].sum()) if not order_summary.empty else 0.0,
        "unfulfilled_lines": int(len(unfulfilled)),
    }


def read_picking_path_result(job: dict[str, Any]) -> dict[str, Any]:
    # sku_code / slot_id / order_no 都是编码而非数值，回读时固定成字符串，避免前端显示成 1001752.0
    route = (
        pd.read_csv(job["result_files"]["route"], dtype={"sku_code": str, "slot_id": str, "order_no": str})
        if Path(job["result_files"]["route"]).exists()
        else pd.DataFrame()
    )
    order_summary = (
        pd.read_csv(job["result_files"]["order_summary"])
        if Path(job["result_files"]["order_summary"]).exists()
        else pd.DataFrame()
    )
    unfulfilled = (
        pd.read_csv(job["result_files"]["unfulfilled"])
        if Path(job["result_files"]["unfulfilled"]).exists()
        else pd.DataFrame()
    )
    files = job.get("params", {}).get("files", {})
    slots = pd.read_csv(files["slots"]["path"]) if "slots" in files and Path(files["slots"]["path"]).exists() else pd.DataFrame()
    aisles = (
        pd.read_csv(files["aisles"]["path"])
        if "aisles" in files and Path(files["aisles"]["path"]).exists()
        else pd.DataFrame()
    )
    nodes = (
        pd.read_csv(files["nodes"]["path"])
        if "nodes" in files and Path(files["nodes"]["path"]).exists()
        else pd.DataFrame()
    )
    pick_points = (
        pd.read_csv(files["pick_points"]["path"])
        if "pick_points" in files and Path(files["pick_points"]["path"]).exists()
        else pd.DataFrame()
    )

    layout_slots = pd.DataFrame()
    layout_areas = pd.DataFrame()
    if not slots.empty:
        slots = slots.copy()
        slots["is_storage"] = pd.to_numeric(slots.get("is_storage", 1), errors="coerce").fillna(1).astype(int)
        layout_slots = slots[slots["is_storage"].eq(1)].copy()
        layout_areas = slots[~slots["is_storage"].eq(1)].copy()

    return {
        "summary": job["summary"],
        "route_rows": dataframe_records(route, limit=300),
        "order_summary_rows": dataframe_records(order_summary, limit=200),
        "unfulfilled_rows": dataframe_records(unfulfilled, limit=200),
        "layout_slots": dataframe_records(layout_slots),
        "layout_areas": dataframe_records(layout_areas),
        "aisles": dataframe_records(aisles),
        "nodes": dataframe_records(nodes),
        "pick_points": dataframe_records(pick_points),
    }


