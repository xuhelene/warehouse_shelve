from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.picking_path.preprocess import write_pick_points


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build picking points from storage slots and aisle areas.")
    parser.add_argument("--slots", default="data/slotting/slots.csv")
    parser.add_argument("--aisles", default="data/picking_path/picking_aisles.csv")
    parser.add_argument("--output", default="data/picking_path/picking_points.csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pick_points = write_pick_points(args.slots, args.aisles, args.output)
    print(f"Wrote {len(pick_points)} picking points to {Path(args.output)}")


if __name__ == "__main__":
    main()
