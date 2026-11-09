"""小规模最短闭合路径枚举，只评估距离质量，不宣称启发式全局最优。"""
from datetime import datetime
from itertools import combinations, permutations
import json
from pathlib import Path
from time import monotonic

from apps.warehouse_data import prepare
from core.warehouse_layout import WarehouseLayout
from solvers.warehouse_outbound_heuristic import solve
from tools.generate_outbound_test_data import row


POINTS = ["C14-1-1", "C14-18-1", "C14-45-1", "C15-1-1", "C15-18-1", "C15-45-1"]
SAMPLES = [list(slots) for n in (4, 5) for slots in combinations(POINTS, n)]


def evaluate(slots, solver=solve):
    layout = WarehouseLayout()
    raw_stock, raw_orders = [row(qty=1, slot=s) for s in slots], [row(qty=len(slots))]
    specs = [dict(编码="A", 所属客户="O", 托含量="40")]
    stock, orders, caps = prepare(raw_stock, raw_orders, specs, layout, "picking_path")
    result = solver(stock, orders, caps, layout, 1, monotonic() + 10)
    # 此处只有单货主单SKU且一托装得下，枚举访问顺序即可求该场景最优闭合路径。
    def distance(order):
        points = ["STAGING", *order, "STAGING"]
        return sum(layout.leg(a, b)["distance"] for a, b in zip(points, points[1:]))
    optimal_order = min(permutations(slots), key=distance)
    optimum = distance(optimal_order)
    return dict(inventory=raw_stock, orders=raw_orders, specs=specs, storeBatchFlag=1, result=result,
                optimal_order=list(optimal_order), optimal_distance=optimum,
                heuristic_distance=result["total_distance"], gap_percent=100 * (result["total_distance"] / optimum - 1))


if __name__ == "__main__":
    rows = [evaluate(slots) for slots in SAMPLES]
    report = dict(scope="单货主单SKU、一托足够、4/5停靠点的21个小实例", cases=rows,
                  optimal_count=sum(abs(r["gap_percent"]) < 1e-9 for r in rows),
                  max_gap_percent=max(r["gap_percent"] for r in rows))
    path = Path(__file__).resolve().parents[1] / "results/testing" / datetime.now().strftime("outbound_quality_%Y%m%d_%H%M%S_%f.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"{len(rows)} cases; optimal {report['optimal_count']}; max gap {report['max_gap_percent']:.3f}%; {path}")
