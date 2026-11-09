"""HTTP 冒烟测试；仅对测试实例运行，会创建两次规划及结果文件。"""
import argparse
import json
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010/whshelve")
    parser.add_argument("--solvers", action="store_true", help="Also verify installed OR-Tools backends inside the application environment")
    args = parser.parse_args()

    def call(path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(args.url.rstrip("/") + path, data=data, method=method,
                          headers={"Content-Type": "application/json"} if data else {})
        with urlopen(request, timeout=90) as response:
            assert response.status == 200, path
            content = response.read()
            return json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content

    for path in ["/", "/modules/slotting", "/modules/picking-path", "/settings",
                 "/warehouse-preview", "/static/warehouse-preview.js", "/openapi.json"]:
        call(path)
    settings = call("/api/settings")
    assert all(settings[key]["backend"] == "heuristic" for key in ("slotting", "picking_path")), "Use a fresh heuristic test instance"
    for key in ("slotting", "picking_path"):
        call("/api/settings/" + key, {**settings[key], "threads": 2}, method="PUT")
    for module in ("slotting", "picking-path"):
        for kind in ("inventory", "orders"):
            assert call(f"/api/{module}/inputs/{kind}/sample")["count"] > 0
    row = dict(sku_id="A", name="A", owner_code="O", owner_name="O", warehouse_code="WH",
               timestamp="2026-01-01", order_no="SO", base_quantity="10", base_unit="瓶",
               conversion_ratio="1", quantity="10", unit="箱", slot_id="C15-1-1",
               staging=False, available_base_quantity="10")
    specs = [dict(编码="A", 所属客户="O", 货品名称="A", 托含量="40")]
    for module, inventory in [("slotting", []), ("picking-path", [row])]:
        result = call(f"/api/{module}/plan", dict(inventory=inventory, orders=[row], specs=specs))
        assert result["status"] == "feasible", result
        assert call("/api/warehouse/runs/" + result["run_id"]) == result
        call("/warehouse-results/" + result["run_id"])
        print(f"PASS {module}: {result['run_id']}")
    print("PASS pages, static assets, sample data, heuristic planning, saved results")
    if args.solvers:
        import pandas as pd
        from solvers.slotting_ortools_backend import solve_with_ortools
        from solvers.picking_path_ortools_backend import solve_picking_path_with_ortools
        from modules.picking_path.service import _all_shortest_paths
        slots = pd.DataFrame([dict(slot_id="S1", pallet_cap=2, slot_cost=1)])
        items = pd.DataFrame([dict(item_id="A", qty_boxes=10, boxes_per_pallet=10)])
        candidates = pd.DataFrame([dict(item_id="A", slot_id="S1", pallet_cap=2, cap_boxes=20)])
        solution, _ = solve_with_ortools(slots, items, candidates, time_limit_s=5, num_search_workers=1)
        assert solution.qty_boxes_in_slot.sum() == 10
        tasks = pd.DataFrame([dict(route_id="R", pick_node_id="P")])
        graph = {"S": {"P": 2}, "P": {"S": 2, "E": 3}, "E": {"P": 3}}
        sequence, meta = solve_picking_path_with_ortools(tasks, graph, "S", "E", _all_shortest_paths,
                                                       time_limit_s=5, num_search_workers=1)
        assert sequence["R"] == ["P"] and meta["objective_value"] == 5
        print("PASS OR-Tools slotting and picking backends")


if __name__ == "__main__":
    main()
