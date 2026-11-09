"""联合优化出库托盘分组及托内路径。"""
from fractions import Fraction
from time import monotonic

from modules.picking_path.outbound_model import build_model
from solvers.copt_runtime import create_env, load_copt
from solvers.warehouse_outbound_heuristic import _route, solve as solve_outbound


def solve(inventory, orders, capacities, layout, flag, config):
    deadline = monotonic() + config["time_limit_s"]
    cp, copt = load_copt()
    stages = []

    def optimize(task, distance, baseline):
        picks = baseline["picks"]
        if len(picks) < 2:
            return [baseline]
        boxes = [Fraction(row["base_quantity"]) / Fraction(row["conversion_ratio"]) for row in picks]
        limits = [Fraction(capacities[row["owner_code"], row["sku_id"]]) for row in picks]
        stage = dict(task=task, owner_code=picks[0]["owner_code"], used_incumbent=False)
        stages.append(stage)
        if monotonic() >= deadline:
            stage.update(status="time_limit", used_baseline=True)
            return [baseline]
        model = env.createModel("warehouse_outbound")
        try:
            arcs = build_model(cp, copt, model, picks, boxes, limits, distance, deadline)
        except TimeoutError:
            stage.update(status="time_limit", used_baseline=True)
            return [baseline]
        remaining = deadline - monotonic()
        if remaining <= 0:
            stage.update(status="time_limit", used_baseline=True)
            return [baseline]
        model.setParam(copt.Param.Threads, config["threads"])
        model.setParam(copt.Param.RelGap, 0)
        model.setParam(copt.Param.TimeLimit, remaining)
        model.solve()
        stage["status"] = model.status
        if not model.hasmipsol:
            stage["used_baseline"] = True
            return [baseline]
        stage.update(objective=model.objval, best_bound=model.bestbnd, mip_gap=model.mipgap)
        chosen = [(i, j) for (i, j), arc in arcs.items() if arc.x > 0.5]
        successors = {i: j for i, j in chosen if i != -1}
        routes = []
        for i, start in chosen:
            if i != -1:
                continue
            indices = []
            while start != -1:
                indices.append(start)
                start = successors.pop(start)
            # 用精确数量复核，避免浮点求解容差造成超载。
            if sum(boxes[j] for j in indices) > min(limits[j] for j in indices):
                stage.update(used_baseline=True, reason="capacity_validation")
                return [baseline]
            routes.append(_route([picks[j] for j in indices], capacities, layout, task, distance, emit=True))
        if successors or sum(len(route["picks"]) for route in routes) != len(picks):
            stage.update(used_baseline=True, reason="route_validation")
            return [baseline]
        if sum(route["distance"] for route in routes) > baseline["distance"]:
            stage["used_baseline"] = True
            return [baseline]
        stage["used_incumbent"] = True
        return routes

    env = create_env(cp)
    try:
        result = solve_outbound(inventory, orders, capacities, layout, flag, deadline,
                                route_optimizer=optimize, route_strategy="COPT")
    except (ValueError, TimeoutError):
        raise
    except Exception as error:
        raise RuntimeError(f"出库COPT求解失败：{error}") from error
    finally:
        env.close()
    result["solver_stages"] = stages
    result["search"]["time_limit_reached"] = any(
        stage["status"] in ("time_limit", copt.TIMEOUT) for stage in stages
    )
    if any(stage.get("used_baseline") or stage["status"] != copt.OPTIMAL or stage.get("mip_gap", 0) > 1e-8
           for stage in stages):
        result["warnings"].append("部分出库任务未证明模型最优，已返回完整可行路径。")
    return result
