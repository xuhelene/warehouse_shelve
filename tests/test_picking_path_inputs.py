from io import BytesIO
from pathlib import Path
from collections import defaultdict
from decimal import Decimal

import pandas as pd
import pytest

from modules.picking_path.input_service import load_input
from modules.slotting.input_service import load_input as load_inbound


def test_outbound_sample_is_selected_day_only():
    data = load_input("orders")
    assert data["count"] == 1
    assert all(row["operation"] == "出库" and row["timestamp"].startswith("2026-09-09") for row in data["rows"])
    assert load_inbound("orders")["count"] == 7


def test_inventory_matches_inbound_page():
    assert load_input("inventory") == load_inbound("inventory")


def test_sample_outbound_demand_is_fully_covered_by_available_inventory():
    stock, demand = defaultdict(Decimal), defaultdict(Decimal)
    fields = ("warehouse_code", "owner_code", "sku_id", "base_unit", "conversion_ratio")
    for row in load_input("inventory")["rows"]:
        if not row["staging"] and row["slot_id"]:
            stock[tuple(row[f] for f in fields)] += Decimal(row["available_base_quantity"])
    for row in load_input("orders")["rows"]:
        demand[tuple(row[f] for f in fields)] += Decimal(row["base_quantity"])
    assert demand
    assert all(stock[key] >= quantity for key, quantity in demand.items())


def test_uploaded_orders_filter_direction_without_date_limit():
    content = Path("data/slotting/orders.csv").read_bytes()
    data = load_input("orders", content, "orders.csv")
    assert data["count"] > 24
    assert all(row["operation"] == "出库" for row in data["rows"])


def test_excel_preserves_outbound_quantities_and_identifiers():
    frame = pd.read_csv("data/slotting/orders.csv", dtype=str).head(2)
    stream = BytesIO()
    frame.to_excel(stream, index=False)
    rows = load_input("orders", stream.getvalue(), "orders.xlsx")["rows"]
    assert rows[0]["sku_id"] == frame.iloc[0]["货品编码"]
    assert rows[0]["quantity"] == frame.iloc[0]["实际操作数量"]
    assert rows[0]["base_quantity"] == frame.iloc[0]["主单位数量"]


def test_inbound_only_upload_is_not_outbound_demand():
    frame = pd.read_csv("data/slotting/orders.csv", dtype=str)
    content = frame.loc[frame["操作类型"].eq("入库")].to_csv(index=False).encode()
    with pytest.raises(ValueError, match="没有符合条件的出库订单"):
        load_input("orders", content, "inbound.csv")
