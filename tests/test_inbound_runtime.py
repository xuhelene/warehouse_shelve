from contextvars import ContextVar
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from solvers import warehouse_inbound_runtime as runtime
from tests.test_inbound_copt import engine
from tests.test_copt_environment_reuse import track_environments


def test_serial_queue_context_and_same_thread_cleanup(monkeypatch):
    entered, release = Event(), Event()
    scope = ContextVar("runtime_test_scope", default=None)
    events = []
    def create(cp):
        events.append(("create", get_ident()))
        return SimpleNamespace(close=lambda: events.append(("close", get_ident())))
    monkeypatch.setattr(runtime.backend, "create_env", create)
    def solve(*args, session, distance_cache):
        session.acquire(None)
        entered.set()
        assert release.wait(10)
        return dict(scope=scope.get(), config=args[-1])
    monkeypatch.setattr(runtime.backend, "solve", solve)
    worker = runtime.InboundCoptRuntime()
    futures = []
    try:
        token = scope.set("first")
        futures.append(worker.submit(None, None, None, None, None, {"threads": 2}))
        scope.reset(token)
        assert entered.wait(10)
        for index in range(8):
            token = scope.set(index)
            futures.append(worker.submit(None, None, None, None, None, {}))
            scope.reset(token)
        with pytest.raises(RuntimeError, match="8个请求"):
            worker.submit(None, None, None, None, None, {})
        release.set()
        results = [future.result(timeout=10) for future in futures]
        assert [result["scope"] for result in results] == ["first", *range(8)]
        assert results[0]["config"] == dict(threads=2, aggregate_full=True, copt_read_mode="bulk",
                                           copt_objectives="combined", copt_distance_cache="batch")
        assert results[-1]["copt_runtime"]["environment_reused_calls"] == 8
    finally:
        release.set()
        worker.close()
    worker.close()
    assert events == [("create", worker.thread.ident), ("close", worker.thread.ident)]
    assert worker.thread.ident != get_ident()
    with pytest.raises(RuntimeError, match="关闭"):
        worker.submit()


def test_failure_recreates_environment_without_retry(monkeypatch):
    events = []
    monkeypatch.setattr(runtime.backend, "create_env", lambda cp: SimpleNamespace(close=lambda: events.append("close")))
    def solve(*args, session, distance_cache):
        session.acquire(None)
        if args[0] == "fail":
            raise RuntimeError("connection lost")
        return {}
    monkeypatch.setattr(runtime.backend, "solve", solve)
    worker = runtime.InboundCoptRuntime()
    try:
        with pytest.raises(RuntimeError, match="connection lost") as caught:
            worker.submit("fail", None, None, None, None, {}).result(timeout=10)
        assert events == ["close"]
        assert caught.value.copt_runtime["environment_created"] == 1
        result = worker.submit("ok", None, None, None, None, {}).result(timeout=10)
        assert result["copt_runtime"]["environment_created"] == 2
    finally:
        worker.close()
    assert events == ["close", "close"]


def test_global_lifecycle_is_lazy_and_restartable(monkeypatch):
    runtime.shutdown_runtime()
    runtime.start_runtime()
    assert runtime._runtime is None
    monkeypatch.setattr(runtime.backend, "solve", lambda *args, **kwargs: {})
    try:
        assert runtime.solve(None, None, None, None, None, {})["copt_runtime"]["environment_created"] == 0
        worker = runtime._runtime
        runtime.shutdown_runtime()
        assert not worker.thread.is_alive()
        with pytest.raises(RuntimeError, match="已关闭"):
            runtime.solve()
        runtime.start_runtime()
        assert runtime.solve(None, None, None, None, None, {})["copt_runtime"]["profile"] == "optimized_inbound"
        assert runtime._runtime is not worker
    finally:
        runtime.shutdown_runtime()
        runtime.start_runtime()


def test_optimized_real_model_reuses_environment_and_releases_models(engine, monkeypatch):
    from apps.warehouse_data import prepare
    from core.warehouse_layout import WarehouseLayout
    from tools.generate_inbound_test_data import scenarios, row
    events, models, envs = track_environments(monkeypatch)
    worker = runtime.InboundCoptRuntime()
    results = []
    try:
        for quantity in (80, 40):
            case = scenarios()[0]
            layout = WarehouseLayout()
            stock, orders, capacities = prepare([], [row(qty=quantity)], case["specs"], layout, "slotting")
            layout.slots = {s: layout.slots[s] for s in ("C15-1-2", "C15-2-2")}
            results.append(worker.submit(stock, orders, capacities, layout, {},
                                         dict(time_limit_s=10, threads=1, mip_gap=0)).result(timeout=20))
        assert [sum(item["boxes"] for item in r["assignments"]) for r in results] == [80, 40]
        assert [r["environment_reused"] for r in results] == [False, True]
        assert all(r["solution_read"]["mode"] == "bulk" for r in results)
        assert all(r["objective_strategy"]["actual"] == "combined" for r in results)
        assert len(models) == 2 and len(envs) == 1
        assert all(m.solver.NumVariables() == 0 for m in models)
    finally:
        worker.close()
    assert events.count("environment_close") == 1


def test_http_lifecycle_and_saved_results(engine, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apps import warehouse_service as service
    from core import sku_turnover
    from tools.generate_inbound_test_data import scenarios
    import json
    monkeypatch.setattr(service, "runtime_path", lambda name: tmp_path / name)
    monkeypatch.setattr(sku_turnover, "read_latest", lambda: None)
    monkeypatch.setattr(service, "read_settings", lambda: dict(slotting=dict(backend="copt", time_limit_s=10, threads=1, mip_gap=0)))
    events, models, envs = track_environments(monkeypatch)
    app = FastAPI()
    app.include_router(service.router)
    app.add_event_handler("startup", runtime.start_runtime)
    app.add_event_handler("shutdown", runtime.shutdown_runtime)
    try:
        with TestClient(app) as client:
            for reused in (False, True):
                response = client.post("/api/slotting/plan", json=scenarios()[0])
                assert response.status_code == 200
                result = response.json()
                assert result["status"] == "feasible"
                assert result["environment_reused"] is reused
                assert result["effective_solver_config"]["aggregate_full"] is True
                saved = json.loads((tmp_path / "results/warehouse" / result["run_id"] / "result.json").read_text(encoding="utf-8"))
                assert saved == result
                assert client.get("/api/warehouse/runs/" + result["run_id"]).json() == result
        assert events.count("environment_close") == 1
    finally:
        runtime.shutdown_runtime()
        runtime.start_runtime()
