"""将每日期初库存、入库订单Excel按日期配对，不回写次日库存。"""
from datetime import date
from pathlib import Path
import re

from apps.warehouse_data import load_input, load_specs


def load_cases(directory, specs_path, start=None, end=None):
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    specs_path = Path(specs_path)
    specs = load_specs(specs_path.read_bytes(), specs_path.name)["rows"]
    def files(kind):
        folder = Path(directory) / kind
        if not folder.is_dir():
            raise ValueError(f"缺少数据目录：{folder}")
        mapping = {}
        for path in sorted(folder.iterdir()):
            if path.name.startswith("~$") or path.suffix.lower() not in {".xlsx", ".csv"}:
                continue
            match = re.search(r"\d{4}-\d{2}-\d{2}", path.name)
            if not match:
                continue
            day = date.fromisoformat(match.group())
            if (start and day < start) or (end and day > end):
                continue
            if day in mapping:
                raise ValueError(f"{kind}同一天存在多个文件：{day}")
            mapping[day] = path
        return mapping
    orders, inventories = files("入库订单"), files("库存快照")
    if not orders:
        raise ValueError("所选范围没有入库订单文件")
    cases, report = [], []
    for day, path in sorted(orders.items()):
        entry = dict(date=day.isoformat(), orders=str(path), inventory=str(inventories.get(day, "")))
        case = dict(id=day.isoformat(), specs=specs, grades=[])
        try:
            try:
                case["orders"] = load_input("orders", path.read_bytes(), path.name, operation="入库")["rows"]
            except ValueError as error:
                if str(error) == "文件中没有数据":
                    entry["status"] = "skipped_empty_orders"
                    report.append(entry)
                    continue
                raise
            if day not in inventories:
                raise ValueError("缺少同日期的期初库存")
            stock = inventories[day]
            case["inventory"] = load_input("inventory", stock.read_bytes(), stock.name)["rows"]
            entry["status"] = "loaded"
        except (ValueError, OSError) as error:
            case["_input_error"] = str(error)
            entry.update(status="input_failed", error=str(error))
        cases.append(case)
        report.append(entry)
    return cases, dict(requested_start=str(start) if start else None, requested_end=str(end) if end else None,
                       first_order_file=min(orders).isoformat(), last_order_file=max(orders).isoformat(),
                       grade_policy="all_low", independent_daily_snapshots=True, dates=report)
