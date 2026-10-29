from datetime import date
from io import BytesIO
import json

import pandas as pd
import pytest

from core.sku_turnover import calculate, update


def orders(rows):
    return pd.DataFrame(rows, columns=["操作类型", "操作时间", "仓库编码", "货品编码", "主单位数量", "转换率", "货主编码"])


def test_window_conversion_owners_ties_and_warehouse_ranking():
    frame = orders([
        ["出库", "2026-09-11", "W", "001", "3", "6", "A"],
        ["出库", "2026-10-10 23:59:59", "W", "001", "3", "6", "B"],
        ["出库", "2026-10-10", "W", "002", "1", "1", "A"],
        ["出库", "2026-10-10", "W", "003", "0.5", "1", "A"],
        ["出库", "2026-10-10", "W", "004", "0.1", "1", "A"],
        ["出库", "2026-10-10", "Z", "005", "1", "1", "A"],
        ["出库", "2026-09-10", "W", "outside", "999", "1", "A"],
        ["出库", "2026-10-11", "W", "future", "999", "1", "A"],
        ["入库", "2026-10-10", "W", "inbound", "999", "1", "A"],
    ])
    # 保持样例格式一致。
    frame["操作时间"] = frame["操作时间"].map(lambda x: x if " " in x else x + " 00:00:00")
    result = calculate(frame.to_csv(index=False).encode(), "orders.csv", date(2026, 10, 10))
    assert result["start_date"] == "2026-09-11"
    assert result["outbound_rows"] == 6
    assert [r["sku_id"] for r in result["rows"]] == ["inbound", "002", "001", "001", "003", "004", "005"]
    assert [r["owner_code"] for r in result["rows"]] == ["A", "A", "A", "B", "A", "A", "A"]
    assert [r["grade"] for r in result["rows"]] == ["高", "低", "低", "低", "低", "低", "高"]
    assert result["rows"][2]["outbound_boxes"] == "0.5"
    assert result["rows"][3]["outbound_boxes"] == "0.5"
    assert result["rows"][0]["movement_boxes"] == "999"


def test_owner_required_and_same_owner_rows_are_summed():
    frame = orders([["出库", "2026-10-10", "W", "001", "3", "6", "A"]] * 2)
    result = calculate(frame.to_csv(index=False).encode(), "x.csv", None)
    assert result["sku_count"] == 1
    assert result["rows"][0]["outbound_boxes"] == "1.0"
    frame.loc[0, "货主编码"] = " "
    with pytest.raises(ValueError, match="货主编码.*不能为空"):
        calculate(frame.to_csv(index=False).encode(), "x.csv", None)
    with pytest.raises(ValueError, match="缺少字段.*货主编码"):
        calculate(frame.drop(columns="货主编码").to_csv(index=False).encode(), "x.csv", None)


def test_xlsx_and_csv_match_and_three_grades():
    frame = orders([["出库", "2026-10-10", "W", str(i), str(10-i), "1", "A"] for i in range(6)])
    csv_result = calculate(frame.to_csv(index=False).encode(), "x.csv", None)
    stream = BytesIO()
    frame.to_excel(stream, index=False)
    assert calculate(stream.getvalue(), "x.xlsx", None) == csv_result
    assert [row["grade"] for row in csv_result["rows"]] == ["高", "高", "高", "高", "高", "中"]


@pytest.mark.parametrize("qty,ratio", [("-1", "1"), ("1", "0"), ("NaN", "1"), ("1", "Infinity")])
def test_invalid_numbers(qty, ratio):
    frame = orders([["出库", "2026-10-10", "W", "001", qty, ratio, "A"]])
    with pytest.raises(ValueError, match="无效"):
        calculate(frame.to_csv(index=False).encode(), "x.csv", None)


def test_preserve_previous_result_when_update_fails(tmp_path):
    result = update(output_root=tmp_path)
    assert result["end_date"] == "2026-10-10"
    assert (tmp_path / result["run_id"] / "orders.csv").exists()
    with pytest.raises(ValueError):
        update(b"bad\n1", "bad.csv", output_root=tmp_path)
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == result


def test_inbound_only_history_and_contribution_thresholds():
    frame = orders([["入库", "2026-10-10", "W", str(i), str(qty), "1", "A"] for i, qty in enumerate([80, 10, 5, 5, 0])])
    result = calculate(frame.to_csv(index=False).encode(), "x.csv", None)
    assert result["outbound_rows"] == 0
    assert [r["grade"] for r in result["rows"]] == ["高", "中", "中", "低", "低"]
