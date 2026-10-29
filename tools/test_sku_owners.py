"""统计库存、订单及两表合并后，同一货品编码对应多个货主编码的情况。"""
import argparse
import csv
import json
from pathlib import Path


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"货品编码", "货主编码", "货主名称"}
        if required - set(reader.fieldnames or []):
            raise ValueError(f"{path} 缺少字段：{sorted(required - set(reader.fieldnames or []))}")
        return list(reader)


def summarize(rows):
    owners_by_sku = {}
    missing = 0
    for row in rows:
        sku, owner = row["货品编码"].strip(), row["货主编码"].strip()
        if not sku or not owner:
            missing += 1
            continue
        names = owners_by_sku.setdefault(sku, {}).setdefault(owner, set())
        names.add(row["货主名称"].strip())
    multiple = {
        sku: {owner: sorted(names) for owner, names in sorted(owners.items())}
        for sku, owners in sorted(owners_by_sku.items()) if len(owners) > 1
    }
    return {"rows": len(rows), "sku_count": len(owners_by_sku),
            "missing_sku_or_owner_rows": missing,
            "multi_owner_sku_count": len(multiple), "multi_owner_skus": multiple}


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=root / "data/slotting/existing_inventory.csv")
    parser.add_argument("--orders", type=Path, default=root / "data/slotting/orders.csv")
    parser.add_argument("--output", type=Path, default=root / "results/sku_owner_check.json")
    args = parser.parse_args()
    inventory, orders = read_rows(args.inventory), read_rows(args.orders)
    report = {"sources": {"inventory": str(args.inventory), "orders": str(args.orders)},
              "inventory": summarize(inventory), "orders": summarize(orders),
              "combined": summarize(inventory + orders)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("inventory", "orders", "combined"):
        item = report[name]
        print(f"{name}: rows={item['rows']}, skus={item['sku_count']}, "
              f"multi_owner_skus={item['multi_owner_sku_count']}, "
              f"missing_sku_or_owner_rows={item['missing_sku_or_owner_rows']}")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
