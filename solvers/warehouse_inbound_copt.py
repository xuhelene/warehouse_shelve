"""立体库 COPT 求解：上架量、距离、层位偏好依次优化。"""
from time import monotonic

from modules.slotting.inbound_model import build_model, build_result, preferred_levels, prepare_groups
from solvers.copt_runtime import load_copt, create_env


def solve(inventory, orders, capacities, layout, grades, config):
    started = monotonic()
    deadline = started + config["time_limit_s"]
    groups, occupied, empty, allow_mix = prepare_groups(inventory, orders, capacities, layout, grades, deadline)
    cp, COPT = load_copt()
    env = create_env(cp)
    model = None
    try:
        model = env.createModel("warehouse_inbound")
        model.setParam(COPT.Param.Threads, config["threads"])
        model.setParam(COPT.Param.RelGap, 0)
        x, distance = build_model(cp, COPT, model, groups, occupied, empty, allow_mix, capacities, layout, deadline)
        quantity = cp.quicksum(float(groups[i]["row"]["boxes"]) * v for (i, s), v in x.items())
        penalty = cp.quicksum(v for (i, s), v in x.items() if layout.slots[s]["level"] not in preferred_levels(groups[i]))
        selected, stages, warnings = [], [], []
        for name, objective, sense in (("quantity", quantity, COPT.MAXIMIZE), ("distance", distance, COPT.MINIMIZE), ("preference", penalty, COPT.MINIMIZE)):
            if not x:
                stages.append(dict(name=name, status=COPT.OPTIMAL, objective=0, best_bound=0, mip_gap=0, trivial=True))
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                warnings.append("达到时间上限，保留前一阶段可行方案；未完成所有目标优化")
                break
            model.setParam(COPT.Param.TimeLimit, remaining)
            # 数量及距离阶段必须证明最优，才能固定其值进入下一目标。
            model.setParam(COPT.Param.RelGap, config.get("mip_gap", 0.005) if name == "preference" else 0)
            model.setObjective(objective, sense)
            model.solve()
            if not model.hasmipsol:
                if stages:
                    warnings.append("后续阶段未找到可行解，保留前一阶段方案")
                    break
                raise RuntimeError(f"COPT未找到可行解，状态={model.status}")
            selected = [(i, s) for (i, s), v in x.items() if v.x > 0.5]
            stages.append(dict(name=name, status=model.status, objective=model.objval, best_bound=model.bestbnd, mip_gap=model.mipgap))
            if model.status != COPT.OPTIMAL or model.mipgap > 1e-8:
                warnings.append(f"{name}阶段未证明最优，返回当前可行方案")
                break
            model.addConstr(objective == model.objval)
        if not stages:
            raise TimeoutError("入库建模达到时间上限，未生成可行方案")
        return build_result(inventory, layout, groups, occupied, allow_mix, selected, stages, warnings, started, "COPT")
    except Exception as error:
        raise RuntimeError(f"入库COPT求解失败：{error}") from error
    finally:
        env.close()
