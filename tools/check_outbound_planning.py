"""从命令行测试立体库拣货路径推荐，默认使用 COPT。"""
import argparse
from datetime import datetime
from fractions import Fraction
import json
from math import isclose
from pathlib import Path

from apps.warehouse_service import plan
from core.algorithm_settings import SolverSettings


SMOKE_CASES = ("vertical_same_column", "multi_trip_0", "wave_owner_unload", "partial_available")


def check_result(case, result):
    if result["status"] not in {"feasible", "partial"}:
        raise ValueError(result["message"])
    legs = result["legs"]
    trips = result["trips"]
    if legs != [leg for trip in trips for leg in trip["legs"]]:
        raise ValueError("路线与趟次明细不一致")
    if not isclose(result["total_distance"], sum(leg["distance"] for leg in legs), abs_tol=1e-7):
        raise ValueError("总距离与路线明细不一致")
    for trip in trips:
        route = trip["legs"]
        if (route[0]["start"] != "STAGING" or route[-1]["end"] != "STAGING"
                or route[-1]["action"] != "unload"
                or any(a["end"] != b["start"] for a, b in zip(route, route[1:]))
                or {leg["owner_code"] for leg in route} != {trip["owner_code"]}):
            raise ValueError("趟次路线或货主隔离校验失败")
        if any(Fraction(leg["load_after"]) > Fraction(leg["capacity"])
               for leg in route if leg["action"] == "pick"):
            raise ValueError("趟次容量校验失败")
    expected = case.get("expected", {})
    if "trips" in expected and len(trips) != expected["trips"]:
        raise ValueError("趟次数与场景预期不符")
    if "trip_loads" in expected and sorted(Fraction(trip["loaded_boxes"]) for trip in trips) != sorted(
        Fraction(str(load)) for load in expected["trip_loads"]
    ):
        raise ValueError("各趟装载量与场景预期不符")
    for field, rows, column in (
        ("fulfilled", result["picks"], "base_quantity"),
        ("shortage", result["shortages"], "shortage_quantity"),
    ):
        if field in expected and sum(Fraction(row[column]) for row in rows) != Fraction(str(expected[field])):
            raise ValueError(f"{field} 与场景预期不符")
    if "distance" in expected and not isclose(result["total_distance"], float(expected["distance"]), abs_tol=1e-7):
        raise ValueError("路线距离与场景预期不符")
    if result["backend"] == "copt" and any(stage.get("used_baseline") for stage in result["solver_stages"]):
        raise ValueError("COPT 有任务回退到初始方案，请检查 solver_stages")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("copt", "heuristic"), default="copt")
    parser.add_argument("--input", type=Path, help="出库请求 JSON 对象或对象数组")
    parser.add_argument("--output", type=Path, help="输出目录")
    parser.add_argument("--time-limit", type=float, default=60, help="每个场景的时限（秒）")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    folder = args.output or root / "results/testing" / datetime.now().strftime("outbound_cli_%Y%m%d_%H%M%S_%f")
    try:
        config = SolverSettings(backend=args.backend, time_limit_s=args.time_limit, threads=args.threads)
        source = args.input or root / "data/testing/outbound_requirement_cases.json"
        cases = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(cases, dict):
            cases = [cases]
        if not isinstance(cases, list) or not cases or any(not isinstance(case, dict) for case in cases):
            raise ValueError("输入必须为出库请求对象或非空对象数组")
        if args.input is None:
            by_id = {case["id"]: case for case in cases}
            cases = [by_id[name] for name in SMOKE_CASES]
        folder.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"ERROR: {error}\n")

    reports = []
    for index, case in enumerate(cases, 1):
        report = dict(id=case.get("id", str(index)), passed=False)
        try:
            result = plan("picking_path", case, output_root=folder, settings={"picking_path": config.model_dump()})
            report.update(run_id=result["run_id"], status=result["status"], distance=result.get("total_distance"),
                          solver_stages=result.get("solver_stages", []))
            check_result(case, result)
            report["passed"] = True
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
            report["error"] = str(error)
        reports.append(report)
        print(f"{'PASS' if report['passed'] else 'FAIL'} {report['id']}: {report.get('error', report.get('status'))}")
    summary = folder / "summary.json"
    summary.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results: {summary.resolve()}")
    return 0 if all(report["passed"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
