import json

import pytest

from core.warehouse_layout import WarehouseLayout
from modules.slotting.inbound_objectives import ExactDistanceCache, integer_distance_costs, PREPROCESS_TIMING_FIELDS
from solvers import warehouse_inbound_copt as backend
from tests.test_inbound_copt import engine
from tests.test_inbound_aggregation import run
from tools.compare_inbound_solvers import main
from tools.generate_inbound_test_data import scenarios


def costs(layout, slots, cache=None, count=3):
    return integer_distance_costs(layout, slots, count, float("inf"), cache=cache)


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["id"])
def test_cold_and_warm_cache_keep_all_objectives(engine, case):
    cache = ExactDistanceCache()
    before = run(case, backend, True, "bulk", "combined")
    for repeat in range(2):
        after = run(case, backend, True, "bulk", "combined", distance_cache=cache)
        assert [s["objective"] for s in after["solver_stages"]] == pytest.approx(
            [s["objective"] for s in before["solver_stages"]], abs=1e-7)
        assert after["model_stats"] == before["model_stats"]
        assert all(after["timings"][key] >= 0 for key in PREPROCESS_TIMING_FIELDS)
        assert sum(after["timings"][key] for key in PREPROCESS_TIMING_FIELDS) <= after["timings"]["modeling_s"]
        info = after["objective_strategy"]["geometry_cache"]
        if repeat and after["model_stats"]["assignment_variables"]:
            assert info["enabled"] and info["slot_hits"] > 0
            assert info["slot_misses"] == 0


def test_identical_new_layout_hits_and_candidate_subset_rescaled():
    cache = ExactDistanceCache()
    slots = ["C15-1-2", "C15-2-5"]
    first, meta = costs(WarehouseLayout(), slots, cache, count=1)
    second, warm = costs(WarehouseLayout(), slots[:1], cache, count=8)
    expected, fresh = costs(WarehouseLayout(), slots[:1], count=8)
    assert second == expected and warm["distance_unit"] == fresh["distance_unit"]
    assert warm["preference_weight"] == 9 and meta["preference_weight"] == 2
    assert warm["geometry_cache"]["slot_hits"] == 1 and warm["geometry_cache"]["slot_misses"] == 0
    assert set(second) == {slots[0]} and cache.report()["cached_slots"] == 2
    assert cache.stats["generations"] == 1
    # 相同距离、数量变化后仍重做系数规模检查，不能缓存旧的安全结论。
    unsafe, reason = costs(WarehouseLayout(), slots[:1], cache, count=10**9)
    assert unsafe is None and reason["fallback_reason"] == "integer_coefficients_too_large"


@pytest.mark.parametrize("change", ["weight", "staging", "node", "slot_node", "slot_level", "graph_topology", "graph_cost"])
def test_geometry_change_invalidates_and_matches_uncached(change):
    cache = ExactDistanceCache()
    slot = "C15-1-2"
    costs(WarehouseLayout(), [slot], cache)
    layout = WarehouseLayout()
    if change == "weight":
        layout.layer_weight = 3
    elif change == "staging":
        layout.nodes["STAGING"]["x"] += 0.25
        layout.edge("STAGING", "FRONT")
    elif change == "node":
        layout.nodes["0:23.575"]["x"] += 0.25
    elif change == "slot_node":
        layout.slots[slot]["node"] = layout.slots["C15-2-2"]["node"]
    elif change == "slot_level":
        layout.slots[slot]["level"] = 3
    elif change == "graph_topology":
        del layout.graph["FRONT"]["1:-1.75"]
        del layout.graph["1:-1.75"]["FRONT"]
    elif change == "graph_cost":
        layout.graph["STAGING"]["FRONT"] += 1
        layout.graph["FRONT"]["STAGING"] += 1
    actual, meta = costs(layout, [slot], cache)
    expected, reference = costs(layout, [slot])
    assert actual == expected
    assert meta.get("fallback_reason") == reference.get("fallback_reason")
    assert meta["geometry_cache"]["changed"] and meta["geometry_cache"]["slot_hits"] == 0
    assert cache.stats["invalidations"] == 1 and cache.stats["generations"] == 2


def test_overridden_distance_bypasses_cache():
    cache = ExactDistanceCache()
    slots = ["C15-1-2"]
    costs(WarehouseLayout(), slots, cache)
    layout = WarehouseLayout()
    original = layout.leg
    layout.leg = lambda a, b: original(a, b)
    actual, meta = costs(layout, slots, cache)
    expected, _ = costs(layout, slots)
    assert actual == expected and not meta["geometry_cache"]["enabled"]
    assert cache.stats["bypassed"] == 1


def test_cli_cache_counts_and_timings(tmp_path, engine):
    case = scenarios()[0]
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    records = {}
    for mode in ("off", "batch"):
        folder = tmp_path / mode
        assert main(["--input", str(path), "--output", str(folder), "--backends", "copt", "--model-variant", "aggregate",
                     "--copt-env", "reuse", "--copt-read", "bulk", "--copt-objectives", "combined",
                     "--copt-distance-cache", mode, "--repeats", "3", "--threads", "1"]) == 0
        records[mode] = json.loads((folder / "runs.json").read_text(encoding="utf-8"))
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        assert summary["backends"]["copt"]["objective_strategies"]["solve_calls"] == 6
        assert all(field in summary["backends"]["copt"]["timings"] for field in PREPROCESS_TIMING_FIELDS)
        report = json.loads((folder / "distance_cache.json").read_text(encoding="utf-8"))
        if mode == "batch":
            assert report["generations"] == 1 and report["invalidations"] == 0
            assert report["slot_hits"] == 2 * report["slot_misses"]
            assert report["checks"] == 3
        assert summary["distance_cache"] == report
    a, b = records["off"], records["batch"]
    assert [(r["assigned_boxes"], r["distance"], r["preference_violations"]) for r in a] == [
        (r["assigned_boxes"], r["distance"], r["preference_violations"]) for r in b]
