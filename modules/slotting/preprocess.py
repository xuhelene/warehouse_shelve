from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict
import numpy as np
import pandas as pd

from core.constants import boxes_per_pallet, default_slot_pallet_cap
from core.models import SolverConfig


def build_sku_metrics_from_outbound_orders(
    orders: pd.DataFrame,
    date_col: str = "订单下达时间",
    sku_col: str = "产品编码",
    qty_col: str = "合格品",
    cover_days: int = 14,
    robust_days: int = 60,
    min_stock_boxes: int = 0,
) -> pd.DataFrame:
    df = orders.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df[sku_col] = df[sku_col].astype(str)
    df[qty_col] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0).astype(float)

    end = df[date_col].max()
    start = end - pd.Timedelta(days=robust_days - 1)
    dfr = df[df[date_col].between(start, end)].copy()
    if dfr.empty:
        raise ValueError("Outbound orders are empty in the selected robust_days window.")

    daily = dfr.groupby([sku_col, date_col], as_index=False)[qty_col].sum()
    daily.rename(columns={qty_col: "out_boxes"}, inplace=True)

    span_days = max((end - start).days + 1, 1)
    out_sum = daily.groupby(sku_col)["out_boxes"].sum().rename("out_sum").reset_index()
    out_sum["avg_daily_out"] = out_sum["out_sum"] / span_days
    out_sum["monthly_outbound_boxes"] = out_sum["avg_daily_out"] * 30.0
    out_sum["qty_boxes_est"] = np.ceil(out_sum["avg_daily_out"] * cover_days).astype(int)

    if min_stock_boxes > 0:
        out_sum["qty_boxes_est"] = out_sum["qty_boxes_est"].clip(lower=int(min_stock_boxes))

    return out_sum[[sku_col, "monthly_outbound_boxes", "qty_boxes_est"]].rename(columns={sku_col: "sku_id"})


def auto_candidate_slot_limit(
    items: pd.DataFrame,
    slots: pd.DataFrame,
    forbidden_count: int = 0,
) -> Optional[int]:
    """最优解只会用到最便宜的若干个货位，返回这个数量作为候选裁剪上限。

    交换论证：每个货位至多放一个 item，若最优解用了第 k 便宜的货位而某个更便宜的
    货位空着，把它换过去成本严格下降 —— 前提是两个货位容量相同，所以 pallet_cap
    不同构时返回 None（不裁剪）。所需货位数 = Σ ⌈P_i / cap⌉，再为 forbidden 可能
    把某些 item 顶到更贵货位上留出等量余量。
    """
    caps = slots["pallet_cap"].unique()
    if len(caps) != 1 or int(caps[0]) <= 0:
        return None
    cap = int(caps[0])
    need_pallets = np.ceil(items["qty_boxes"] / items["boxes_per_pallet"])
    return int(np.ceil(need_pallets / cap).sum()) + int(forbidden_count)


@dataclass
class PickingGeometry:
    """拣货几何：货位 -> 所属拣货通道 / 拣货点 x，以及通道 y 和唯一横通道的 x。

    货位的 (x, y) 是货架本身的坐标，不是拣货员站的位置：2C1 与 2C2 背靠背共用
    A_ROW_2C1_2C2 这条通道，y 相差 9 但拣货点重合，实际步行为 0；而 2C2 与 2C3
    y 只差 7，却必须绕到唯一的横通道才能过去。所以邻近性必须按拣货通道算，
    不能按货架坐标的 L1 距离算。
    """

    slot_aisle: Dict[str, str]
    slot_pick_x: Dict[str, float]
    aisle_y: Dict[str, float]
    cross_x: float


