from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two slotting solutions with unified business metrics."
    )
    parser.add_argument("--solution-a", required=True, help="Path to the first solution CSV")
    parser.add_argument("--solution-b", required=True, help="Path to the second solution CSV")
    parser.add_argument("--label-a", default="solution_a", help="Display name for solution A")
    parser.add_argument("--label-b", default="solution_b", help="Display name for solution B")
    parser.add_argument("--slots", default="data/slotting/slots.csv", help="Path to slots.csv")
    parser.add_argument("--items", default="data/slotting/items_virtual.csv", help="Path to items_virtual.csv")
    parser.add_argument(
        "--alpha-slots",
        type=float,
        default=0.01,
        help="Alpha used by the slotting objective for slot activation penalty",
    )
    parser.add_argument(
        "--top-diff-items",
        type=int,
        default=10,
        help="How many most-changed items to print",
    )
    return parser.parse_args()


def _require_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _normalize_items(items_path: str) -> pd.DataFrame:
    items = pd.read_csv(items_path).copy()
    _require_columns(
        items,
        {"sku_id", "batch_id", "qty_boxes"},
        "items_virtual.csv",
    )
    items["sku_id"] = items["sku_id"].astype(str)
    items["batch_id"] = items["batch_id"].astype(str)
    items["item_id"] = items["sku_id"] + "||" + items["batch_id"]
    items["qty_boxes"] = pd.to_numeric(items["qty_boxes"], errors="coerce").fillna(0).astype(int)

    if "monthly_outbound_boxes" in items.columns:
        items["monthly_outbound_boxes"] = (
            pd.to_numeric(items["monthly_outbound_boxes"], errors="coerce").fillna(1.0).astype(float)
        )
        items.loc[items["monthly_outbound_boxes"] <= 0, "monthly_outbound_boxes"] = 1.0
    else:
        items["monthly_outbound_boxes"] = 1.0
    return items


def _normalize_slots(slots_path: str) -> pd.DataFrame:
    slots = pd.read_csv(slots_path).copy()
    _require_columns(slots, {"slot_id", "slot_cost"}, "slots.csv")
    slots["slot_id"] = slots["slot_id"].astype(str)
    slots["slot_cost"] = pd.to_numeric(slots["slot_cost"], errors="coerce").astype(float)
    return slots


def _normalize_solution(path: str, name: str) -> pd.DataFrame:
    sol = pd.read_csv(path).copy()
    required = {"slot_id", "qty_boxes_in_slot", "pallets_used_in_slot"}
    if "item_id" not in sol.columns:
        if {"sku_id", "batch_id"}.issubset(sol.columns):
            sol["sku_id"] = sol["sku_id"].astype(str)
            sol["batch_id"] = sol["batch_id"].astype(str)
            sol["item_id"] = sol["sku_id"] + "||" + sol["batch_id"]
        else:
            raise ValueError(
                f"{name} must contain item_id, or both sku_id and batch_id."
            )

    _require_columns(sol, required | {"item_id"}, name)
    sol["item_id"] = sol["item_id"].astype(str)
    sol["slot_id"] = sol["slot_id"].astype(str)
    sol["qty_boxes_in_slot"] = pd.to_numeric(sol["qty_boxes_in_slot"], errors="coerce").fillna(0).astype(int)
    sol["pallets_used_in_slot"] = (
        pd.to_numeric(sol["pallets_used_in_slot"], errors="coerce").fillna(0).astype(int)
    )
    return sol


