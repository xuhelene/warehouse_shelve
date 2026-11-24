"""立体库 COPT 求解：上架量、距离、层位偏好依次优化。"""
from time import monotonic, perf_counter
from threading import get_ident
from math import isfinite

from modules.slotting.inbound_model import (
    aggregate_groups, build_model, build_result, expand_selection, model_statistics,
    preferred_levels, prepare_groups,
)
from solvers.copt_runtime import load_copt, create_env
from modules.slotting.inbound_objectives import integer_distance_costs, PREPROCESS_TIMING_FIELDS


TIMING_FIELDS = ("import_s", "environment_s", "modeling_s", "quantity_solve_s", "distance_solve_s",
                 "preference_solve_s", "combined_solve_s", "result_read_s", "model_release_s", "environment_close_s")
# result_read_s 的子项，不参与 total_s 的分项加总。
READ_TIMING_FIELDS = ("variable_read_s",)


def read_selection(model, variables, copt, mode):
    if mode == "bulk":
        values = list(model.getInfo(copt.Info.Value, list(variables.values())))
    elif mode == "scalar":
        values = [variable.x for variable in variables.values()]
    else:
        raise ValueError("COPT解读取方式必须为scalar或bulk")
    if len(values) != len(variables):
        raise RuntimeError(f"COPT解向量长度不一致：期望{len(variables)}，实际{len(values)}")
    if any(not isfinite(value) for value in values):
        raise RuntimeError("COPT解向量包含非有限值")
    return [key for key, value in zip(variables, values) if value > 0.5]


def close_environment(env):
    try:
        env.close()
    finally:
        dispose = getattr(type(env), "dispose", None)
        if dispose is not None:
            dispose(env)


class CoptEnvironmentSession:
    """仅供同线程串行批次复用；失败后关闭环境，下次调用重新创建。"""
    def __init__(self):
        self.env = None
        self.thread = get_ident()
        self.closed = False
        self.stats = dict(mode="reuse", create_attempts=0, created=0, reused_calls=0,
                          close_attempts=0, create_seconds=0.0, close_seconds=0.0, cleanup_errors=[])

    def _check_thread(self):
        if get_ident() != self.thread:
            raise RuntimeError("COPT批次环境仅允许创建它的线程串行使用")

    def acquire(self, cp):
        self._check_thread()
        if self.closed:
            raise RuntimeError("COPT批次环境已关闭")
        reused = self.env is not None
        if reused:
            self.stats["reused_calls"] += 1
        else:
            self.stats["create_attempts"] += 1
            tick = perf_counter()
            try:
                self.env = create_env(cp)
                self.stats["created"] += 1
            finally:
                self.stats["create_seconds"] += perf_counter() - tick
        return self.env, reused

    def invalidate(self):
        self._check_thread()
        if self.env is None:
            return
        env, self.env = self.env, None
        self.stats["close_attempts"] += 1
        tick = perf_counter()
        try:
            close_environment(env)
        except Exception as error:
            self.stats["cleanup_errors"].append(str(error))
            raise
        finally:
            self.stats["close_seconds"] += perf_counter() - tick

    def close(self):
        self._check_thread()
        try:
            self.invalidate()
        finally:
            self.closed = True


