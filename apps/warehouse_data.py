"""入出库共用的文件解析与标准化，不依赖 HTTP 或平台响应格式。"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from io import BytesIO
import re
import pandas as pd
from core.paths import default_input_path

SAMPLE_PATHS = {"inventory": "data/slotting/existing_inventory.csv", "orders": "data/slotting/orders.csv"}


def load_input(kind: str, content: bytes | None = None, filename: str = "", *, operation: str = "入库") -> dict:
    if operation not in {"入库", "出库"}:
        raise ValueError("未知操作类型")
    if kind not in SAMPLE_PATHS:
        raise ValueError("未知数据类型")
    sample = content is None
    if sample:
        path = default_input_path(SAMPLE_PATHS[kind])
        content, filename = path.read_bytes(), path.name
    if filename.lower().endswith(".xlsx"):
        frame = pd.read_excel(BytesIO(content), dtype=str).fillna("")
    elif filename.lower().endswith(".csv"):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                frame = pd.read_csv(BytesIO(content), dtype=str, encoding=encoding).fillna("")
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("CSV 编码须为 UTF-8 或 GB18030")
    else:
        raise ValueError("仅支持 CSV、XLSX 文件")
    frame.columns = frame.columns.str.strip()
    fields = ({"货位编码", "货品编码", "货品名称", "入库日期", "库存总量(订单单位)", "订单单位", "转换比", "主单位数量", "主单位"}
              if kind == "inventory" else
              {"操作类型", "操作时间", "货品编码", "货品名称", "实际操作数量", "实际操作单位", "主单位数量", "主单位", "转换率"})
    missing = fields - set(frame.columns)
    if missing:
        raise ValueError("缺少字段：" + "、".join(sorted(missing)))
    if frame.empty:
        raise ValueError("文件中没有数据")
    if kind == "orders":
        dates = pd.to_datetime(frame["操作时间"], errors="coerce")
        selected = frame["操作类型"].str.strip().eq(operation)
        sample_date = "2026-09-09" if operation == "出库" else "2026-09-22"
        if sample:
            selected &= dates.dt.strftime("%Y-%m-%d").eq(sample_date)
        frame = frame.loc[selected]
        if frame.empty:
            raise ValueError(f"没有符合条件的{operation}订单" + (f"（{sample_date}）" if sample else ""))
    rows, warnings = [], []
    for index, row in frame.iterrows():
        time_field = "入库日期" if kind == "inventory" else "操作时间"
        timestamp = pd.to_datetime(row[time_field], errors="coerce")
        if pd.isna(timestamp):
            raise ValueError(f"第 {index + 2} 行：{time_field}无效")
        quantity_field = "库存总量(订单单位)" if kind == "inventory" else "实际操作数量"
        ratio_field = "转换比" if kind == "inventory" else "转换率"
        for field in (quantity_field, ratio_field, "主单位数量"):
            value = pd.to_numeric(row[field], errors="coerce")
            if pd.isna(value) or not float("-inf") < float(value) < float("inf") or value < 0 or (field == ratio_field and value == 0):
                raise ValueError(f"第 {index + 2} 行：{field}数值无效")
        if not str(row["货品编码"]).strip():
            raise ValueError(f"第 {index + 2} 行：货品编码不能为空")
        item = dict(sku_id=row["货品编码"], name=row["货品名称"], timestamp=timestamp.isoformat(),
                    quantity=row[quantity_field], unit=row["订单单位" if kind == "inventory" else "实际操作单位"],
                    conversion_ratio=row[ratio_field], base_quantity=row["主单位数量"], base_unit=row["主单位"])
        item.update(owner_code=str(row.get("货主编码", "")).strip(),
                    owner_name=str(row.get("货主名称", "")).strip(),
                    warehouse_code=str(row.get("仓库编码", "")).strip(),
                    order_no=str(row.get("关联单号", "")).strip(),
                    batch_no=str(row.get("批次号", "")).strip())
        if kind == "inventory":
            item["available_base_quantity"] = str(row.get("可用数量", "")).strip()
            item["available_quantity"] = str(row.get("可用量(订单单位)", "")).strip()
        if kind == "inventory":
            location = str(row["货位编码"]).strip()
            match = re.fullmatch(r"054-03-C-(14|15|16)-(\d{1,2})-(\d{1,2})-丙", location)
            slot_id = None
            if match:
                rack, column, level = map(int, match.groups())
                if 1 <= column <= 45 and 1 <= level <= 5 and not (20 <= column <= 22 and level <= 2):
                    slot_id = f"C{rack}-{column}-{level}"
            if location != "E001" and slot_id is None:
                warnings.append(f"第 {index + 2} 行：货位 {location} 无法映射到当前布局")
            item.update(location_code=location, slot_id=slot_id, staging=location == "E001")
        else:
            item["operation"] = row["操作类型"]
        rows.append(item)
    return {"kind": kind, "filename": filename, "source": "sample" if sample else "upload", "rows": rows,
            "count": len(rows), "warnings": warnings, "staging_count": sum(row.get("staging", False) for row in rows)}


def number(value, label, *, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}数值无效") from None


def load_specs(content=None, filename=""):
    if content is None:
        path = default_input_path("data/sku.xlsx")
        content, filename = path.read_bytes(), path.name
    if filename.lower().endswith(".xlsx"):
        frame = pd.read_excel(BytesIO(content), dtype=str).fillna("")
    elif filename.lower().endswith(".csv"):
        try:
            frame = pd.read_csv(BytesIO(content), dtype=str, encoding="utf-8-sig").fillna("")
        except UnicodeDecodeError:
            frame = pd.read_csv(BytesIO(content), dtype=str, encoding="gb18030").fillna("")
    else:
        raise ValueError("SKU规格仅支持 CSV、XLSX")
    frame.columns = frame.columns.str.strip()
    required = {"编码", "货品名称", "所属客户", "托含量"}
    if required - set(frame.columns):
        raise ValueError("SKU规格缺少字段：" + "、".join(sorted(required - set(frame.columns))))
    return dict(filename=filename, rows=frame.to_dict("records"), count=len(frame))


def _owner_name(value):
    return "".join(str(value).split()).replace("（", "(").replace("）", ")")


def prepare(inventory, orders, specs, layout, operation):
    if not orders:
        raise ValueError("请先加载订单")
    inventory, orders = deepcopy(inventory), deepcopy(orders)
    missing, capacities = {}, {}
    for row in inventory + orders:
        for key in ("warehouse_code", "owner_code", "sku_id", "timestamp", "base_unit"):
            if not str(row.get(key, "")).strip():
                raise ValueError(f"{row.get('sku_id', '')} 缺少 {key}")
        try:
            stamp = pd.Timestamp(row["timestamp"])
            if pd.isna(stamp) or stamp.tzinfo is not None:
                raise ValueError
            row["timestamp"] = stamp.isoformat()
        except (ValueError, TypeError):
            raise ValueError("库存或订单时间无效；请使用本地时间") from None
        row["base_quantity"] = number(row.get("base_quantity"), "主单位数量")
        row["conversion_ratio"] = number(row.get("conversion_ratio"), "转换比", positive=True)
        row["boxes"] = row["base_quantity"] / row["conversion_ratio"]
    warehouses = {r["warehouse_code"] for r in inventory + orders}
    if len(warehouses) != 1:
        raise ValueError("本次请求必须来自同一仓库")
    # 只校验有关的在架库存；备货区不占用货架，也不参与出库。
    relevant = [r for r in inventory if r["base_quantity"] > 0 and not r.get("staging")] + orders
    required_skus = {(r["owner_code"], r["sku_id"]) for r in orders}
    for row in inventory:
        if row["base_quantity"] > 0 and not row.get("staging") and row.get("slot_id") not in layout.slots:
            raise ValueError(f"库存货位无法映射：{row.get('location_code', row.get('slot_id'))}")
    for row in relevant:
        key = (row["owner_code"], row["sku_id"])
        if operation == "picking_path" and key not in required_skus:
            continue
        matches = [s for s in specs if str(s.get("编码", "")).strip() == row["sku_id"] and
                   (str(s.get("货主编码", "")).strip() == row["owner_code"] if str(s.get("货主编码", "")).strip()
                    else _owner_name(s.get("所属客户", "")) == _owner_name(row.get("owner_name", "")))]
        values = set()
        for spec in matches:
            try:
                values.add(number(spec.get("托含量", ""), "托含量", positive=True))
            except ValueError:
                pass
        if len(values) != 1:
            if key in required_skus:
                missing[key] = [row["sku_id"], row.get("name", "")]
        else:
            capacities[key] = next(iter(values))
    if missing:
        prefix = "请先维护该sku主数据信息:" if operation == "slotting" else "无法计算出库容量，缺少有效SKU托含量:"
        raise ValueError(prefix + json.dumps(list(missing.values()), ensure_ascii=False))
    grouped = {}
    for row in inventory:
        if row.get("staging") or row["base_quantity"] == 0:
            continue
        row.update(layout.slots[row["slot_id"]])
        grouped.setdefault(row["slot_id"], []).append(row)
        available = row.get("available_base_quantity", "")
        if str(available).strip():
            row["available_base"] = number(available, "可用数量")
        elif str(row.get("available_quantity", "")).strip():
            # 原始箱数可能经舍入；全量可用时保留准确主单位数量。
            row["available_base"] = (row["base_quantity"] if number(row["available_quantity"], "可用量") == number(row["quantity"], "库存总量")
                                     else number(row["available_quantity"], "可用量") * row["conversion_ratio"])
        elif operation == "picking_path":
            raise ValueError(f"{row['sku_id']} 缺少可用库存数量")
        else:
            row["available_base"] = row["base_quantity"]
        if row["available_base"] > row["base_quantity"]:
            raise ValueError("可用数量超过在库数量")
    for slot, rows in grouped.items():
        if len({r["owner_code"] for r in rows}) != 1:
            raise ValueError(f"货位{slot}包含不同货主")
        limits = [capacities.get((r["owner_code"], r["sku_id"])) for r in rows]
        if all(limit is not None for limit in limits) and sum(r["boxes"] for r in rows) > min(limits):
            raise ValueError(f"货位{slot}库存超过单托容量")
    for row in orders:
        if not row.get("order_no"):
            raise ValueError("订单缺少关联单号")
    for key in capacities:
        rows = [r for r in relevant if (r["owner_code"], r["sku_id"]) == key]
        if len({(r["base_unit"], r["conversion_ratio"]) for r in rows}) != 1:
            raise ValueError(f"{key[1]}同货主SKU的主单位或转换比不一致")
    return inventory, orders, capacities


