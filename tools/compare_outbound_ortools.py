"""对比 VNS 与 OR-Tools 的出库业务正确性、路线质量和求解时延。"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter

import ortools
from ortools.sat.python import cp_model  # 在计时前加载原生库

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from modules.picking_path.planning_service import plan
from tests.test_outbound_requirements import validate_result
from tools.benchmark_outbound_routes import SAMPLES, evaluate


def run(case, config, *, planner=plan):
    layout = WarehouseLayout(case.get("layer_weight", 1))
    data = prepare(case["inventory"], case["orders"], case["specs"], layout, "picking_path")
    original = deepcopy(data)
    started = perf_counter()
    result = planner(*data, layout, case, config)
    elapsed = perf_counter() - started
    assert original == data, "求解修改了输入"
    validate_result(case, result, *data, layout,
                    check_route_expectations=config["backend"] == "heuristic")
    optimum = case.get("optimal_distance")
    if optimum is not None:
        assert result["total_distance"] >= optimum - 1e-7
        if config["backend"] == "ortools":
            assert math.isclose(result["total_distance"], optimum, abs_tol=1e-7)
    return dict(seconds=elapsed, result=result)


def summarize(rows):
    report = {}
    for suite in dict.fromkeys(row["suite"] for row in rows):
        cases = [r for r in rows if r["suite"] == suite]
        passed = [r for r in cases if "error" not in r]
        deltas = [r["ortools"]["distance"] - r["heuristic"]["distance"] for r in passed]
        summary = dict(cases=len(cases), passed=len(passed),
                       shorter=sum(d < -1e-7 for d in deltas),
                       equal=sum(abs(d) <= 1e-7 for d in deltas),
                       longer=sum(d > 1e-7 for d in deltas))
        for backend in ("heuristic", "ortools"):
            samples = [r[backend] for r in passed]
            times = sorted(r["seconds"] for r in samples)
            summary[backend] = dict(distance=sum(r["distance"] for r in samples),
                                    trips=sum(r["trips"] for r in samples),
                                    median_ms=1000 * median(times) if times else None,
                                    p95_ms=1000 * times[math.ceil(.95 * len(times)) - 1] if times else None,
                                    timeout_cases=sum(r["timeout"] for r in samples),
                                    fallback_cases=sum(r["fallback"] for r in samples))
        report[suite] = summary
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--time-limit", type=float, default=10)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    if args.repeats < 1 or args.threads < 1 or not math.isfinite(args.time_limit) or args.time_limit <= 0:
        parser.error("重复次数、线程数和时限必须为正数")
    root = Path(__file__).resolve().parents[1]
    cases = []
    for name in ("requirement", "distribution"):
        cases.extend(json.loads((root / f"data/testing/outbound_{name}_cases.json").read_text(encoding="utf-8")))
    for index, slots in enumerate(SAMPLES, 1):
        quality = evaluate(slots)
        cases.append(dict(id=f"exact_route_{index:02}", suite="quality",
                          **{key: quality[key] for key in
                             ("inventory", "orders", "specs", "storeBatchFlag", "optimal_distance")}))
    folder = root / "results/testing" / datetime.now().strftime("outbound_ortools_%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True)
    rows = []
    for index, case in enumerate(cases, 1):
        row = dict(id=case["id"], suite=case.get("suite", "business"))
        samples = {"heuristic": [], "ortools": []}
        try:
            for repeat in range(args.repeats):
                backends = tuple(samples)
                for backend in backends if (index + repeat) % 2 else reversed(backends):
                    samples[backend].append(run(case, dict(backend=backend, time_limit_s=args.time_limit,
                                                           threads=args.threads)))
            for backend, runs in samples.items():
                ranked = sorted(runs, key=lambda r: r["seconds"])
                result = ranked[len(ranked) // 2]["result"]
                row[backend] = dict(seconds=median(r["seconds"] for r in runs),
                                    distance=result["total_distance"], trips=len(result["trips"]),
                                    timeout=any(r["result"]["search"]["time_limit_reached"] for r in runs),
                                    fallback=any(s["used_baseline"] for r in runs
                                                 for s in r["result"].get("solver_stages", [])),
                                    stage_statuses=dict(Counter(s["status"] for r in runs
                                                                for s in r["result"].get("solver_stages", []))))
        except Exception as error:
            row["error"] = f"{type(error).__name__}: {error}"
            print(f"FAIL {case['id']}: {row['error']}", flush=True)
        (folder / f"{case['id']}.json").write_text(
            json.dumps(dict(input=case, runs=samples), ensure_ascii=False, default=str), encoding="utf-8")
        rows.append(row)
        if index % 25 == 0:
            print(f"{index}/{len(cases)}", flush=True)
    report = dict(config=vars(args), python=platform.python_version(), ortools=ortools.__version__,
                  cpu=os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()), logical_cpus=os.cpu_count(),
                  summary=summarize(rows), cases=rows)
    path = folder / "summary.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(path)
    return int(any("error" in row for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
