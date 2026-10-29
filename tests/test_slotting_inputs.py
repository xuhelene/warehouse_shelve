from io import BytesIO

import pandas as pd
import pytest

from modules.slotting.input_service import load_input


def test_inventory_sample_retains_fifo_and_original_quantities():
    data = load_input("inventory")
    assert data["count"] == 274
    assert not data["warnings"]
    first = data["rows"][0]
    assert first["slot_id"] == "C15-45-1"
    assert first["quantity"] == "0.167"
    assert first["base_quantity"] == "1"
    assert first["timestamp"] == "2026-08-31T14:57:00"
    staging = [row for row in data["rows"] if row["staging"]]
    assert len(staging) == 4
    assert all(row["slot_id"] is None for row in staging)


def test_sample_orders_only_selected_day_inbound():
    data = load_input("orders")
    assert data["count"] == 7
    assert all(row["operation"] == "入库" and row["timestamp"].startswith("2026-09-22") for row in data["rows"])


def test_uploaded_orders_keep_other_dates():
    content = open("data/slotting/orders.csv", "rb").read()
    data = load_input("orders", content, "orders.csv")
    assert data["count"] > 7
    assert all(row["operation"] == "入库" for row in data["rows"])


def test_xlsx_and_csv_use_same_normalization():
    frame = pd.read_csv("data/slotting/existing_inventory.csv", dtype=str).head(2)
    stream = BytesIO()
    frame.to_excel(stream, index=False)
    actual = load_input("inventory", stream.getvalue(), "inventory.xlsx")
    assert actual["rows"] == load_input("inventory")["rows"][:2]


def test_missing_columns_are_reported():
    with pytest.raises(ValueError, match="缺少字段"):
        load_input("inventory", "货品编码\n001\n".encode(), "invalid.csv")


def test_invalid_conversion_is_not_defaulted():
    frame = pd.read_csv("data/slotting/existing_inventory.csv", dtype=str).head(1)
    frame["转换比"] = "0"
    with pytest.raises(ValueError, match="转换比"):
        load_input("inventory", frame.to_csv(index=False).encode(), "invalid.csv")
