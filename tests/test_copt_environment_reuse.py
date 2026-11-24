import json
from types import SimpleNamespace
from threading import Thread

import pytest

from solvers import warehouse_inbound_copt as backend
from tests.test_inbound_copt import engine, Model
from tools.compare_inbound_solvers import main
from tools.generate_inbound_test_data import scenarios, row


def track_environments(monkeypatch, modern=False):
    events, models, envs = [], [], []
    class TrackedModel(Model):
        def clear(self):
            events.append("model_clear")
            super().clear()
    if modern:
        def dispose(self):
            events.append("model_dispose")
            self.solver.Clear()
        TrackedModel.dispose = dispose
    class Environment:
        def createModel(self, name):
            events.append("create_model")
            model = TrackedModel()
            models.append(model)
            return model

        def close(self):
            events.append("environment_close")
    if modern:
        Environment.dispose = lambda self: events.append("environment_dispose")
    def create(cp):
        events.append("create_environment")
        env = Environment()
        envs.append(env)
        return env
    monkeypatch.setattr(backend, "create_env", create)
    return events, models, envs


@pytest.mark.parametrize("modern", [False, True])
def test_cli_fresh_vs_reuse_equivalence_and_cleanup(tmp_path, engine, monkeypatch, modern):
    events, models, envs = track_environments(monkeypatch, modern)
    case = scenarios()[0]
    case.update(orders=[row(qty=120)], slots=["C15-1-2", "C15-2-2", "C15-3-2"], grades=[])
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    outputs = {}
    for mode in ("fresh", "reuse"):
        folder = tmp_path / mode
        assert main(["--input", str(path), "--output", str(folder), "--model-variant", "aggregate",
                     "--copt-env", mode, "--repeats", "3", "--threads", "1"]) == 0
        outputs[mode] = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
    a, b = outputs["fresh"], outputs["reuse"]
    assert [(r["assigned_boxes"], r["distance"], r["preference_violations"], r["optimality"]) for r in a] == [
        (r["assigned_boxes"], r["distance"], r["preference_violations"], r["optimality"]) for r in b]
    reused = [r for r in b if r["backend"] == "copt"]
    assert [r["environment_reused"] for r in reused] == [False, True, True]
    assert {r["model_cleanup"] for r in reused} == {"dispose" if modern else "clear"}
    assert len(envs) == 4  # fresh三次，reuse一次
    assert len(models) == 6 and len({id(m) for m in models}) == 6
    assert all(m.solver.NumVariables() == 0 for m in models)
    assert events.count("environment_close") == 4
    if modern:
        assert events.count("environment_dispose") == 4
    summary = json.loads((tmp_path / "reuse/summary.json").read_text(encoding="utf-8"))
    lifecycle = summary["copt_environment"]
    assert lifecycle["created"] == 1 and lifecycle["reused_calls"] == 2
    assert lifecycle["close_attempts"] == 1 and not lifecycle["cleanup_errors"]
    assert lifecycle["accounted_seconds"] == pytest.approx(lifecycle["sum_call_seconds"] + lifecycle["final_close_seconds"])


def test_failed_model_invalidates_session_and_next_call_recovers(engine, monkeypatch):
    from apps.warehouse_data import prepare
    from core.warehouse_layout import WarehouseLayout
    events, models, envs = track_environments(monkeypatch)
    session = backend.CoptEnvironmentSession()
    case = scenarios()[0]
    layout = WarehouseLayout()
    stock, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
    layout.slots = {s: layout.slots[s] for s in case["slots"]}
    original = Model.solve
    monkeypatch.setattr(Model, "solve", lambda self: (_ for _ in ()).throw(RuntimeError("connection lost")))
    config = dict(time_limit_s=10, threads=1, mip_gap=0)
    with pytest.raises(RuntimeError, match="connection lost"):
        backend.solve(stock, orders, caps, layout, {}, config, session=session)
    assert session.env is None and events.count("environment_close") == 1
    monkeypatch.setattr(Model, "solve", original)
    result = backend.solve(stock, orders, caps, layout, {}, config, session=session)
    assert result["status"] == "feasible" and session.stats["created"] == 2
    session.close()
    session.close()  # 重复关闭不重复释放
    assert events.count("environment_close") == 2


def test_cross_thread_and_closed_session_are_rejected(engine, monkeypatch):
    events, models, envs = track_environments(monkeypatch)
    session = backend.CoptEnvironmentSession()
    errors = []
    def call():
        try:
            session.acquire(None)
        except RuntimeError as error:
            errors.append(str(error))
    thread = Thread(target=call)
    thread.start()
    thread.join()
    assert errors and not envs
    session.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        session.acquire(None)


def test_final_close_failure_saved_and_returns_nonzero(tmp_path, engine, monkeypatch):
    env = SimpleNamespace(createModel=lambda name: Model(), close=lambda: (_ for _ in ()).throw(RuntimeError("close failed")))
    monkeypatch.setattr(backend, "create_env", lambda cp: env)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(scenarios()[0]), encoding="utf-8")
    output = tmp_path / "output"
    assert main(["--input", str(path), "--output", str(output), "--copt-env", "reuse", "--threads", "1"]) == 1
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["copt_environment"]["cleanup_errors"] == ["close failed"]