def load_picking_geometry(
    picking_points_path: Optional[str],
    picking_aisles_path: Optional[str],
) -> Tuple[Optional[PickingGeometry], str]:
    """读取拣货几何；不可用时返回 (None, 原因)，调用方据此退回货架坐标 L1 度量。"""
    if not picking_points_path or not picking_aisles_path:
        return None, "picking geometry paths not provided"
    for path in (picking_points_path, picking_aisles_path):
        if not Path(path).exists():
            return None, f"file not found: {path}"

    points = pd.read_csv(picking_points_path)
    aisles = pd.read_csv(picking_aisles_path)
    need_points = {"slot_id", "aisle_id", "pick_x", "pick_y"}
    need_aisles = {"aisle_id", "aisle_type", "x"}
    if need_points - set(points.columns):
        return None, f"picking_points.csv missing columns: {sorted(need_points - set(points.columns))}"
    if need_aisles - set(aisles.columns):
        return None, f"picking_aisles.csv missing columns: {sorted(need_aisles - set(aisles.columns))}"

    cross = aisles[aisles["aisle_type"].astype(str).eq("cross")]
    # 多条横通道时"绕哪一条"本身是个选择，线性的 Steiner 形式不再成立；
    # 这里不猜，直接退回旧度量并在 meta 里说明。
    if len(cross) != 1:
        return None, f"expected exactly 1 cross aisle, found {len(cross)}"

    points["slot_id"] = points["slot_id"].astype(str)
    points["aisle_id"] = points["aisle_id"].astype(str)
    aisle_y = points.groupby("aisle_id")["pick_y"].agg(["min", "max"])
    if not np.isclose(aisle_y["min"], aisle_y["max"]).all():
        return None, "each aisle must have a single pick_y"

    return (
        PickingGeometry(
            slot_aisle=dict(zip(points["slot_id"], points["aisle_id"])),
            slot_pick_x=dict(zip(points["slot_id"], points["pick_x"].astype(float))),
            aisle_y={str(k): float(v) for k, v in aisle_y["min"].items()},
            cross_x=float(cross["x"].iloc[0]),
        ),
        "ok",
    )


def multi_slot_item_ids(items: pd.DataFrame, slots: pd.DataFrame) -> set:
    """可能需要占用多个货位的 item。按最小 pallet_cap 判定，宁可多判不可漏判。"""
    min_cap = int(slots["pallet_cap"].min())
    if min_cap <= 0:
        return set(items["item_id"].astype(str))
    need_pallets = np.ceil(items["qty_boxes"] / items["boxes_per_pallet"])
    return set(items.loc[need_pallets > min_cap, "item_id"].astype(str))


