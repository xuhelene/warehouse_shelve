"""使用 OR-Tools 的 SCIP 后端逐阶段求解立体库入库模型。"""
from time import monotonic

from modules.slotting.inbound_model import (
    build_assignment_model, build_result, preferred_levels, prepare_groups,
)
from solvers.warehouse_inbound_matching import match


def solve(inventory, orders, capacities, layout, grades, config):
    try:
        from ortools.linear_solver import linear_solver_pb2, pywraplp
    except (ImportError, OSError) as error:
        if getattr(error, "winerror", None) == 127:
            raise RuntimeError(
                "OR-Tools 的 Windows DLL 加载失败（WinError 127）。"
                "请用仅安装本项目 requirements.txt 依赖的独立 Python 环境重启服务。"
            ) from error
        raise RuntimeError(f"无法加载 OR-Tools 线性求解器：{error}") from error

    started = monotonic()
    deadline = started + config["time_limit_s"]
    groups, occupied, empty, allow_mix = prepare_groups(
        inventory, orders, capacities, layout, grades, deadline,
    )
    if groups and not allow_mix:
        matched = match(groups, empty, layout, deadline)
        if matched is not None:
            selected, stages = matched
            warnings = ["匹配求解超过时间上限，已返回最优方案"] if monotonic() >= deadline else []
            result = build_result(inventory, layout, groups, occupied, allow_mix, selected, stages, warnings, started, "OR-Tools")
            result["solver_method"] = "min_cost_flow"
            return result
    model = pywraplp.Solver.CreateSolver("SCIP")
    threads = config["threads"]
    if model is None:
        raise RuntimeError("OR-Tools SCIP 初始化或线程设置失败")
    x, distance = build_assignment_model(
        model.BoolVar, model.Add, model.Sum, groups, occupied, empty,
        allow_mix, capacities, layout, deadline,
    )
    quantity = model.Sum(float(groups[i]["row"]["boxes"]) * v for (i, s), v in x.items())
    penalty = model.Sum(v for (i, s), v in x.items()
                        if layout.slots[s]["level"] not in preferred_levels(groups[i]))
    selected, stages, warnings = [], [], []
    params = pywraplp.MPSolverParameters()
    for name, objective in (("quantity", quantity), ("distance", distance), ("preference", penalty)):
        if not x:
            stages.append(dict(name=name, status=model.OPTIMAL, objective=0, best_bound=0, mip_gap=0, trivial=True))
            continue
        if name == "quantity":
            model.Maximize(objective)
        else:
            model.Minimize(objective)
        stage_model = model
        stage_variables = x
        if threads > 1:
            # SCIP 并行求解后复用原实例，下一阶段可能在预求解时中断。
            proto = linear_solver_pb2.MPModelProto()
            model.ExportModelToProto(proto)
            stage_model = pywraplp.Solver.CreateSolver("SCIP")
            if (stage_model is None or stage_model.LoadModelFromProto(proto)
                    or not stage_model.SetNumThreads(threads)):
                if not stages:
                    raise RuntimeError("OR-Tools SCIP 初始化或线程设置失败")
                warnings.append("后续阶段初始化失败，保留前一阶段方案")
                break
            variables = stage_model.variables()
            stage_variables = {key: variables[var.index()] for key, var in x.items()}
        if stages:
            chosen = set(selected)
            stage_model.SetHint(list(stage_variables.values()), [int(key in chosen) for key in stage_variables])
        remaining = deadline - monotonic()
        if remaining <= 0:
            warnings.append("达到时间上限，保留前一阶段可行方案")
            break
        stage_model.SetTimeLimit(max(1, int(remaining * 1000)))
        params.SetDoubleParam(params.RELATIVE_MIP_GAP, config.get("mip_gap", 0.005) if name == "preference" else 0)
        status = stage_model.Solve(params)
        if status not in (model.OPTIMAL, model.FEASIBLE):
            if stages:
                warnings.append("后续阶段未找到可行解，保留前一阶段方案")
                break
            raise RuntimeError(f"OR-Tools未找到可行解，状态={status}")
        selected = [key for key, var in stage_variables.items() if var.solution_value() > 0.5]
        value = stage_model.Objective().Value()
        bound = stage_model.Objective().BestBound()
        if threads > 1:
            del stage_variables, stage_model
        gap = abs(value - bound) / max(abs(value), 1e-10)
        stages.append(dict(name=name, status=status, objective=value, best_bound=bound, mip_gap=gap))
        if status != model.OPTIMAL or gap > 1e-8:
            warnings.append(f"{name}阶段未证明最优，返回当前可行方案")
            break
        if name != "preference":
            model.Add(objective == value)
    if not stages:
        raise TimeoutError("入库建模达到时间上限，未生成可行方案")
    result = build_result(inventory, layout, groups, occupied, allow_mix, selected, stages, warnings, started, "OR-Tools")
    result["solver_method"] = "scip"
    return result
