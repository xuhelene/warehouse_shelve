"""同一输入比较 COPT、OR-Tools/SCIP、现有启发式；保存CSV及JSON报告。"""
import argparse
from copy import deepcopy
import csv
from datetime import date, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
from statistics import median
from time import monotonic, perf_counter

from apps.warehouse_data import prepare
from core.algorithm_settings import SolverSettings
from core.warehouse_layout import WarehouseLayout
from modules.slotting.inbound_model import preferred_levels
from tools.test_inbound_copt import objective_cases, validate
from solvers.warehouse_inbound_copt import TIMING_FIELDS, READ_TIMING_FIELDS, CoptEnvironmentSession
from modules.slotting.inbound_objectives import ExactDistanceCache, PREPROCESS_TIMING_FIELDS


BACKENDS = ("copt", "ortools", "heuristic")


def objective_metrics(strategy):
    if not strategy:
        return {}
    metrics = dict(objective_strategy=strategy)
    names = {"requested": "copt_objectives_requested", "actual": "copt_objectives_actual",
             "solve_calls": "copt_solve_calls", "fallback_reason": "objective_fallback_reason"}
    for key in ("requested", "actual", "solve_calls", "fallback_reason", "distance_unit", "preference_weight",
                "max_combined_coefficient", "combined_objective_upper_bound"):
        if key in strategy:
            metrics[names.get(key, key)] = strategy[key]
    cache_info = strategy.get("geometry_cache")
    if cache_info is not None:
        metrics.update(distance_cache_requested=cache_info["requested"], distance_cache_enabled=cache_info["enabled"],
                       distance_cache_changed=cache_info["changed"], distance_cache_hits=cache_info["slot_hits"],
                       distance_cache_misses=cache_info["slot_misses"])
    return metrics


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run(case, backend, config, folder, *, copt_session=None, copt_distance_cache=None):
    metrics = dict(backend=backend, passed=False)
    result = None
    try:
        if case.get("_input_error"):
            raise ValueError(case["_input_error"])
        # 导入不计入调用用时；许可和集群连接初始化计入。
        if backend == "copt":
            from solvers.warehouse_inbound_copt import solve
        elif backend == "ortools":
            from solvers.warehouse_inbound_ortools import solve
        else:
            from solvers.warehouse_inbound_heuristic import solve
        weight = float(case.get("layer_weight", 1))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("层间距离权重必须为非负有限值")
        layout = WarehouseLayout(weight)
        stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
        if "slots" in case:
            layout.slots = {s: layout.slots[s] for s in case["slots"]}
        if any(r["base_quantity"] and not r.get("staging") and r["slot_id"] not in layout.slots for r in stock):
            raise ValueError("受限布局缺少已有库存货位")
        original = deepcopy((stock, orders, caps))
        grades = {tuple(g[:3]): g[3] for g in case.get("grades", [])}
        started = perf_counter()
        try:
            if backend == "heuristic":
                result = solve(stock, orders, caps, layout, grades, monotonic() + config["time_limit_s"])
            elif backend == "ortools":
                result = solve(stock, orders, caps, layout, grades, config, use_matching=False)
            else:
                extra = dict(session=copt_session) if copt_session is not None else {}
                if copt_distance_cache is not None:
                    extra["distance_cache"] = copt_distance_cache
                result = solve(stock, orders, caps, layout, grades, config, **extra)
        finally:
            metrics["seconds"] = perf_counter() - started
        metrics.update(result.get("timings", {}))
        metrics.update(result.get("model_stats", {}))
        metrics.update(objective_metrics(result.get("objective_strategy")))
        if backend == "copt":
            metrics.update(environment_reused=result.get("environment_reused", False),
                           model_cleanup=result.get("model_cleanup"))
            metrics.update({("copt_read_mode" if k == "mode" else k): v for k, v in result.get("solution_read", {}).items()})
        if original != (stock, orders, caps):
            raise ValueError("求解修改了输入快照")
        validate(stock, orders, caps, layout, result)
        boxes = sum((r["boxes"] for r in result["assignments"]), Decimal(0))
        penalty = sum(r["level"] not in preferred_levels(r) for r in result["assignments"])
        stages = result.get("solver_stages", [])
        for stage in stages:
            if "solve_time_s" in stage:
                metrics[stage["name"] + "_solve_s"] = stage["solve_time_s"]
        metrics.update(passed=True, status=result["status"], assigned_boxes=str(boxes),
                       unassigned_boxes=str(sum((r["boxes"] for r in result["unassigned"]), Decimal(0))),
                       distance=result["total_distance"], preference_violations=penalty,
                       trips=len(result["legs"]), solver_method="SCIP" if backend == "ortools" else backend,
                       optimality=result.get("optimality", "not_proven"), stages=stages,
                       solve_time_s=result.get("solve_time_s"))
    except Exception as error:
        metrics["error"] = f"{type(error).__name__}: {error}"
        metrics.update(getattr(error, "timings", {}))
        metrics.update(objective_metrics(getattr(error, "objective_strategy", None)))
        metrics.update({("copt_read_mode" if k == "mode" else k): v for k, v in getattr(error, "solution_read", {}).items()})
    save(folder / "result.json", result if result is not None else dict(status="failed", message=metrics.get("error"),
             timings={k: metrics[k] for k in (*TIMING_FIELDS, *READ_TIMING_FIELDS, *PREPROCESS_TIMING_FIELDS, "other_s", "total_s") if k in metrics}))
    save(folder / "metrics.json", metrics)
    return metrics


