from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.picking_path.service import solve_picking_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warehouse picking path optimizer")
    parser.add_argument("--order", default="data/picking_path/orders_wave.csv")
    parser.add_argument("--inventory", default="results/slotting_solution.csv")
    parser.add_argument("--nodes", default="data/picking_path/picking_nodes.csv")
    parser.add_argument("--network", default="data/picking_path/picking_network.csv")
    parser.add_argument("--stations", default="data/picking_path/picking_stations.csv")
    parser.add_argument("--pick-points", default="data/picking_path/picking_points.csv")
    parser.add_argument("--output-dir", default="results/picking_path")
    parser.add_argument("--backend", choices=["heuristic", "ortools"], default="heuristic",
                        help="picking-path 暂未实现 copt 后端")
    parser.add_argument("--mode", choices=["per-order", "wave"], default="per-order",
                        help="per-order=一单一趟；wave=整份订单表合并成一个波次，只走一条路径")
    parser.add_argument("--start-station", default="PICK_START")
    parser.add_argument("--end-station", default="OUTBOUND")
    parser.add_argument("--time-limit", type=float, default=60.0)
    parser.add_argument("--num-search-workers", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    route, reports, meta = solve_picking_path(
        order_path=args.order,
        inventory_path=args.inventory,
        nodes_path=args.nodes,
        network_path=args.network,
        stations_path=args.stations,
        pick_points_path=args.pick_points,
        backend=args.backend,
        mode=args.mode,
        start_station=args.start_station,
        end_station=args.end_station,
        time_limit_s=args.time_limit,
        num_search_workers=args.num_search_workers,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    route_path = output_dir / "picking_route.csv"
    order_summary_path = output_dir / "picking_order_summary.csv"
    unfulfilled_path = output_dir / "picking_unfulfilled.csv"
    route.to_csv(route_path, index=False)
    reports["order_summary"].to_csv(order_summary_path, index=False)
    reports["unfulfilled"].to_csv(unfulfilled_path, index=False)

    print("\n=== Solve Meta ===")
    for key, value in meta.items():
        print(f"{key}: {value}")
    print(f"\nSaved: {route_path}")
    print(f"Saved: {order_summary_path}")
    print(f"Saved: {unfulfilled_path}")


if __name__ == "__main__":
    main()
