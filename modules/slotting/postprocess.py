import pandas as pd


def enrich_solution(sol: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    demand_cols = ["monthly_outbound_boxes"] if "monthly_outbound_boxes" in items.columns else []
    sol = sol.merge(
        items[["item_id", "sku_id", "batch_id", "qty_boxes", "boxes_per_pallet"] + demand_cols],
        on="item_id",
        how="left",
    )
    check = sol.groupby("item_id")["qty_boxes_in_slot"].sum().rename("assigned_boxes").reset_index()
    sol = sol.merge(check, on="item_id", how="left")
    return sol