def compare(a, b):
    """-1表示a更优；箱数精确比较，距离允许1e-7误差。"""
    for av, bv, tolerance in ((-Decimal(a["assigned_boxes"]), -Decimal(b["assigned_boxes"]), 0),
                              (a["distance"], b["distance"], 1e-7),
                              (a["preference_violations"], b["preference_violations"], 0)):
        if abs(av - bv) > tolerance:
            return -1 if av < bv else 1
    return 0


def summarize(rows):
    summary = {}
    backends = tuple(b for b in BACKENDS if any(r["backend"] == b for r in rows))
    for backend in backends:
        samples = [r for r in rows if r["backend"] == backend]
        passed = [r for r in samples if r["passed"]]
        times = sorted(r["seconds"] for r in passed)
        summary[backend] = dict(runs=len(samples), passed=len(passed), failed=len(samples) - len(passed),
                                optimal_runs=sum(r["optimality"] == "optimal" for r in passed),
                                not_proven_runs=sum(r["optimality"] != "optimal" for r in passed),
                                partial_runs=sum(r["status"] == "partial" for r in passed),
                                total_seconds=sum(times), mean_seconds=sum(times) / len(times) if times else None,
                                median_seconds=median(times) if times else None,
                                p95_seconds=times[math.ceil(.95 * len(times)) - 1] if times else None)
        summary[backend]["timings"] = {}
        summary[backend]["model_stats"] = {}
        for field in ("original_groups", "model_groups", "aggregated_classes", "assignment_variables",
                      "baseline_assignment_variables", "removed_assignment_variables"):
            values = [r[field] for r in passed if field in r]
            if values:
                summary[backend]["model_stats"][field] = dict(runs=len(values), total=sum(values), mean=sum(values) / len(values))
        reads = [r for r in samples if "variable_read_calls" in r]
        if reads:
            summary[backend]["solution_read"] = dict(modes=sorted({r["copt_read_mode"] for r in reads}),
                runs=len(reads), variable_read_calls=sum(r["variable_read_calls"] for r in reads),
                variable_values_read=sum(r["variable_values_read"] for r in reads))
        strategies = [r for r in passed if "copt_objectives_actual" in r]
        if strategies:
            summary[backend]["objective_strategies"] = dict(runs=len(strategies),
                actual_counts={mode: sum(r["copt_objectives_actual"] == mode for r in strategies)
                               for mode in sorted({r["copt_objectives_actual"] for r in strategies})},
                solve_calls=sum(r["copt_solve_calls"] for r in strategies),
                fallback_runs=sum("objective_fallback_reason" in r for r in strategies))
        cache_rows = [r for r in passed if "distance_cache_hits" in r]
        if cache_rows:
            summary[backend]["distance_cache"] = dict(runs=len(cache_rows),
                enabled_runs=sum(r["distance_cache_enabled"] for r in cache_rows),
                hits=sum(r["distance_cache_hits"] for r in cache_rows),
                misses=sum(r["distance_cache_misses"] for r in cache_rows))
        for field in (*TIMING_FIELDS, *READ_TIMING_FIELDS, *PREPROCESS_TIMING_FIELDS, "other_s", "total_s"):
            values = sorted(r[field] for r in samples if field in r)
            if values:
                summary[backend]["timings"][field] = dict(runs=len(values), total=sum(values), mean=sum(values) / len(values),
                                                         median=median(values), p95=values[math.ceil(.95 * len(values)) - 1])
    pairs = {}
    for index, a in enumerate(backends):
        for b in backends[index + 1:]:
            left = {(r["case_index"], r["repeat"]): r for r in rows if r["backend"] == a and r["passed"]}
            right = {(r["case_index"], r["repeat"]): r for r in rows if r["backend"] == b and r["passed"]}
            outcomes = [compare(left[k], right[k]) for k in left.keys() & right.keys()]
            pairs[f"{a}_vs_{b}"] = dict(comparable_runs=len(outcomes), left_better=outcomes.count(-1),
                                         equal=outcomes.count(0), right_better=outcomes.count(1))
    return dict(backends=summary, pairs=pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="单个场景或场景数组JSON；默认内置入库场景")
    source.add_argument("--data-dir", type=Path, help="含库存快照、入库订单子目录的客户每日数据目录；全部低等级")
    parser.add_argument("--specs", type=Path, help="SKU主数据CSV/XLSX，默认data/sku.xlsx")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--time-limit", type=float, default=60)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--mip-gap", type=float, default=0.005)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model-variant", choices=("baseline", "aggregate"), default="baseline",
                        help="精确模型：baseline原模型；aggregate只聚合完全等价整托，启发式不变")
    parser.add_argument("--copt-env", choices=("fresh", "reuse"), default="fresh",
                        help="COPT环境：fresh每次创建；reuse在本次脚本内串行复用环境，模型仍独立")
    parser.add_argument("--copt-read", choices=("scalar", "bulk"), default="scalar",
                        help="COPT变量解值：scalar逐变量读取；bulk每阶段一次批量读取")
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS),
                        help="本批次运行的算法；第三阶段可设为copt，默认仍运行全部三种")
    parser.add_argument("--copt-objectives", choices=("sequential", "combined"), default="sequential",
                        help="COPT目标：sequential原三阶段；combined数量独立，距离/偏好严格合并，不安全时回退")
    parser.add_argument("--copt-distance-cache", choices=("off", "batch"), default="off",
                        help="合并目标的精确距离缓存：off每次重建；batch本次进程内按完整布局校验复用")
    args = parser.parse_args(argv)
    backends = tuple(dict.fromkeys(args.backends))
    if args.repeats < 1:
        parser.error("repeats必须大于0")
    if args.copt_distance_cache == "batch" and (args.copt_objectives != "combined" or "copt" not in backends):
        parser.error("batch距离缓存仅用于combined目标且须运行copt")
    if not args.data_dir and any((args.specs, args.start_date, args.end_date)):
        parser.error("specs/start-date/end-date仅用于data-dir")
    dataset_report = None
    try:
        config = SolverSettings(time_limit_s=args.time_limit, threads=args.threads, mip_gap=args.mip_gap).model_dump()
        config["aggregate_full"] = args.model_variant == "aggregate"
        config["copt_read_mode"] = args.copt_read
        config["copt_objectives"] = args.copt_objectives
        config["copt_distance_cache"] = args.copt_distance_cache
        root = Path(__file__).resolve().parents[1]
        if args.data_dir:
            from tools.inbound_customer_cases import load_cases
            cases, dataset_report = load_cases(args.data_dir, args.specs or root / "data/sku.xlsx", args.start_date, args.end_date)
        else:
            cases = json.loads((args.input or root / "data/testing/inbound_requirement_cases.json").read_text(encoding="utf-8-sig"))
        if isinstance(cases, dict):
            cases = [cases]
        if not isinstance(cases, list) or not cases or any(not isinstance(c, dict) for c in cases):
            raise ValueError("输入必须为非空场景对象或对象数组")
        if args.input is None and args.data_dir is None:
            cases += objective_cases()
    except (ValueError, OSError) as error:
        parser.error(str(error))
    folder = args.output or root / "results/testing" / datetime.now().strftime("inbound_compare_%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True, exist_ok=False)
    if dataset_report is not None:
        save(folder / "dataset_report.json", dataset_report)
        print(f"每日独立快照，全部低周转等级；订单文件范围 {dataset_report['first_order_file']} ~ {dataset_report['last_order_file']}; "
              f"运行日期 {len(cases)}，空订单跳过 {sum(d['status'] == 'skipped_empty_orders' for d in dataset_report['dates'])}", flush=True)
    packages = {}
    for package in ("coptpy", "ortools", "numpy", "pandas", "pydantic"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    save(folder / "environment.json", dict(python=platform.python_version(), system=platform.platform(),
                                           machine=platform.machine(), processor=platform.processor(), packages=packages,
                                           config=config, repeats=args.repeats, copt_env=args.copt_env, backends=backends))
    print(f"精确模型版本: {args.model_variant}; COPT环境: {args.copt_env}; COPT解读取: {args.copt_read}; "
          f"COPT目标: {args.copt_objectives}; 距离缓存: {args.copt_distance_cache}; 启发式保持原算法", flush=True)
    rows = []
    session = CoptEnvironmentSession() if args.copt_env == "reuse" else None
    distance_cache = ExactDistanceCache() if args.copt_distance_cache == "batch" else None
    lifecycle = dict(mode=args.copt_env, cleanup_errors=[])
    try:
        for index, case in enumerate(cases):
            target = folder / f"{index:03d}"
            target.mkdir()
            save(target / "input.json", case)
            for repeat in range(args.repeats):
                # 轮换顺序，减少固定先后顺序的影响；各次调用使用新布局及输入。
                offset = (index + repeat) % len(backends)
                for backend in backends[offset:] + backends[:offset]:
                    output = target / f"{repeat + 1:02d}_{backend}"
                    output.mkdir()
                    values = run(case, backend, {**config, "backend": backend}, output,
                                 copt_session=session, copt_distance_cache=distance_cache)
                    values.update(case_index=index, id=case.get("id", str(index)), repeat=repeat + 1)
                    rows.append(values)
                    print(f"{'PASS' if values['passed'] else 'FAIL'} {values['id']} #{repeat + 1} {backend}: "
                          f"{values.get('seconds', 0):.4f}s boxes={values.get('assigned_boxes', '-')} "
                          f"distance={values.get('distance', '-')} optimal={values.get('optimality', '-')} "
                          f"{values.get('error', '')}", flush=True)
                    save(folder / "runs.json", rows)
    finally:
        tick = perf_counter()
        try:
            if session is not None:
                session.close()
        except Exception as error:
            lifecycle["cleanup_errors"].append(str(error))
        finally:
            final_close_seconds = perf_counter() - tick if session is not None else 0.0
            if session is not None:
                prior_errors = lifecycle["cleanup_errors"]
                lifecycle.update(session.stats)
                lifecycle["cleanup_errors"] = list(dict.fromkeys(prior_errors + session.stats["cleanup_errors"]))
            calls = [r["seconds"] for r in rows if r["backend"] == "copt" and "seconds" in r]
            accounted = sum(calls) + final_close_seconds
            lifecycle.update(timed_calls=len(calls), sum_call_seconds=sum(calls),
                             final_close_seconds=final_close_seconds, accounted_seconds=accounted,
                             amortized_seconds=accounted / len(calls) if calls else None)
            save(folder / "copt_environment.json", lifecycle)
            cache_report = dict(mode=args.copt_distance_cache, **(distance_cache.report() if distance_cache is not None else {}))
            save(folder / "distance_cache.json", cache_report)
    summary = summarize(rows)
    summary["copt_environment"] = lifecycle
    summary["distance_cache"] = cache_report
    save(folder / "summary.json", summary)
    fields = ["case_index", "id", "repeat", "backend", "passed", "status", "seconds", "solve_time_s",
              "assigned_boxes", "unassigned_boxes", "distance", "preference_violations", "trips", "optimality", "solver_method", "error"]
    fields += [*TIMING_FIELDS, *READ_TIMING_FIELDS, *PREPROCESS_TIMING_FIELDS, "other_s", "total_s"]
    fields += ["aggregate_full", "original_groups", "model_groups", "aggregated_classes",
               "assignment_variables", "baseline_assignment_variables", "removed_assignment_variables"]
    fields += ["environment_reused", "model_cleanup"]
    fields += ["copt_read_mode", "variable_read_calls", "variable_values_read"]
    fields += ["copt_objectives_requested", "copt_objectives_actual", "copt_solve_calls", "objective_fallback_reason",
               "distance_unit", "preference_weight", "max_combined_coefficient", "combined_objective_upper_bound"]
    fields += ["distance_cache_requested", "distance_cache_enabled", "distance_cache_changed", "distance_cache_hits", "distance_cache_misses"]
    with (folder / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"Results: {folder.resolve()}")
    return int(any(not r["passed"] for r in rows) or bool(lifecycle["cleanup_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
