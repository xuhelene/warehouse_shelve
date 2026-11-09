"""出库HTTP入口、实时快照与求解结果持久化回归。"""
from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps import warehouse_service as service
from modules.picking_path import planning_service
from tools.generate_outbound_test_data import row, scenarios


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = {key: dict(backend="heuristic", time_limit_s=10, threads=8)
                for key in ("slotting", "picking_path")}
    monkeypatch.setattr(service, "read_settings", lambda: settings)
    monkeypatch.setattr(service, "runtime_path", lambda name: tmp_path / name)
    app = FastAPI()
    app.include_router(service.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("name", ["fractional_full_not_tail", "fractional_exact_full_load", "fractional_capacity_validation"])
def test_precision_fix_survives_api_and_json_save(client, tmp_path, name):
    case = next(c for c in scenarios() if c["id"] == name)
    original = deepcopy(case)
    result = client.post("/api/picking-path/plan", json=case)
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "feasible", data
    assert case == original
    saved = tmp_path / "results/warehouse" / data["run_id"]
    assert json.loads((saved / "result.json").read_text(encoding="utf-8")) == data
    assert json.loads((saved / "input.json").read_text(encoding="utf-8"))["inventory"] == original["inventory"]
    assert client.get("/api/warehouse/runs/" + data["run_id"]).json() == data
    assert data["settings"]["picking_path"]["threads"] == 8
    assert any("单线程" in w for w in data["warnings"])
    if name == "fractional_full_not_tail":
        assert data["picks"][0]["inventory_index"] == 3
    elif name == "fractional_exact_full_load":
        assert len(data["picks"]) == 4
        assert len(data["trips"]) == 2
    else:
        assert len(data["trips"]) == 1


def test_wave_shortage_preserves_owner_order_and_unit(client, tmp_path):
    payload = dict(storeBatchFlag=1, inventory=[row(qty=3), row(qty=7, owner="P", slot="C15-2-1")],
                   orders=[row(qty=5), row(qty=8, owner="P", order="SO-2")], specs=scenarios()[0]["specs"])
    first = client.post("/api/picking-path/plan", json=payload).json()
    second = client.post("/api/picking-path/plan", json=payload).json()
    assert first["status"] == "partial" and first["run_id"] != second["run_id"]
    assert [(r["order_no"], r["owner_code"], r["shortage_quantity"], r["unit"]) for r in first["shortages"]] == [
        ("SO-1", "O", "2", "瓶"), ("SO-2", "P", "1", "瓶")]
    assert len(first["trips"]) == 2
    for data in (first, second):
        saved = tmp_path / "results/warehouse" / data["run_id"]
        assert json.loads((saved / "result.json").read_text(encoding="utf-8")) == data


def test_ortools_plan_is_saved_and_retrievable(tmp_path, monkeypatch):
    settings = {"picking_path": dict(backend="ortools", time_limit_s=10, threads=8)}
    monkeypatch.setattr(service, "runtime_path", lambda name: tmp_path / name)
    case = next(c for c in scenarios() if c["id"] == "fractional_exact_full_load")
    result = service.plan("picking_path", case, settings=settings)
    assert result["status"] == "feasible", result
    assert result["backend"] == "ortools"
    assert result["route_strategy"] == "OR-Tools"
    assert result["solver_stages"][0]["status"] == "OPTIMAL"
    assert len(result["trips"]) == 2
    assert service.read_run(result["run_id"]) == result


def test_platform_refreshes_both_snapshot_and_capacity(client, monkeypatch):
    snapshots = iter([[row(qty=10)], [row(qty=1)]])
    specs = iter([[dict(编码="A", 所属客户="O", 托含量="5")], [dict(编码="A", 所属客户="O", 托含量="40")]])
    calls = []

    def fetch_stock(request):
        calls.append("inventory")
        return next(snapshots)

    def fetch_specs(ids, request):
        calls.append(("specs", ids))
        return next(specs)

    monkeypatch.setattr(service, "get_inventory_snapshot", fetch_stock)
    monkeypatch.setattr(service, "get_sku_master_data", fetch_specs)
    # 第一次是非法超容量快照，应明确失败；第二次不能沿用第一次库存或SKU容量。
    payload = dict(source="platform", storeBatchFlag=1, inventory=[row(qty=999)], orders=[row(qty=2)], specs=[])
    first = client.post("/api/picking-path/plan", json=payload).json()
    second = client.post("/api/picking-path/plan", json=payload).json()
    assert first["status"] == "failed" and "容量" in first["message"]
    assert second["status"] == "partial" and second["shortages"][0]["shortage_quantity"] == "1"
    assert second["legs"][0]["capacity"] == "40"
    assert calls == ["inventory", ("specs", ["A"]), "inventory", ("specs", ["A"])]


@pytest.mark.parametrize("kind", ["timeout", "missing_master", "platform_unconfigured", "invalid_flag"])
def test_failed_runs_are_saved_without_partial_picks(client, tmp_path, monkeypatch, kind):
    payload = dict(inventory=[row()], orders=[row()], specs=scenarios()[0]["specs"], storeBatchFlag=1)
    if kind == "timeout":
        monkeypatch.setattr(planning_service, "monotonic", lambda: -100)
    elif kind == "missing_master":
        payload["specs"] = []
    elif kind == "platform_unconfigured":
        payload["source"] = "platform"
    else:
        payload["storeBatchFlag"] = True
    data = client.post("/api/picking-path/plan", json=payload).json()
    assert data["status"] == "failed" and "picks" not in data
    saved = tmp_path / "results/warehouse" / data["run_id"]
    assert (saved / "input.json").exists()
    assert json.loads((saved / "result.json").read_text(encoding="utf-8")) == data
