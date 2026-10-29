"""立体库出库任务：模式校验及FIFO/容量路径求解调度。"""
from time import monotonic

from solvers.warehouse_outbound_heuristic import solve


def plan(inventory, orders, capacities, layout, payload, config):
    flag = payload.get("storeBatchFlag", 0)
    if type(flag) is not int or flag not in (0, 1):
        raise ValueError("storeBatchFlag仅支持0按单、1按波次")
    return solve(inventory, orders, capacities, layout, flag, monotonic() + config["time_limit_s"])
