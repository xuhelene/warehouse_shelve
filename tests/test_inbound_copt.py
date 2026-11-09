"""无有效COPT许可时，用独立MIP引擎验证同一模型及求解阶段。"""
from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from ortools.linear_solver import pywraplp

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from solvers import warehouse_inbound_copt as backend
from tools.generate_inbound_test_data import scenarios, row
from tools.test_inbound_copt import validate, main, objective_cases


class Model:
    def __init__(self):
        self.solver = pywraplp.Solver.CreateSolver("SCIP")
        self.status = None

    def addVar(self, **kwargs):
        return self.solver.BoolVar(kwargs["name"])

    def addConstr(self, expr):
        self.solver.Add(expr)

    def setParam(self, name, value):
        if name == "time":
            self.solver.SetTimeLimit(int(value * 1000))

    def setObjective(self, expr, sense):
        (self.solver.Maximize if sense == 1 else self.solver.Minimize)(expr)

    def solve(self):
        self.status = self.solver.Solve()

    hasmipsol = property(lambda self: self.status in (0, 1))
    objval = property(lambda self: self.solver.Objective().Value())
    bestbnd = property(lambda self: self.solver.Objective().BestBound())
    mipgap = property(lambda self: 0 if self.status == 0 else 1)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(pywraplp.Variable, "x", property(lambda v: v.solution_value()), raising=False)
    constants = SimpleNamespace(BINARY="B", MAXIMIZE=1, MINIMIZE=-1, OPTIMAL=0,
                                Param=SimpleNamespace(Threads="threads", RelGap="gap", TimeLimit="time"))
    from ortools.linear_solver.python.linear_solver_natural_api import SumArray
    monkeypatch.setattr(backend, "load_copt", lambda: (SimpleNamespace(quicksum=lambda terms: SumArray(list(terms))), constants))
    monkeypatch.setattr(backend, "create_env", lambda cp: SimpleNamespace(createModel=lambda name: Model(), close=lambda: None))


def execute(case):
    layout = WarehouseLayout(case.get("layer_weight", 1))
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in case["slots"]}
    original = deepcopy((stock, orders, caps))
    result = backend.solve(stock, orders, caps, layout, {tuple(g[:3]): g[3] for g in case["grades"]}, dict(time_limit_s=10, threads=1, mip_gap=0))
    assert original == (stock, orders, caps)
    validate(stock, orders, caps, layout, result)
    return result


@pytest.mark.parametrize("case", scenarios(), ids=lambda case: case["id"])
def test_all_requirement_invariants(engine, case):
    result = execute(case)
    assert len(result["solver_stages"]) == 3


def test_box_priority_over_tail_count(engine):
    case = scenarios()[0]
    case.update(orders=[row(qty=1), row("B", 40)], inventory=[], slots=["C15-1-3"], grades=[])
    result = execute(case)
    assert sum(r["boxes"] for r in result["assignments"]) == D(40)
    assert result["assignments"][0]["sku_id"] == "B"


def test_distance_before_preference(engine):
    case = scenarios()[0]
    case.update(orders=[row(qty=40)], inventory=[], slots=["C15-45-2", "C15-1-3"], grades=[["WH", "O", "A", "高"]])
    assert execute(case)["assignments"][0]["slot_id"] == "C15-1-3"


def test_global_capacity_selection(engine):
    case = scenarios()[0]
    case.update(orders=[row(qty=1), row("B", 30), row("C", 19)], inventory=[], slots=["C15-1-1"], grades=[])
    result = execute(case)
    assert sum(r["boxes"] for r in result["assignments"]) == D(31)


def test_cli_success_and_expectation_failure(engine, tmp_path):
    import json
    case = scenarios()[0]
    case["copt_expected"] = dict(assigned_boxes="40")
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    assert main(["--input", str(path), "--output", str(tmp_path / "pass")]) == 0
    case["copt_expected"]["assigned_boxes"] = "999"
    path.write_text(json.dumps(case), encoding="utf-8")
    assert main(["--input", str(path), "--output", str(tmp_path / "fail")]) == 1


def test_license_failure_saved(tmp_path, monkeypatch):
    def fail():
        raise RuntimeError("license expired")
    monkeypatch.setattr(backend, "load_copt", fail)
    assert main(["--output", str(tmp_path)]) == 1
    assert "license expired" in (tmp_path / "000/result.json").read_text(encoding="utf-8")


def test_builtin_cli_suite(engine, tmp_path):
    assert main(["--output", str(tmp_path)]) == 0


def test_service_routes_copt_and_saves_failure(engine, tmp_path, monkeypatch):
    from apps import warehouse_service
    from core import sku_turnover
    monkeypatch.setattr(sku_turnover, "read_latest", lambda: None)
    config = dict(backend="copt", time_limit_s=10, threads=1, mip_gap=0)
    settings = dict(slotting=config, picking_path=dict(backend="heuristic", time_limit_s=10))
    case = scenarios()[0]
    result = warehouse_service.plan("slotting", case, output_root=tmp_path, settings=settings)
    assert result["status"] == "feasible"
    assert result["backend"] == "copt"
    assert result["solver_stages"]
    def fail(*args):
        raise RuntimeError("license expired")
    monkeypatch.setattr(backend, "solve", fail)
    result = warehouse_service.plan("slotting", case, output_root=tmp_path, settings=settings)
    assert result["status"] == "failed"
    assert "license expired" in result["message"]
    assert (tmp_path / result["run_id"] / "result.json").exists()


def test_timeout_preserves_incumbent(engine, monkeypatch):
    original = Model.solve
    def timed_out(self):
        original(self)
        self.status = 1
    monkeypatch.setattr(Model, "solve", timed_out)
    result = execute(scenarios()[0])
    assert len(result["solver_stages"]) == 1
    assert result["warnings"]


def test_no_incumbent_fails(engine, monkeypatch):
    monkeypatch.setattr(Model, "solve", lambda self: setattr(self, "status", 6))
    with pytest.raises(RuntimeError, match="COPT"):
        execute(scenarios()[0])
