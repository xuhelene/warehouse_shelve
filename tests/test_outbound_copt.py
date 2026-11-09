"""用独立 MIP 引擎验证出库分托和路径模型，无需 COPT 许可。"""
from decimal import Decimal
from types import SimpleNamespace

from ortools.linear_solver import pywraplp
import pytest

from solvers import warehouse_outbound_copt as backend
from solvers import warehouse_outbound_ortools
from tests.test_inbound_copt import Model


@pytest.mark.parametrize("solver, strategy", [(backend, "COPT"), (warehouse_outbound_ortools, "OR-Tools")])
def test_repartitions_trips_to_reduce_complete_distance(monkeypatch, solver, strategy):
    class RoutingModel(Model):
        def addVar(self, vtype=None, lb=0, ub=1):
            if vtype == "B":
                return self.solver.BoolVar("")
            return self.solver.NumVar(lb, ub, "")

    monkeypatch.setattr(pywraplp.Variable, "x", property(lambda v: v.solution_value()), raising=False)
    constants = SimpleNamespace(BINARY="B", MINIMIZE=-1, OPTIMAL=0, TIMEOUT=8,
                                Param=SimpleNamespace(Threads="threads", RelGap="gap", TimeLimit="time"))
    env = SimpleNamespace(createModel=lambda name: RoutingModel(), close=lambda: None)
    monkeypatch.setattr(backend, "load_copt", lambda: (SimpleNamespace(quicksum=sum), constants))
    monkeypatch.setattr(backend, "create_env", lambda cp: env)
    positions = dict(STAGING=0, A=10, B=-10, C=11, D=-11)
    layout = SimpleNamespace(
        slots={slot: {"level": 1} for slot in "ABCD"},
        leg=lambda start, end: dict(start=start, end=end, distance=abs(positions[start] - positions[end])),
    )
    stock = [dict(slot_id=slot, sku_id=slot, owner_code="O", base_unit="箱", timestamp=0,
                  base_quantity=Decimal(5), available_base=Decimal(5), conversion_ratio=Decimal(1))
             for slot in "ABCD"]
    orders = [{**row, "order_no": "SO"} for row in stock]
    capacities = {("O", slot): Decimal(10) for slot in "ABCD"}
    result = solver.solve(stock, orders, capacities, layout, 0, dict(time_limit_s=10, threads=1))
    assert result["route_strategy"] == strategy
    assert result["search"]["initial_distance"] == 84
    assert result["total_distance"] == 44
    assert not result["solver_stages"][0].get("used_baseline", False)
    assert [trip["loaded_boxes"] for trip in result["trips"]] == [10, 10]
    assert sorted(row["sku_id"] for row in result["picks"]) == list("ABCD")
    assert all(row["available_base"] == 0 for row in result["remaining_inventory"])
