from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd


def _require_columns(df: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = columns - set(df.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def build_pick_points(slots_path: str | Path, aisles_path: str | Path) -> pd.DataFrame:
    slots = pd.read_csv(slots_path)
    aisles = pd.read_csv(aisles_path)

    _require_columns(slots, {"slot_id", "is_storage", "x", "y", "length", "width"}, "slots.csv")
    _require_columns(aisles, {"aisle_id", "aisle_type", "x", "y", "length", "width"}, "picking_aisles.csv")

    slots["is_storage"] = pd.to_numeric(slots["is_storage"], errors="coerce").fillna(1).astype(int)
    for col in ["x", "y", "length", "width"]:
        slots[col] = pd.to_numeric(slots[col], errors="coerce")
        aisles[col] = pd.to_numeric(aisles[col], errors="coerce")

    storage_slots = slots[slots["is_storage"].eq(1)].copy()
    row_aisles = aisles[aisles["aisle_type"].astype(str).eq("row")].copy()
    if row_aisles.empty:
        raise ValueError("picking_aisles.csv must contain at least one row aisle")

    rows: list[dict[str, object]] = []
    for _, slot in storage_slots.iterrows():
        slot_x_center = float(slot["x"] + slot["width"] / 2)
        slot_y_center = float(slot["y"] + slot["length"] / 2)

        candidates = row_aisles[
            (row_aisles["x"] <= slot_x_center)
            & ((row_aisles["x"] + row_aisles["width"]) >= slot_x_center)
        ].copy()
        if candidates.empty:
            candidates = row_aisles.copy()

        candidates["aisle_center_y"] = candidates["y"] + candidates["length"] / 2
        candidates["distance_to_slot_center"] = (candidates["aisle_center_y"] - slot_y_center).abs()
        aisle = candidates.sort_values(["distance_to_slot_center", "aisle_center_y", "aisle_id"]).iloc[0]
        pick_x = slot_x_center
        pick_y = float(aisle["aisle_center_y"])

        rows.append(
            {
                "slot_id": str(slot["slot_id"]),
                "pick_node_id": f"PICK_{slot['slot_id']}",
                "aisle_id": str(aisle["aisle_id"]),
                "pick_x": round(pick_x, 3),
                "pick_y": round(pick_y, 3),
                "slot_x_center": round(slot_x_center, 3),
                "slot_y_center": round(slot_y_center, 3),
                "access_distance": round(abs(pick_y - slot_y_center), 3),
            }
        )

    return pd.DataFrame(rows).sort_values("slot_id").reset_index(drop=True)


def write_pick_points(
    slots_path: str | Path,
    aisles_path: str | Path,
    output_path: Optional[str | Path] = None,
) -> pd.DataFrame:
    pick_points = build_pick_points(slots_path, aisles_path)
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        pick_points.to_csv(output_path, index=False)
    return pick_points
