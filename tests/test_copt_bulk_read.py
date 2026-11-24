import json
from types import SimpleNamespace

import pytest
from ortools.linear_solver import pywraplp

from solvers import warehouse_inbound_copt as backend
from tests.test_inbound_copt import engine, Model
from tests.test_inbound_aggregation import run
from tools.generate_inbound_test_data import scenarios, row
from tools.compare_inbound_solvers import main


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["id"])
def test_bulk_equivalent_for_all_business_cases(engine, case):
    scalar = run(case, backend, True, "scalar")
    bulk = run(case, backend, True, "bulk")
    assert [s["objective"] for s in scalar["solver_stages"]] == pytest.approx(
        [s["objective"] for s in bulk["solver_stages"]], abs=1e-7)
    assert scalar["model_stats"] == bulk["model_stats"]
    variables = bulk["model_stats"]["assignment_variables"]
    assert bulk["solution_read"]["variable_read_calls"] == (3 if variables else 0)
    assert bulk["solution_read"]["variable_values_read"] == 3 * variables
    assert scalar["solution_read"]["variable_read_calls"] == 3 * variables
    assert bulk["timings"]["variable_read_s"] <= bulk["timings"]["result_read_s"]


def test_bulk_avoids_scalar_reads_and_preserves_variable_order(engine, monkeypatch):
    def scalar_forbidden(var):
        raise AssertionError("bulk不得访问变量.x")
    monkeypatch.setattr(pywraplp.Variable, "x", property(scalar_forbidden))
    calls = []
    original = Model.getInfo
    def record(self, info, variables):
        calls.append([v.name() for v in variables])
        assert all(name.startswith("x_") for name in calls[-1])
        return original(self, info, variables)
    monkeypatch.setattr(Model, "getInfo", record)
    # 混托场景还含used_辅助变量；批量接口只接收x变量。
    case = next(c for c in scenarios() if c["id"] == "mix_three")
    result = run(case, backend, True, "bulk")
    assert len(result["assignments"]) == 3
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]


@pytest.mark.parametrize("values, message", [([1], "长度"), ([1, float("nan")], "非有限"), ([0, float("inf")], "非有限")])
def test_invalid_bulk_vector_fails_without_fallback(values, message):
    variables = {("i", "B"): object(), ("j", "A"): object()}
    def get_info(info, received):
        assert info == "Value" and received == list(variables.values())
        return values
    with pytest.raises(RuntimeError, match=message):
        backend.read_selection(SimpleNamespace(getInfo=get_info), variables, SimpleNamespace(Info=SimpleNamespace(Value="Value")), "bulk")


def test_bulk_cli_reports_counter_and_reuse(tmp_path, engine):
    case = scenarios()[0]
    case.update(orders=[row(qty=120)], slots=["C15-1-2", "C15-2-2", "C15-3-2"], grades=[])
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    for mode in ("scalar", "bulk"):
        folder = tmp_path / mode
        assert main(["--input", str(path), "--output", str(folder), "--model-variant", "aggregate",
                     "--copt-env", "reuse", "--copt-read", mode, "--repeats", "2", "--threads", "1", "--backends", "copt"]) == 0
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        reads = summary["backends"]["copt"]["solution_read"]
        assert set(summary["backends"]) == {"copt"} and summary["pairs"] == {}
        assert reads["modes"] == [mode] and reads["variable_values_read"] == 18
        assert reads["variable_read_calls"] == (6 if mode == "bulk" else 18)
        assert summary["copt_environment"]["created"] == 1
        assert summary["copt_environment"]["reused_calls"] == 1
        rows = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
        assert all(r["copt_read_mode"] == mode for r in rows if r["backend"] == "copt")


def test_bulk_failure_keeps_diagnostics_and_invalidates_environment(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(Model, "getInfo", lambda *args: [])
    path = tmp_path / "case.json"
    path.write_text(json.dumps(scenarios()[0]), encoding="utf-8")
    folder = tmp_path / "failed"
    assert main(["--input", str(path), "--output", str(folder), "--copt-env", "reuse", "--copt-read", "bulk",
                 "--threads", "1", "--backends", "copt"]) == 1
    rows = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
    failure = next(r for r in rows if r["backend"] == "copt")
    assert not failure["passed"] and "长度" in failure["error"]
    assert failure["variable_read_calls"] == 1 and failure["variable_values_read"] == 0
    assert failure["variable_read_s"] <= failure["result_read_s"]
    lifecycle = json.loads((folder / "copt_environment.json").read_text(encoding="utf-8"))
    assert lifecycle["created"] == 1 and lifecycle["close_attempts"] == 1
