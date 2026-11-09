"""比较出库 CP-SAT 剪枝、初解提示和搜索预算。"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from solvers.warehouse_outbound_ortools import solve
from tools.compare_outbound_ortools import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-hints", action="store_true", help="固定 3 秒预算，每例交替测试提示开关各三次")
    args = parser.parse_args()
    source = Path("data/testing/outbound_distribution_cases.json")
    cases = [c for c in json.loads(source.read_text(encoding="utf-8"))
             if c["suite"] == "multi_sku_multi_pallet"]
    hard = {"multi_WHCP03_4sku_105boxes_mode1_tails", "multi_WHCP01_2sku_105boxes_mode1_tails"}
    target = Path("results/testing") / datetime.now().strftime("outbound_cp_sat_%Y%m%d_%H%M%S_%f.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    print(target, flush=True)
    with target.open("w", encoding="utf-8") as output:
        for index, case in enumerate(cases, 1):
            variants = [(1, True, True), (3, True, True)]
            if case["id"] in hard:
                variants += [(10, False, False), (10, True, False), (10, True, True)]
            if args.compare_hints:
                variants = [(3, True, bool((index + repeat + offset) % 2))
                            for repeat in range(3) for offset in range(2)]
            for limit, prune, hint in variants:
                sample = run(case, dict(backend="ortools", time_limit_s=limit, threads=8),
                             planner=lambda *args: solve(*args[:4], args[4]["storeBatchFlag"], args[5],
                                                         prune=prune, hint=hint))
                row = dict(id=case["id"], limit=limit, prune=prune, hint=hint, **sample)
                output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                output.flush()
            if index % 12 == 0:
                print(f"{index}/{len(cases)}", flush=True)


if __name__ == "__main__":
    main()
