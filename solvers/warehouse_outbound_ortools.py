"""使用 OR-Tools CP-SAT 联合优化出库分托和托内路径。"""
from fractions import Fraction
from math import lcm
from time import monotonic

from modules.picking_path.outbound_model import build_ortools_model
from solvers.warehouse_outbound_heuristic import _route, solve as solve_outbound


def solve(inventory, orders, capacities, layout, flag, config, *, prune=True, hint=True):
    try:
        from ortools.sat.python import cp_model
    except (ImportError, OSError) as error:
        raise RuntimeError(f"无法加载 OR-Tools CP-SAT：{error}") from error

    deadline = monotonic() + config["time_limit_s"]
    stages = []

    class Progress(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__()
            self.improvements = []

        def on_solution_callback(self):
            if not self.improvements or self.objective_value < self.improvements[-1]["distance"]:
                self.improvements.append(dict(seconds=self.wall_time, distance=self.objective_value,
                                              best_bound=self.best_objective_bound))

    def optimize(task, distance, baseline):
        picks = baseline["picks"]
        if len(picks) < 2:
            return [baseline]
        stage = dict(task=task, owner_code=picks[0]["owner_code"], used_baseline=True)
        stages.append(stage)
        started = monotonic()
        boxes = [Fraction(row["base_quantity"]) / Fraction(row["conversion_ratio"]) for row in picks]
        limits = [Fraction(capacities[row["owner_code"], row["sku_id"]]) for row in picks]
        max_limit = max(limits)
        scale = 1
        for value in boxes + limits:
            scale = lcm(scale, value.denominator)
            if scale * max_limit * len(picks) >= 2**60:
                stage["status"] = "capacity_precision"
                return [baseline]
        model = cp_model.CpModel()
        try:
            arcs = build_ortools_model(model, [row["slot_id"] for row in picks],
                                       [int(q * scale) for q in boxes], [int(cap * scale) for cap in limits],
                                       distance, deadline, prune=prune)
        except TimeoutError:
            stage["status"] = "time_limit"
            return [baseline]
        if hint:
            # 初始方案的明细顺序和趟次与模型节点一一对应。
            chosen = set()
            previous = 0
            for i, row in enumerate(picks, 1):
                if previous and row["trip"] != picks[previous - 1]["trip"]:
                    chosen.add((previous, 0))
                    previous = 0
                chosen.add((previous, i))
                previous = i
            chosen.add((previous, 0))
            for edge, arc in arcs.items():
                model.add_hint(arc, int(edge in chosen))
        stage.update(nodes=len(picks), arcs=len(arcs), build_seconds=monotonic() - started)
        remaining = deadline - monotonic()
        if remaining <= 0:
            stage["status"] = "time_limit"
            return [baseline]
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = config["threads"]
        progress = Progress()
        status = solver.solve(model, progress)
        stage.update(solve_seconds=solver.wall_time, improvements=progress.improvements)
        stage["status"] = solver.status_name(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return [baseline]
        chosen = [(i, j) for (i, j), arc in arcs.items() if solver.boolean_value(arc)]
        successors = {i: j for i, j in chosen if i}
        routes = []
        for i, j in chosen:
            if i:
                continue
            rows = []
            while j:
                rows.append(picks[j - 1])
                j = successors[j]
            routes.append(_route(rows, capacities, layout, task, distance, emit=True))
        stage.update(objective=solver.objective_value, best_bound=solver.best_objective_bound)
        if sum(route["distance"] for route in routes) > baseline["distance"]:
            return [baseline]
        stage["used_baseline"] = False
        return routes

    result = solve_outbound(inventory, orders, capacities, layout, flag, deadline,
                            route_optimizer=optimize, route_strategy="OR-Tools")
    result["solver_stages"] = stages
    result["search"]["time_limit_reached"] = any(
        stage["status"] in ("time_limit", "UNKNOWN", "FEASIBLE") for stage in stages
    )
    if any(stage["used_baseline"] or stage["status"] != "OPTIMAL" for stage in stages):
        result["warnings"].append("部分出库任务未证明模型最优，已返回完整可行路径。")
    return result