def solve(inventory, orders, capacities, layout, grades, config, *, session=None, distance_cache=None):
    timing_started = perf_counter()
    started = monotonic()
    deadline = started + config["time_limit_s"]
    timings = dict.fromkeys(TIMING_FIELDS, 0.0)
    timings.update(dict.fromkeys(READ_TIMING_FIELDS, 0.0))
    timings.update(dict.fromkeys(PREPROCESS_TIMING_FIELDS, 0.0))
    read_mode = config.get("copt_read_mode", "scalar")
    solution_read = dict(mode=read_mode, variable_read_calls=0, variable_values_read=0)
    strategy = dict(requested=config.get("copt_objectives", "sequential"), actual="sequential", solve_calls=0)
    env = None
    model = None
    result = None
    failure = None
    environment_reused = False
    cleanup_method = None
    try:
        if read_mode not in {"scalar", "bulk"}:
            raise ValueError("COPT解读取方式必须为scalar或bulk")
        if strategy["requested"] not in {"sequential", "combined"}:
            raise ValueError("COPT目标策略必须为sequential或combined")
        cache_mode = config.get("copt_distance_cache", "off")
        if cache_mode not in {"off", "batch"} or (cache_mode == "batch" and distance_cache is None):
            raise ValueError("距离缓存须为off或batch；batch模式须传入批次缓存")
        tick = perf_counter()
        cp, COPT = load_copt()
        timings["import_s"] += perf_counter() - tick
        tick = perf_counter()
        try:
            if session is None:
                env = create_env(cp)
            else:
                env, environment_reused = session.acquire(cp)
        finally:
            timings["environment_s"] += perf_counter() - tick
        tick = perf_counter()
        groups, occupied, empty, allow_mix = prepare_groups(inventory, orders, capacities, layout, grades, deadline)
        original_groups = groups
        groups = aggregate_groups(groups, empty, layout, config.get("aggregate_full", False), deadline)
        model = env.createModel("warehouse_inbound")
        model.setParam(COPT.Param.Threads, config["threads"])
        model.setParam(COPT.Param.RelGap, 0)
        slot_usage = {} if strategy["requested"] == "combined" else None
        x, distance = build_model(cp, COPT, model, groups, occupied, empty, allow_mix, capacities, layout, deadline,
                                  slot_usage=slot_usage)
        quantity = cp.quicksum(float(groups[i]["row"]["boxes"]) * v for (i, s), v in x.items())
        penalty = cp.quicksum(v for (i, s), v in x.items() if layout.slots[s]["level"] not in preferred_levels(groups[i]))
        objectives = [("quantity", quantity, COPT.MAXIMIZE), ("distance", distance, COPT.MINIMIZE), ("preference", penalty, COPT.MINIMIZE)]
        costs = None
        if strategy["requested"] == "combined":
            costs, metadata = integer_distance_costs(layout, slot_usage, len(original_groups), deadline,
                cache=distance_cache if cache_mode == "batch" else None)
            strategy.update(metadata)
            for field in PREPROCESS_TIMING_FIELDS:
                timings[field] += metadata["preprocess_timings"][field]
            if costs is not None:
                expression_started = perf_counter()
                try:
                    combined = cp.quicksum(costs[s] * used for s, used in slot_usage.items()) * strategy["preference_weight"] + penalty
                finally:
                    expression_seconds = perf_counter() - expression_started
                    timings["combined_expression_s"] += expression_seconds
                    strategy["preprocess_timings"]["combined_expression_s"] += expression_seconds
                strategy["actual"] = "combined"
                objectives = [("quantity", quantity, COPT.MAXIMIZE), ("combined", combined, COPT.MINIMIZE)]
        timings["modeling_s"] += perf_counter() - tick
        selected, stages, warnings = [], [], []
        if strategy.get("fallback_reason") and x:
            warnings.append(f"合并目标未启用，保留原三阶段：{strategy['fallback_reason']}")
        for name, objective, sense in objectives:
            if not x:
                stages.append(dict(name=name, status=COPT.OPTIMAL, objective=0, best_bound=0, mip_gap=0, trivial=True, proven_optimal=True, solve_time_s=0))
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                warnings.append("达到时间上限，保留前一阶段可行方案；未完成所有目标优化")
                break
            tick = perf_counter()
            model.setParam(COPT.Param.TimeLimit, remaining)
            # 数量及距离阶段必须证明最优，才能固定其值进入下一目标。
            model.setParam(COPT.Param.RelGap, config.get("mip_gap", 0.005) if name == "preference" else 0)
            model.setObjective(objective, sense)
            timings["modeling_s"] += perf_counter() - tick
            tick = perf_counter()
            try:
                strategy["solve_calls"] += 1
                model.solve()
            finally:
                stage_seconds = perf_counter() - tick
                timings[name + "_solve_s"] += stage_seconds
            tick = perf_counter()
            try:
                has_solution = model.hasmipsol
                if name == "combined" and not has_solution:
                    strategy["combined_stage"] = dict(status=model.status, has_solution=False, solve_time_s=stage_seconds)
                if has_solution:
                    read_started = perf_counter()
                    solution_read["variable_read_calls"] += 1 if read_mode == "bulk" else len(x)
                    try:
                        selected = read_selection(model, x, COPT, read_mode)
                        solution_read["variable_values_read"] += len(x)
                    finally:
                        variable_seconds = perf_counter() - read_started
                        timings["variable_read_s"] += variable_seconds
                    status, value, bound, gap = model.status, model.objval, model.bestbnd, model.bestgap
                    proven = status == COPT.OPTIMAL and gap <= 1e-8
                    if name == "combined":
                        slots = {s for i, s in selected}
                        preference = sum(layout.slots[s]["level"] not in preferred_levels(groups[i]) for i, s in selected)
                        expected = sum(costs[s] for s in slots) * strategy["preference_weight"] + preference
                        if abs(expected - value) > 1e-4:
                            raise RuntimeError("合并目标值与整数方案校验不一致")
                        proven = proven and abs(value - bound) < 1e-4
                        strategy["combined_stage"] = dict(status=status, has_solution=True, objective=value, best_bound=bound, mip_gap=gap,
                            proven_optimal=proven, solve_time_s=stage_seconds, variable_read_s=variable_seconds)
                        # 保留三个业务目标字段；组合目标界不能拆成两个独立目标界。
                        distance_value = sum(layout.leg("STAGING", s)["distance"] for s in slots)
                        for logical_name, logical_value in (("distance", distance_value), ("preference", preference)):
                            stages.append(dict(name=logical_name, status=status, objective=logical_value,
                                best_bound=logical_value if proven else None, mip_gap=0 if proven else None,
                                proven_optimal=proven, derived_from="combined"))
                    else:
                        stages.append(dict(name=name, status=status, objective=value, best_bound=bound, mip_gap=gap,
                                           proven_optimal=proven, solve_time_s=stage_seconds, variable_read_s=variable_seconds))
            finally:
                timings["result_read_s"] += perf_counter() - tick
            if not has_solution:
                if stages:
                    warnings.append("后续阶段未找到可行解，保留前一阶段方案")
                    break
                raise RuntimeError(f"COPT未找到可行解，状态={model.status}")
            if not proven:
                warnings.append(f"{name}阶段未证明最优，返回当前可行方案")
                break
            if name not in {"preference", "combined"}:
                tick = perf_counter()
                model.addConstr(objective == value)
                timings["modeling_s"] += perf_counter() - tick
        if not stages:
            raise TimeoutError("入库建模达到时间上限，未生成可行方案")
        tick = perf_counter()
        result = build_result(inventory, layout, original_groups, occupied, allow_mix,
                              expand_selection(selected, groups), stages, warnings, started, "COPT")
        result["model_stats"] = model_statistics(original_groups, groups, x, config.get("aggregate_full", False))
        timings["result_read_s"] += perf_counter() - tick
        result["timings"] = timings
        result["environment_reused"] = environment_reused
        result["solution_read"] = solution_read
        result["objective_strategy"] = strategy
        return result
    except Exception as error:
        failure = RuntimeError(f"入库COPT求解失败：{error}")
        failure.timings = timings
        failure.solution_read = solution_read
        failure.objective_strategy = strategy
        raise failure from error
    finally:
        cleanup_errors = []
        tick = perf_counter()
        try:
            if model is not None:
                dispose = getattr(type(model), "dispose", None)
                if dispose is not None:
                    dispose(model)
                    cleanup_method = "dispose"
                else:
                    model.clear()
                    cleanup_method = "clear"
        except Exception as error:
            cleanup_errors.append(f"模型清理失败：{error}")
        finally:
            timings["model_release_s"] += perf_counter() - tick
        tick = perf_counter()
        try:
            # 外部批次持有环境；仅失败时废弃，避免污染后续任务。
            if session is not None and (failure is not None or cleanup_errors):
                session.invalidate()
            elif session is None and env is not None:
                close_environment(env)
        except Exception as error:
            cleanup_errors.append(f"环境清理失败：{error}")
        finally:
            timings["environment_close_s"] += perf_counter() - tick
            timings["total_s"] = perf_counter() - timing_started
            timings["other_s"] = max(0, timings["total_s"] - sum(timings[key] for key in TIMING_FIELDS))
        if result is not None:
            result["model_cleanup"] = cleanup_method
        if cleanup_errors:
            if failure is not None:
                failure.args = (f"{failure}；{'；'.join(cleanup_errors)}",)
            else:
                error = RuntimeError("；".join(cleanup_errors))
                error.timings = timings
                error.solution_read = solution_read
                error.objective_strategy = strategy
                raise error
