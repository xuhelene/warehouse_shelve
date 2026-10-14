from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import SolveOptions, SolverConfig
from modules.slotting.service import solve_slotting


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warehouse slotting optimizer")
    parser.add_argument("--slots", default="data/slotting/slots.csv")
    # parser.add_argument("--items", default="items.csv")
    parser.add_argument("--outbound", default=None)
    parser.add_argument("--items", default="data/slotting/inbound_items.csv")
    parser.add_argument("--existing-inventory", default="data/slotting/existing_inventory.csv")
    parser.add_argument("--picking-points", default="data/picking_path/picking_points.csv",
                        help="Slot-to-aisle picking geometry; used by the ortools proximity penalty.")
    parser.add_argument("--picking-aisles", default="data/picking_path/picking_aisles.csv",
                        help="Aisle definitions (needs the cross aisle) for the proximity penalty.")
    parser.add_argument("--output", default="results/slotting_solution.csv")
    parser.add_argument("--backend", choices=["heuristic", "copt", "ortools"], default="copt")
    parser.add_argument("--time-limit", type=float, default=60.0)
    parser.add_argument("--alpha-slots", type=float, default=0.01)
    parser.add_argument("--alpha-proximity", type=float, default=1.0,
                        help="L1 span penalty for SKUs split across slots (ortools backend only). 0 disables.")
    parser.add_argument("--proximity-max-distance", type=float, default=0.0,
                        help="Elastic cap K (meters) on the picking detour between slots of one SKU. 0 disables.")
    parser.add_argument("--alpha-proximity-excess", type=float, default=50.0,
                        help="Penalty weight per meter beyond --proximity-max-distance.")
    parser.add_argument("--cover-days", type=int, default=14)
    parser.add_argument("--robust-days", type=int, default=60)
    parser.add_argument("--min-stock-boxes", type=int, default=0)
    parser.add_argument("--candidate-slots-per-item", type=int, default=None)
    parser.add_argument("--mip-gap", type=float, default=0.005,
                        help="Relative MIP gap tolerance for copt/ortools backends. 0 proves optimality.")
    parser.add_argument("--num-search-workers", type=int, default=8)
    parser.add_argument("--fallback-qty-min", type=int, default=200)
    parser.add_argument("--fallback-qty-max", type=int, default=1200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    options = SolveOptions(
        backend=args.backend,
        time_limit_s=args.time_limit,
        alpha_slots=args.alpha_slots,
        use_demand_if_present=True,
        candidate_slots_per_item=args.candidate_slots_per_item,
        num_search_workers=args.num_search_workers,
        alpha_proximity=args.alpha_proximity,
        proximity_max_distance=args.proximity_max_distance,
        alpha_proximity_excess=args.alpha_proximity_excess,
        mip_gap=args.mip_gap,
    )

    sol, meta = solve_slotting(
        slots_path=args.slots,
        items_virtual_path=args.items,
        existing_inventory_path=args.existing_inventory,
        picking_points_path=args.picking_points,
        picking_aisles_path=args.picking_aisles,
        cfg=SolverConfig(),
        options=options,
    )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    sol.to_csv(args.output, index=False)

    print("\n=== Solve Meta ===")
    for k, v in meta.items():
        print(f"{k}: {v}")

    print("\n=== Solution Preview ===")
    print(sol.head(20))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
