"""按近30天入库+出库箱数累计贡献80%/95%计算周转等级。"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
from uuid import uuid4

import pandas as pd

from core.paths import default_input_path, runtime_path


def calculate(content: bytes, filename: str, end_date: date | None) -> dict:
    if filename.lower().endswith(".xlsx"):
        frame = pd.read_excel(BytesIO(content), dtype=str).fillna("")
    elif filename.lower().endswith(".csv"):
        try:
            frame = pd.read_csv(BytesIO(content), dtype=str, encoding="utf-8-sig").fillna("")
        except UnicodeDecodeError:
            frame = pd.read_csv(BytesIO(content), dtype=str, encoding="gb18030").fillna("")
    else:
        raise ValueError("仅支持 CSV、XLSX")
    frame.columns = frame.columns.str.strip()
    required = {"操作类型", "操作时间", "仓库编码", "货主编码", "货品编码", "主单位数量", "转换率"}
    if required - set(frame.columns):
        raise ValueError("缺少字段：" + "、".join(sorted(required - set(frame.columns))))
    movements = frame.loc[frame["操作类型"].str.strip().isin(["入库", "出库"])]
    if movements.empty:
        raise ValueError("没有入库或出库记录")
    dates = pd.to_datetime(movements["操作时间"], errors="coerce")
    if dates.isna().any():
        raise ValueError("作业记录存在无效操作时间")
    end_date = end_date or dates.max().date()
    start_date = end_date - timedelta(days=29)
    selected = movements.loc[(dates.dt.date >= start_date) & (dates.dt.date <= end_date)]
    if selected.empty:
        raise ValueError("所选30天窗口没有入库或出库记录，未更新结果")
    totals, outbound_totals = {}, {}
    for index, row in selected.iterrows():
        warehouse, owner, sku = row["仓库编码"].strip(), row["货主编码"].strip(), row["货品编码"].strip()
        if not warehouse or not owner or not sku:
            raise ValueError(f"第 {index + 2} 行：仓库编码、货主编码、货品编码不能为空")
        try:
            qty, ratio = Decimal(row["主单位数量"]), Decimal(row["转换率"])
            if not qty.is_finite() or not ratio.is_finite() or qty < 0 or ratio <= 0:
                raise InvalidOperation
        except InvalidOperation as error:
            raise ValueError(f"第 {index + 2} 行：主单位数量或转换率无效") from error
        key = (warehouse, owner, sku)
        totals[key] = totals.get(key, Decimal(0)) + qty / ratio
        if row["操作类型"].strip() == "出库":
            outbound_totals[key] = outbound_totals.get(key, Decimal(0)) + qty / ratio
    rows = []
    for warehouse in sorted({key[0] for key in totals}):
        ranked = sorted(((owner, sku, qty) for (wh, owner, sku), qty in totals.items() if wh == warehouse),
                        key=lambda item: (-item[2], item[1], item[0]))
        total = sum(qty for _, _, qty in ranked)
        cumulative = Decimal(0)
        for rank, (owner, sku, qty) in enumerate(ranked, 1):
            grade = "低" if not qty or not total else "高" if cumulative < total * Decimal('.8') else "中" if cumulative < total * Decimal('.95') else "低"
            cumulative += qty
            rows.append(dict(warehouse_code=warehouse, owner_code=owner, sku_id=sku,
                             outbound_boxes=str(outbound_totals.get((warehouse, owner, sku), Decimal(0))),
                             movement_boxes=str(qty), rank=rank, grade=grade))
    return dict(start_date=start_date.isoformat(), end_date=end_date.isoformat(),
                outbound_rows=sum(selected["操作类型"].str.strip().eq("出库")), sku_count=len(rows), rows=rows,
                metric="入库+出库主单位数量÷转换率；1件=1箱；按仓库+货主+SKU分别统计",
                ranking="同仓库按搬运量降序，累计贡献80%/95%；跨阈值条目归入当前等级；零搬运量最低；同量按SKU、货主编码排序")


def update(content=None, filename="", end_date=None, *, output_root=None):
    sample = content is None
    if sample:
        path = default_input_path("data/slotting/orders.csv")
        content, filename = path.read_bytes(), path.name
    elif end_date is None:
        end_date = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    result = calculate(content, filename, end_date)
    result.update(run_id=uuid4().hex, source="sample" if sample else "upload",
                  filename=filename, updated_at=datetime.now(timezone.utc).isoformat())
    root = output_root or runtime_path("results/sku_turnover")
    folder = root / result["run_id"]
    folder.mkdir(parents=True)
    (folder / ("orders.xlsx" if filename.lower().endswith(".xlsx") else "orders.csv")).write_bytes(content)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (folder / "result.json").write_text(text, encoding="utf-8")
    temporary = root / (result["run_id"] + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(root / "latest.json")
    return result


def read_latest():
    path = runtime_path("results/sku_turnover/latest.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
