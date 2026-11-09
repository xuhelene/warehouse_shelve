"""重放受限布局数据集，将每个场景的输入和算法结果保存到 results。"""
from datetime import datetime
import json
from pathlib import Path
from time import monotonic

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from solvers.warehouse_inbound_heuristic import solve


def main():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "data/testing/inbound_requirement_cases.json").read_text(encoding="utf-8"))
    folder = root / "results/testing" / datetime.now().strftime("inbound_%Y%m%d_%H%M%S_%f")
    for case in cases:
        target = folder / case["id"]
        target.mkdir(parents=True)
        (target / "input.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        layout = WarehouseLayout()
        inventory, orders, caps = prepare(case["inventory"], case["orders"], case["specs"], layout, "slotting")
        layout.slots = {s: layout.slots[s] for s in case["slots"]}
        result = solve(inventory, orders, caps, layout, {tuple(g[:3]): g[3] for g in case["grades"]}, monotonic() + 10)
        result.update(backend="heuristic", time_limit_s=10, layer_weight=1)
        (target / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Saved {len(cases)} input/result pairs: {folder}")


if __name__ == "__main__":
    main()
