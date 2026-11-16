"""拣货启发式选择的配置、调用与结果持久化。"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps import settings_api, warehouse_service
from core.algorithm_settings import read_settings, save_settings
from tools.generate_outbound_test_data import scenarios


@pytest.mark.parametrize("algorithm", ["VNS", "ALNS"])
def test_saved_algorithm_is_used_and_recorded(tmp_path, monkeypatch, algorithm):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_api, "read_settings", lambda: read_settings(path))
    monkeypatch.setattr(settings_api, "save_settings", lambda module, values: save_settings(module, values, path))
    monkeypatch.setattr(warehouse_service, "read_settings", lambda: read_settings(path))
    monkeypatch.setattr(warehouse_service, "runtime_path", lambda name: tmp_path / name)
    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(warehouse_service.router)
    with TestClient(app) as client:
        response = client.put("/api/settings/picking_path", json={
            "backend": "heuristic", "heuristic_algorithm": algorithm, "time_limit_s": 10})
        assert response.status_code == 200, response.text
        assert client.get("/api/settings").json()["picking_path"]["heuristic_algorithm"] == algorithm
        data = client.post("/api/picking-path/plan", json=scenarios()[0]).json()
        assert data["status"] in {"feasible", "partial"}, data
        assert data["route_strategy"] == algorithm
        assert data["settings"]["picking_path"]["heuristic_algorithm"] == algorithm
        saved = tmp_path / "results/warehouse" / data["run_id"] / "result.json"
        assert json.loads(saved.read_text(encoding="utf-8"))["route_strategy"] == algorithm
        bad = client.put("/api/settings/picking_path", json={"heuristic_algorithm": "unknown"})
        assert bad.status_code == 422
        assert read_settings(path)["picking_path"]["heuristic_algorithm"] == algorithm