def analyze_solution(
    solution_path: str,
    label: str,
    slots: pd.DataFrame,
    items: pd.DataFrame,
    alpha_slots: float,
) -> tuple[pd.DataFrame, dict]:
    sol = _normalize_solution(solution_path, label)

    slot_ref = slots[["slot_id", "slot_cost"]].drop_duplicates()
    item_ref = items[["item_id", "qty_boxes", "monthly_outbound_boxes"]].drop_duplicates()

    unknown_items = sorted(set(sol["item_id"]) - set(item_ref["item_id"]))
    unknown_slots = sorted(set(sol["slot_id"]) - set(slot_ref["slot_id"]))

    merged = sol.merge(item_ref, on="item_id", how="left").merge(slot_ref, on="slot_id", how="left")
    merged["weighted_cost_component"] = (
        merged["slot_cost"] * merged["monthly_outbound_boxes"] * merged["pallets_used_in_slot"]
    )
    merged["slot_penalty_component"] = alpha_slots

    assigned = (
        merged.groupby("item_id", as_index=False)["qty_boxes_in_slot"]
        .sum()
        .rename(columns={"qty_boxes_in_slot": "assigned_boxes"})
    )
    item_check = item_ref.merge(assigned, on="item_id", how="left")
    item_check["assigned_boxes"] = item_check["assigned_boxes"].fillna(0).astype(int)
    item_check["gap_boxes"] = item_check["assigned_boxes"] - item_check["qty_boxes"]
    item_check["slot_count"] = merged.groupby("item_id")["slot_id"].nunique().reindex(
        item_check["item_id"], fill_value=0
    ).to_list()

    multi_item_slots = (
        merged.groupby("slot_id")["item_id"].nunique().reset_index(name="item_count_in_slot")
    )
    overused_slots = multi_item_slots[multi_item_slots["item_count_in_slot"] > 1].copy()

    metrics = {
        "label": label,
        "rows": int(len(merged)),
        "used_slots": int(merged["slot_id"].nunique()),
        "assigned_items": int(merged["item_id"].nunique()),
        "total_boxes_assigned": int(merged["qty_boxes_in_slot"].sum()),
        "total_pallets_used": int(merged["pallets_used_in_slot"].sum()),
        "weighted_cost_component": float(merged["weighted_cost_component"].sum()),
        "slot_penalty_component": float(alpha_slots * len(merged)),
        "objective_value": float(merged["weighted_cost_component"].sum() + alpha_slots * len(merged)),
        "perfect_item_balance": bool((item_check["gap_boxes"] == 0).all()),
        "imbalanced_item_count": int((item_check["gap_boxes"] != 0).sum()),
        "max_abs_item_gap": int(item_check["gap_boxes"].abs().max()) if not item_check.empty else 0,
        "split_item_count": int((item_check["slot_count"] > 1).sum()),
        "max_slots_per_item": int(item_check["slot_count"].max()) if not item_check.empty else 0,
        "avg_slots_per_item": float(item_check["slot_count"].mean()) if not item_check.empty else 0.0,
        "unknown_item_count": int(len(unknown_items)),
        "unknown_slot_count": int(len(unknown_slots)),
        "multi_item_slot_count": int(len(overused_slots)),
    }

    diagnostics = {
        "item_check": item_check.sort_values(["gap_boxes", "item_id"], ascending=[False, True]),
        "overused_slots": overused_slots.sort_values("item_count_in_slot", ascending=False),
        "unknown_items": unknown_items,
        "unknown_slots": unknown_slots,
    }
    return merged, {"metrics": metrics, "diagnostics": diagnostics}


def build_diff_summary(sol_a: pd.DataFrame, sol_b: pd.DataFrame, top_n: int) -> pd.DataFrame:
    a_pairs = sol_a[["item_id", "slot_id", "qty_boxes_in_slot", "pallets_used_in_slot"]].copy()
    b_pairs = sol_b[["item_id", "slot_id", "qty_boxes_in_slot", "pallets_used_in_slot"]].copy()
    a_pairs = a_pairs.rename(
        columns={
            "qty_boxes_in_slot": "qty_a",
            "pallets_used_in_slot": "pallets_a",
        }
    )
    b_pairs = b_pairs.rename(
        columns={
            "qty_boxes_in_slot": "qty_b",
            "pallets_used_in_slot": "pallets_b",
        }
    )

    diff = a_pairs.merge(b_pairs, on=["item_id", "slot_id"], how="outer")
    diff[["qty_a", "pallets_a", "qty_b", "pallets_b"]] = diff[
        ["qty_a", "pallets_a", "qty_b", "pallets_b"]
    ].fillna(0)
    diff["qty_delta"] = diff["qty_a"] - diff["qty_b"]
    diff["pallet_delta"] = diff["pallets_a"] - diff["pallets_b"]
    diff["changed"] = (diff["qty_delta"] != 0) | (diff["pallet_delta"] != 0)

    item_change = (
        diff[diff["changed"]]
        .groupby("item_id", as_index=False)
        .agg(
            changed_slots=("slot_id", "nunique"),
            total_abs_qty_delta=("qty_delta", lambda s: int(s.abs().sum())),
            total_abs_pallet_delta=("pallet_delta", lambda s: int(s.abs().sum())),
        )
        .sort_values(["total_abs_qty_delta", "changed_slots", "item_id"], ascending=[False, False, True])
        .head(top_n)
    )
    return item_change


