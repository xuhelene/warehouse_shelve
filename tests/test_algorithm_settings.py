import json

import pytest
from pydantic import ValidationError

from core.algorithm_settings import SolverSettings, read_settings, save_settings


def test_initial_values_do_not_write_file(tmp_path):
    path = tmp_path / "settings.json"
    data = read_settings(path)
    assert not path.exists()
    assert data["persisted"] is False
    for module in ("slotting", "picking_path"):
        assert data[module] == dict(backend="copt", time_limit_s=60, threads=8, mip_gap=0.005)


def test_tabs_save_independently_and_survive_reload(tmp_path):
    path = tmp_path / "config" / "algorithm_config.json"
    save_settings("slotting", SolverSettings(backend="ortools", threads=4), path)
    save_settings("picking_path", SolverSettings(backend="heuristic", time_limit_s=30), path)
    data = read_settings(path)
    assert data["slotting"]["backend"] == "ortools"
    assert data["slotting"]["threads"] == 4
    assert data["picking_path"]["backend"] == "heuristic"
    assert data["version"] == 2
    assert data["planning_connected"] is True
    assert json.loads(path.read_text())["version"] == 2


@pytest.mark.parametrize("values", [dict(backend="other"), dict(time_limit_s=0), dict(time_limit_s=float("inf")), dict(threads=1.5), dict(threads=0), dict(mip_gap=-1), dict(mip_gap=2)])
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        SolverSettings(**values)


def test_corrupt_file_is_not_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        save_settings("slotting", SolverSettings(), path)
    assert path.read_text() == "broken"


def test_incomplete_file_is_not_replaced_with_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="缺少必要字段"):
        save_settings("slotting", SolverSettings(), path)
    assert path.read_text() == "{}"


def test_failed_replace_preserves_old_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    save_settings("slotting", SolverSettings(), path)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("test replace failure")

    monkeypatch.setattr("core.algorithm_settings.os.replace", fail_replace)
    with pytest.raises(OSError):
        save_settings("slotting", SolverSettings(threads=2), path)
    assert path.read_bytes() == original
    assert not path.with_suffix(".json.tmp").exists()
