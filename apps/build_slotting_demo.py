"""将原始货物随机落位，并生成一笔含 10 个 SKU 的入库单。"""
from pathlib import Path
import random
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from modules.slotting.preprocess import prepare_slotting_data


def main() -> None:
    data = PROJECT_ROOT / "data" / "slotting"
    rng = random.Random(20260910)
    slots, items, candidates = prepare_slotting_data(
        str(data / "slots.csv"), str(data / "items_virtual.csv")
    )
    free = set(slots["slot_id"])
    rows = []
    for item in items.to_dict("records"):
        choices = candidates[candidates["item_id"].eq(item["item_id"])].to_dict("records")
        rng.shuffle(choices)
        remaining = int(item["qty_boxes"])
        for choice in choices:
            if choice["slot_id"] not in free or remaining <= 0:
                continue
            qty = min(remaining, int(choice["cap_boxes"]))
            rows.append({**item, "slot_id": choice["slot_id"], "qty_boxes": qty})
            free.remove(choice["slot_id"])
            remaining -= qty
        if remaining:
            raise ValueError(f"Insufficient slots for existing item {item['item_id']}")
    inventory = pd.DataFrame(rows)
    inbound = items.drop_duplicates("sku_id").iloc[rng.sample(range(items["sku_id"].nunique()), 10)].copy()
    inbound["qty_boxes"] = [rng.randint(10, 100) for _ in range(10)]
    inbound["order_id"] = "IN_20260910_001"
    inbound["batch_id"] = [f"IN_20260910_{i:03d}" for i in range(1, 11)]
    source_columns = list(pd.read_csv(data / "items_virtual.csv", nrows=0).columns)
    inventory[source_columns + ["slot_id"]].to_csv(data / "existing_inventory.csv", index=False)
    inbound[source_columns + ["order_id"]].to_csv(data / "inbound_items.csv", index=False)
    print(f"Existing: {len(inventory)} slots, {int(inventory.qty_boxes.sum())} boxes; inbound: 10 SKUs, {int(inbound.qty_boxes.sum())} boxes; free slots: {len(free)}")


if __name__ == "__main__":
    main()
