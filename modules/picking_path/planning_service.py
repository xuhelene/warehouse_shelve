"""立体库出库任务：模式校验及FIFO/容量路径求解调度。"""
from time import monotonic

from solvers.warehouse_outbound_copt import solve as solve_copt
from solvers.warehouse_outbound_heuristic import solve
from solvers.warehouse_outbound_ortools import solve as solve_ortools


def plan(inventory, orders, capacities, layout, payload, config):
    flag = payload.get("storeBatchFlag", 0)
    if type(flag) is not int or flag not in (0, 1):
        raise ValueError("storeBatchFlag仅支持0按单、1按波次")
    if config["backend"] == "copt":
        return solve_copt(inventory, orders, capacities, layout, flag, config)
    if config["backend"] == "ortools":
        return solve_ortools(inventory, orders, capacities, layout, flag, config)
    return solve(inventory, orders, capacities, layout, flag, monotonic() + config["time_limit_s"])