def prepare_slotting_data(
    slots_path: str,
    items_virtual_path: str,
    cfg: Optional[SolverConfig] = None,
    use_demand_if_present: bool = True,
    candidate_slots_per_item: Optional[int] = None,
    occupied_slot_ids: Optional[set[str]] = None,
    auto_prune_slots: bool = True,
    full_candidates_for_multi_slot: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if cfg is None:
        cfg = SolverConfig()

    slots = pd.read_csv(slots_path)
    items = pd.read_csv(items_virtual_path)

    if "slot_id" not in slots.columns:
        raise ValueError("slots.csv must contain slot_id")
    if "is_storage" not in slots.columns:
        slots["is_storage"] = 1
    slots["is_storage"] = pd.to_numeric(slots["is_storage"], errors="coerce").fillna(1).astype(int)
    slots = slots[slots["is_storage"].eq(1)].copy()
    if slots.empty:
        raise ValueError("slots.csv must contain at least one storage row with is_storage=1")
    if "slot_cost" not in slots.columns:
        raise ValueError("slots.csv must contain slot_cost")

    slots["slot_id"] = slots["slot_id"].astype(str)
    slots["slot_cost"] = pd.to_numeric(slots["slot_cost"], errors="coerce").astype(float)
    if "pallet_cap" not in slots.columns:
        slots["pallet_cap"] = default_slot_pallet_cap()
    slots["pallet_cap"] = pd.to_numeric(slots["pallet_cap"], errors="coerce").astype(int)

    req_items = {"sku_id", "batch_id", "qty_boxes", "box_l", "box_w", "box_h"}
    missing = req_items - set(items.columns)
    if missing:
        raise ValueError(f"items_virtual.csv missing columns: {missing}")

    items["sku_id"] = items["sku_id"].astype(str)
    items["batch_id"] = items["batch_id"].astype(str)
    items["item_id"] = items["sku_id"] + "||" + items["batch_id"]
    items["qty_boxes"] = pd.to_numeric(items["qty_boxes"], errors="coerce").fillna(0).astype(int)

    demand_available = use_demand_if_present and ("monthly_outbound_boxes" in items.columns)
    if demand_available:
        items["monthly_outbound_boxes"] = pd.to_numeric(items["monthly_outbound_boxes"], errors="coerce").fillna(1.0).astype(float)
        items.loc[items["monthly_outbound_boxes"] <= 0, "monthly_outbound_boxes"] = 1.0

    items["boxes_per_pallet"] = items.apply(
        lambda r: boxes_per_pallet(float(r["box_l"]), float(r["box_w"]), float(r["box_h"])), axis=1
    )

    if (items["boxes_per_pallet"] <= 0).any():
        bad = items.loc[items["boxes_per_pallet"] <= 0, ["sku_id", "batch_id", "box_l", "box_w"]].head(10)
        raise ValueError(f"Some boxes do not fit on a pallet at all. Examples:\n{bad}")

    slots = slots[~slots["slot_id"].isin(occupied_slot_ids or set())].copy()
    slot_subset = slots.sort_values("slot_cost").copy()
    slot_limit = candidate_slots_per_item
    if slot_limit is None and auto_prune_slots:
        slot_limit = auto_candidate_slot_limit(items, slot_subset, len(cfg.forbidden))
    item_cols = ["item_id", "sku_id", "batch_id", "qty_boxes", "boxes_per_pallet"]
    slot_cols = ["slot_id", "pallet_cap", "slot_cost"]
    if slot_limit is None:
        cand = items[item_cols].merge(slot_subset[slot_cols], how="cross")
    else:
        cheap = slot_subset.head(int(slot_limit))
        fixed_slot_ids = {str(x.slot_id) for x in cfg.fixed}
        if fixed_slot_ids:
            # fixed 指定的货位可能很贵、落在 head 之外，必须补回来，否则该 item 无候选可用
            forced = slot_subset[slot_subset["slot_id"].isin(fixed_slot_ids)]
            cheap = pd.concat([cheap, forced]).drop_duplicates(subset="slot_id")
        # 开了邻近性惩罚时，需要拆到多个货位的 item 可能宁愿选更贵但挨着的货位，
        # 交换论证对它们不成立，保留全部候选；单货位 item 的 span 恒为 0，照裁不误。
        multi_ids = multi_slot_item_ids(items, slot_subset) if full_candidates_for_multi_slot else set()
        is_multi = items["item_id"].astype(str).isin(multi_ids)
        cand = items.loc[~is_multi, item_cols].merge(cheap[slot_cols], how="cross")
        if is_multi.any():
            cand = pd.concat(
                [cand, items.loc[is_multi, item_cols].merge(slot_subset[slot_cols], how="cross")],
                ignore_index=True,
            )
    cand["cap_boxes"] = cand["boxes_per_pallet"] * cand["pallet_cap"]
    cand = cand[cand["cap_boxes"] > 0].copy()

    if cfg.forbidden:
        forbidden_keys = {f"{x.sku_id}||{x.batch_id}\t{x.slot_id}" for x in cfg.forbidden}
        cand_keys = cand["item_id"].astype(str) + "\t" + cand["slot_id"].astype(str)
        cand = cand[~cand_keys.isin(forbidden_keys)].copy()

    fixed_map: Dict[str, str] = {f"{x.sku_id}||{x.batch_id}": str(x.slot_id) for x in cfg.fixed}
    if fixed_map:
        item_ids = cand["item_id"].astype(str)
        slot_ids = cand["slot_id"].astype(str)
        is_fixed_item = item_ids.isin(fixed_map.keys())
        required_slot = item_ids.map(fixed_map)
        # 未被 fixed 的 item 全部保留；被 fixed 的 item 只保留 slot 等于指定值的候选。
        cand = cand[~is_fixed_item | slot_ids.eq(required_slot)].copy()

    qty_by_item = items.set_index("item_id")["qty_boxes"]
    max_cap_by_item = cand.groupby("item_id")["cap_boxes"].max().reindex(qty_by_item.index)

    no_candidate_items = max_cap_by_item[max_cap_by_item.isna() | (max_cap_by_item <= 0)].index.tolist()
    if no_candidate_items:
        sample = no_candidate_items[:5]
        raise ValueError(
            f"{len(no_candidate_items)} item(s) have no usable candidate slots after applying "
            f"forbidden/fixed/capacity filters. Examples: {sample}. "
            "Loosen forbidden constraints, raise pallet_cap, or expand candidate_slots_per_item."
        )

    need_lb = int(np.sum(np.ceil(qty_by_item / max_cap_by_item)))
    if need_lb > len(slots):
        raise ValueError(
            f"Likely infeasible: lower bound slots needed {need_lb} > available slots {len(slots)}. "
            f"Reduce qty_boxes, reduce cover_days, or increase pallet_cap if physically valid."
        )

    return slots, items, cand
