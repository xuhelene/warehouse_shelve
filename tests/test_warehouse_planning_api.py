import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps import warehouse_service as service
from core import sku_turnover
from test_warehouse_heuristic import row


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = {key: dict(backend="heuristic", time_limit_s=10, threads=8) for key in ("slotting", "picking_path")}
    monkeypatch.setattr(service, "read_settings", lambda: settings)
    monkeypatch.setattr(service, "runtime_path", lambda name: tmp_path / name)
    monkeypatch.setattr(sku_turnover, "read_latest", lambda: None)
    app = FastAPI()
    app.include_router(service.router)
    return TestClient(app)


def test_plan_save_read_result_and_input_validation(client):
    payload = dict(inventory=[row()], orders=[row(qty=15)],
                   specs=[dict(编码="A", 所属客户="O", 货品名称="A", 托含量="40")])
    result = client.post("/api/picking-path/plan", json=payload)
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "partial"
    assert data["shortages"][0]["shortage_quantity"] == "5"
    assert client.get("/api/warehouse/runs/" + data["run_id"]).json() == data
    assert "warehouse-result.js" in client.get("/warehouse-results/" + data["run_id"]).text
    assert client.get("/api/warehouse/runs/invalid").status_code == 404
    assert client.post("/api/slotting/plan", json={}).status_code == 422
    payload["storeBatchFlag"] = 2
    assert client.post("/api/picking-path/plan", json=payload).json()["status"] == "failed"


def test_inbound_plan_and_upload_specs(client):
    content = "编码,货品名称,所属客户,托含量\nA,A,O,40\n".encode()
    response = client.post("/api/warehouse/specs", files={"file": ("sku.csv", content, "text/csv")})
    assert response.status_code == 200
    specs = response.json()["rows"]
    response = client.post("/api/slotting/plan", json=dict(inventory=[], orders=[row(qty=40)], specs=specs))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "feasible"
    assert result["assignments"][0]["level"] == 5


def test_inputs_are_received_by_unified_router(client):
    inventory = client.get("/api/slotting/inputs/inventory/sample")
    outbound = client.get("/api/picking-path/inputs/orders/sample")
    inbound = client.get("/api/slotting/inputs/orders/sample")
    assert inventory.status_code == outbound.status_code == inbound.status_code == 200
    assert inventory.json()["count"] == 274
    assert all(r["operation"] == "入库" for r in inbound.json()["rows"])
    assert all(r["operation"] == "出库" for r in outbound.json()["rows"])
    assert client.get("/api/unknown/inputs/orders/sample").status_code == 404
    assert client.post("/api/slotting/inputs/orders", files={"file": ("bad.csv", b"bad\n1")}).status_code == 422


def test_platform_uses_same_post_and_refreshes_data_each_time(client, monkeypatch):
    snapshots = iter([[row(qty=10)], [row(qty=2)]])
    calls = []

    def inventory_get(request):
        calls.append("inventory")
        return next(snapshots)

    def sku_get(ids, request):
        calls.append(("specs", ids))
        assert request["inventory"][0]["base_quantity"] in {"10", "2"}
        return [dict(编码="A", 所属客户="O", 货品名称="A", 托含量="40")]

    monkeypatch.setattr(service, "get_inventory_snapshot", inventory_get)
    monkeypatch.setattr(service, "get_sku_master_data", sku_get)
    payload = dict(source="platform", orders=[row(qty=5)], inventory=[row(qty=999)])
    first = client.post("/api/picking-path/plan", json=payload).json()
    second = client.post("/api/picking-path/plan", json=payload).json()
    assert first["status"] == "feasible"
    assert second["status"] == "partial"
    assert second["shortages"][0]["shortage_quantity"] == "3"
    assert calls == ["inventory", ("specs", ["A"]), "inventory", ("specs", ["A"])]
    assert first["source"] == second["source"] == "platform"


def test_platform_not_configured_fails_without_file_fallback(client):
    result = client.post("/api/slotting/plan", json=dict(source="platform", orders=[row()])).json()
    assert result["status"] == "failed"
    assert "尚未联调" in result["message"]
    assert "assignments" not in result