def print_metrics(result: dict) -> None:
    metrics = result["metrics"]
    print(f"\n=== {metrics['label']} ===")
    print(f"objective_value: {metrics['objective_value']:.4f}")
    print(f"weighted_cost_component: {metrics['weighted_cost_component']:.4f}")
    print(f"slot_penalty_component: {metrics['slot_penalty_component']:.4f}")
    print(f"used_slots: {metrics['used_slots']}")
    print(f"assigned_items: {metrics['assigned_items']}")
    print(f"total_boxes_assigned: {metrics['total_boxes_assigned']}")
    print(f"total_pallets_used: {metrics['total_pallets_used']}")
    print(f"split_item_count: {metrics['split_item_count']}")
    print(f"max_slots_per_item: {metrics['max_slots_per_item']}")
    print(f"avg_slots_per_item: {metrics['avg_slots_per_item']:.4f}")
    print(f"perfect_item_balance: {metrics['perfect_item_balance']}")
    print(f"imbalanced_item_count: {metrics['imbalanced_item_count']}")
    print(f"unknown_item_count: {metrics['unknown_item_count']}")
    print(f"unknown_slot_count: {metrics['unknown_slot_count']}")
    print(f"multi_item_slot_count: {metrics['multi_item_slot_count']}")


def print_diagnostics(label: str, diagnostics: dict) -> None:
    if diagnostics["unknown_items"]:
        print(f"\n{label} unknown items: {diagnostics['unknown_items'][:10]}")
    if diagnostics["unknown_slots"]:
        print(f"\n{label} unknown slots: {diagnostics['unknown_slots'][:10]}")

    bad_items = diagnostics["item_check"][diagnostics["item_check"]["gap_boxes"] != 0].head(10)
    if not bad_items.empty:
        print(f"\n{label} items with assignment mismatch:")
        print(bad_items.to_string(index=False))

    bad_slots = diagnostics["overused_slots"].head(10)
    if not bad_slots.empty:
        print(f"\n{label} slots used by multiple items:")
        print(bad_slots.to_string(index=False))


def main() -> None:
    args = parse_args()
    slots = _normalize_slots(args.slots)
    items = _normalize_items(args.items)

    sol_a, res_a = analyze_solution(
        solution_path=args.solution_a,
        label=args.label_a,
        slots=slots,
        items=items,
        alpha_slots=args.alpha_slots,
    )
    sol_b, res_b = analyze_solution(
        solution_path=args.solution_b,
        label=args.label_b,
        slots=slots,
        items=items,
        alpha_slots=args.alpha_slots,
    )

    print_metrics(res_a)
    print_metrics(res_b)

    obj_a = res_a["metrics"]["objective_value"]
    obj_b = res_b["metrics"]["objective_value"]
    winner = args.label_a if obj_a < obj_b else args.label_b if obj_b < obj_a else "tie"

    print("\n=== Comparison Summary ===")
    print(f"objective_delta({args.label_a} - {args.label_b}): {obj_a - obj_b:.4f}")
    print(f"better_solution_by_objective: {winner}")
    print(
        f"used_slots_delta({args.label_a} - {args.label_b}): "
        f"{res_a['metrics']['used_slots'] - res_b['metrics']['used_slots']}"
    )
    print(
        f"total_pallets_delta({args.label_a} - {args.label_b}): "
        f"{res_a['metrics']['total_pallets_used'] - res_b['metrics']['total_pallets_used']}"
    )
    print(
        f"split_item_delta({args.label_a} - {args.label_b}): "
        f"{res_a['metrics']['split_item_count'] - res_b['metrics']['split_item_count']}"
    )

    item_diff = build_diff_summary(sol_a, sol_b, args.top_diff_items)
    if not item_diff.empty:
        print("\n=== Top Changed Items ===")
        print(item_diff.to_string(index=False))

    print_diagnostics(args.label_a, res_a["diagnostics"])
    print_diagnostics(args.label_b, res_b["diagnostics"])


if __name__ == "__main__":
    main()
