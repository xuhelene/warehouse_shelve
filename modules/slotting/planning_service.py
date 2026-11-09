"""立体库入库任务：周转等级准备及入库求解调度。"""
from datetime import datetime
from time import monotonic

from core.paths import default_input_path, runtime_path
from solvers.warehouse_inbound_heuristic import solve


def plan(inventory, orders, capacities, layout, payload, config):
    from core.sku_turnover import calculate, read_latest
    path = default_input_path("data/slotting/orders.csv")
    latest = read_latest()
    end_date = None
    if latest:
        source_dir = runtime_path("results/sku_turnover") / latest["run_id"]
        path = source_dir / ("orders.xlsx" if latest["filename"].lower().endswith(".xlsx") else "orders.csv")
        end_date = datetime.fromisoformat(latest["end_date"]).date()
    # 每次从完整历史重算已确认的入库+出库累计贡献等级。
    grades = calculate(path.read_bytes(), path.name, end_date)["rows"]
    grade_map = {(r["warehouse_code"], r["owner_code"], r["sku_id"]): r["grade"] for r in grades}
    if config["backend"] == "copt":
        from solvers.warehouse_inbound_copt import solve as solve_copt
        result = solve_copt(inventory, orders, capacities, layout, grade_map, config)
    elif config["backend"] == "ortools":
        from solvers.warehouse_inbound_ortools import solve as solve_ortools
        result = solve_ortools(inventory, orders, capacities, layout, grade_map, config)
    elif config["backend"] == "heuristic":
        result = solve(inventory, orders, capacities, layout, grade_map, monotonic() + config["time_limit_s"])
    else:
        raise ValueError("立体库入库支持启发式、COPT和OR-Tools")
    result["turnover"] = grades
    return result
