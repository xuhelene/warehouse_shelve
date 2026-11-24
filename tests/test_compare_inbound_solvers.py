import json

from tools.compare_inbound_solvers import compare, main
from tests.test_inbound_copt import engine


def test_ranking_uses_quantity_then_distance_then_preference():
    a = dict(assigned_boxes="40", distance=100, preference_violations=3)
    b = dict(assigned_boxes="39.999999999999999999", distance=1, preference_violations=0)
    assert compare(a, b) == -1
    b["assigned_boxes"] = "40"
    assert compare(a, b) == 1
    b["distance"] = 100
    assert compare(a, b) == 1
    b["preference_violations"] = 3
    assert compare(a, b) == 0


def test_three_backends_outputs_and_missing_license(tmp_path, engine, monkeypatch):
    from tools.test_inbound_copt import objective_cases
    from solvers import warehouse_inbound_copt, warehouse_inbound_ortools
    def no_shortcut(*args):
        raise AssertionError("SCIP比较不应走匹配快捷路径")
    monkeypatch.setattr(warehouse_inbound_ortools, "match", no_shortcut)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(objective_cases(), ensure_ascii=False), encoding="utf-8")
    folder = tmp_path / "pass"
    arguments = ["--input", str(path), "--threads", "1", "--time-limit", "10", "--repeats", "2"]
    assert main(arguments + ["--output", str(folder)]) == 0
    rows = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
    assert len(rows) == 18
    assert all(r["solver_method"] == "SCIP" for r in rows if r["backend"] == "ortools")
    assert {r["backend"] for r in rows} == {"copt", "ortools", "heuristic"}
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["backends"]["heuristic"]["optimal_runs"] == 0
    assert summary["pairs"]["copt_vs_ortools"]["equal"] == 6
    assert summary["backends"]["copt"]["timings"]["environment_s"]["runs"] == 6
    assert summary["backends"]["ortools"]["timings"]["quantity_solve_s"]["runs"] == 6
    for r in rows:
        if r["backend"] == "copt":
            from solvers.warehouse_inbound_copt import TIMING_FIELDS
            assert all(r[k] >= 0 for k in TIMING_FIELDS)
            assert abs(sum(r[k] for k in TIMING_FIELDS) + r["other_s"] - r["total_s"]) < 1e-6
            assert r["total_s"] <= r["seconds"]
    assert len((folder / "comparison.csv").read_text(encoding="utf-8-sig").splitlines()) == 19
    def fail():
        raise RuntimeError("license expired")
    monkeypatch.setattr(warehouse_inbound_copt, "load_copt", fail)
    folder = tmp_path / "fail"
    assert main(arguments + ["--output", str(folder)]) == 1
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["backends"]["copt"]["failed"] == 6
    assert summary["backends"]["ortools"]["passed"] == 6
    assert summary["pairs"]["copt_vs_ortools"]["comparable_runs"] == 0


def test_customer_directory_empty_and_invalid_dates(tmp_path, monkeypatch):
    from datetime import date
    from tools import inbound_customer_cases as loader
    from tools.generate_inbound_test_data import scenarios
    base = scenarios()[0]
    for kind in ("入库订单", "库存快照"):
        (tmp_path / kind).mkdir()
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        (tmp_path / "入库订单" / f"{day}.csv").write_text(day)
    (tmp_path / "库存快照/2026-09-01.csv").write_text("stock")
    specs = tmp_path / "sku.csv"
    specs.write_text("specs")
    monkeypatch.setattr(loader, "load_specs", lambda *args: dict(rows=base["specs"]))
    def load(kind, content, filename, **kwargs):
        if filename == "2026-09-02.csv":
            raise ValueError("文件中没有数据")
        return dict(rows=base["orders"] if kind == "orders" else base["inventory"])
    monkeypatch.setattr(loader, "load_input", load)
    cases, report = loader.load_cases(tmp_path, specs)
    assert [c["id"] for c in cases] == ["2026-09-01", "2026-09-03"]
    assert cases[0]["grades"] == []
    assert "_input_error" in cases[1]
    assert report["dates"][1]["status"] == "skipped_empty_orders"
    cases, report = loader.load_cases(tmp_path, specs, date(2026, 9, 3), date(2026, 9, 3))
    assert len(cases) == 1


def test_aggregate_cli_records_reduced_model(tmp_path, engine):
    from tools.generate_inbound_test_data import scenarios, row
    case = scenarios()[0]
    case.update(orders=[row(qty=120)], slots=["C15-1-2", "C15-2-2", "C15-3-2"], grades=[])
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    folder = tmp_path / "aggregate"
    assert main(["--input", str(path), "--output", str(folder), "--threads", "1", "--model-variant", "aggregate"]) == 0
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    for backend in ("copt", "ortools"):
        assert summary["backends"][backend]["model_stats"]["removed_assignment_variables"]["total"] == 6
    rows = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
    assert all(r["assigned_boxes"] == "120" for r in rows)
