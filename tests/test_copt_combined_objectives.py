from fractions import Fraction
import json

import pytest

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from modules.slotting.inbound_objectives import integer_distance_costs
from solvers import warehouse_inbound_copt as backend
from tests.test_inbound_copt import engine, Model
from tests.test_inbound_aggregation import run
from tools.compare_inbound_solvers import main
from tools.generate_inbound_test_data import scenarios, row
from tools.test_inbound_copt import validate


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["id"])
def test_combined_matches_three_business_objectives(engine, case):
    before = run(case, backend, True, "bulk", "sequential")
    after = run(case, backend, True, "bulk", "combined")
    assert after["model_stats"] == before["model_stats"]
    assert [s["objective"] for s in after["solver_stages"]] == pytest.approx(
        [s["objective"] for s in before["solver_stages"]], abs=1e-7)
    if after["model_stats"]["assignment_variables"]:
        strategy = after["objective_strategy"]
        assert strategy["actual"] == "combined" and strategy["solve_calls"] == 2
        assert after["solution_read"]["variable_read_calls"] == 2
        assert strategy["combined_stage"]["proven_optimal"]
        assert all(s["derived_from"] == "combined" for s in after["solver_stages"][1:])


@pytest.mark.parametrize("weight", [0, 0.01, 0.5, 3])
@pytest.mark.parametrize("grade", ["高", "中", "低"])
def test_layer_weight_and_grade_keep_same_priority(engine, weight, grade):
    case = scenarios()[0]
    case.update(orders=[row(qty=81)], slots=["C15-1-1", "C15-1-2", "C15-2-2", "C15-1-5"],
                grades=[["WH", "O", "A", grade]], layer_weight=weight)
    a, b = (run(case, backend, True, "bulk", mode) for mode in ("sequential", "combined"))
    assert [s["objective"] for s in a["solver_stages"]] == pytest.approx([s["objective"] for s in b["solver_stages"]])


def test_fractional_boxes_not_rounded(engine):
    case = scenarios()[0]
    case.update(orders=[{**row(qty=1), "conversion_ratio": "6", "quantity": "0.167"}],
                slots=["C15-1-1", "C15-1-3"], grades=[])
    a, b = (run(case, backend, True, "bulk", mode) for mode in ("sequential", "combined"))
    assert a["assignments"][0]["base_quantity"] == b["assignments"][0]["base_quantity"] == 1


def test_exact_distance_unit_and_dominance_bound():
    layout = WarehouseLayout(0.5)
    slots = ["C15-1-2", "C15-1-5", "C14-45-3"]
    costs, meta = integer_distance_costs(layout, slots, 5, float("inf"))
    assert meta["preference_weight"] == 6
    unit = Fraction(meta["distance_unit"])
    for slot, cost in costs.items():
        assert float(cost * unit) == pytest.approx(layout.leg("STAGING", slot)["distance"])
    # 距离成本差1单位，也严格胜过最多5个偏好改善。
    assert 10 * 6 + 5 < 11 * 6


def test_unsafe_scaling_falls_back_to_original_model(engine):
    case = scenarios()[0]
    case.update(orders=[row(qty=40)], slots=["C15-1-2", "C15-1-3"], layer_weight=0.123456789012345)
    result = run(case, backend, True, "bulk", "combined")
    strategy = result["objective_strategy"]
    assert strategy["actual"] == "sequential" and strategy["solve_calls"] == 3
    assert strategy["fallback_reason"] == "integer_coefficients_too_large"
    assert result["warnings"]


def test_non_geometric_distances_rejected():
    layout = WarehouseLayout()
    original = layout.leg
    layout.leg = lambda a, b: dict(original(a, b), distance=original(a, b)["distance"] + 0.1)
    costs, meta = integer_distance_costs(layout, ["C15-1-2"], 1, float("inf"))
    assert costs is None and meta["fallback_reason"] == "unsupported_distance_geometry"


def test_timeout_combined_does_not_claim_independent_optima(engine, monkeypatch):
    case = scenarios()[0]
    layout = WarehouseLayout()
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in case["slots"]}
    original = Model.solve
    calls = []
    def timeout(self):
        original(self)
        calls.append(1)
        if len(calls) == 2:
            self.status = 1
    monkeypatch.setattr(Model, "solve", timeout)
    result = backend.solve(stock, orders, caps, layout, {}, dict(time_limit_s=10, threads=1, copt_objectives="combined"))
    validate(stock, orders, caps, layout, result)
    assert result["optimality"] == "not_proven"
    assert result["solver_stages"][0]["proven_optimal"]
    assert all(s["best_bound"] is None and s["mip_gap"] is None and not s["proven_optimal"] for s in result["solver_stages"][1:])


def test_combined_cli_keeps_two_actual_calls_and_zero_gap(tmp_path, engine, monkeypatch):
    calls = []
    original = Model.setParam
    def record(self, name, value):
        if name == "gap":
            calls.append(value)
        original(self, name, value)
    monkeypatch.setattr(Model, "setParam", record)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(scenarios()[0]), encoding="utf-8")
    output = tmp_path / "combined"
    assert main(["--input", str(path), "--output", str(output), "--backends", "copt", "--threads", "1",
                 "--model-variant", "aggregate", "--copt-env", "reuse", "--copt-read", "bulk", "--copt-objectives", "combined"]) == 0
    assert calls and all(value == 0 for value in calls)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["backends"]["copt"]["objective_strategies"] == dict(runs=1, actual_counts=dict(combined=1), solve_calls=2, fallback_runs=0)
    record = json.loads((output / "runs.json").read_text(encoding="utf-8"))[0]
    assert record["combined_solve_s"] > 0
    assert record["distance_solve_s"] == record["preference_solve_s"] == 0
    assert record["objective_strategy"]["combined_stage"]["objective"] >= 0
