"""比较近30天出库量及出入库总量的ABC候选阈值；不修改正式等级。"""
import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import json
from math import ceil
from pathlib import Path


def load_totals(path, end_date=None):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"操作类型", "操作时间", "仓库编码", "货主编码", "货品编码", "货品名称", "主单位数量", "转换率"}
        if required - set(reader.fieldnames or []):
            raise ValueError(f"缺少字段：{sorted(required - set(reader.fieldnames or []))}")
        rows = [r for r in reader if r["操作类型"].strip() in {"入库", "出库"}]
    if not rows:
        raise ValueError("没有出入库记录")
    dates = [date.fromisoformat(r["操作时间"].strip()[:10]) for r in rows]
    end_date = end_date or max(dates)
    start = end_date - timedelta(days=29)
    totals = {}
    counts = {"入库": 0, "出库": 0}
    for line, (r, day) in enumerate(zip(rows, dates), 2):
        if not start <= day <= end_date:
            continue
        key = tuple(r[field].strip() for field in ("仓库编码", "货主编码", "货品编码"))
        if not all(key):
            raise ValueError(f"记录{line}：仓库、货主、SKU不能为空")
        qty, ratio = Decimal(r["主单位数量"]), Decimal(r["转换率"])
        if not qty.is_finite() or not ratio.is_finite() or qty < 0 or ratio <= 0:
            raise ValueError(f"记录{line}：数量或转换率无效")
        item = totals.setdefault(key, {"name": r["货品名称"], "入库": Decimal(0), "出库": Decimal(0)})
        item[r["操作类型"].strip()] += qty / ratio
        counts[r["操作类型"].strip()] += 1
    if not totals:
        raise ValueError("窗口内没有出入库记录")
    return totals, start, end_date, counts


def classify(values, method, a, b):
    """候选累计贡献法包含跨越边界的整组同量条目；零量为C。"""
    positive = sorted(((key, qty) for key, qty in values.items() if qty > 0), key=lambda x: (-x[1], x[0][2], x[0][1]))
    grades = {key: "C" for key in values}
    total = sum(values.values())
    for rank, (key, qty) in enumerate(positive, 1):
        if method in {"count", "thirds"}:
            high = ceil(len(positive) / 3) if method == "thirds" else ceil(len(positive) * a)
            medium = 2 * high if method == "thirds" else ceil(len(positive) * b)
            grade = "A" if rank <= high else "B" if rank <= medium else "C"
        else:
            # 同量条目使用该组开始前的贡献占比，避免数值相同却被分为不同等级。
            before = sum(v for _, v in positive if v > qty)
            grade = "A" if before < total * Decimal(str(a)) else "B" if before < total * Decimal(str(b)) else "C"
        grades[key] = grade
    return grades


def analyze(totals):
    summaries, details = [], []
    candidates = [("原三等分", "thirds", 1/3, 2/3),
                  ("条目10%/30%", "count", .1, .3), ("条目20%/50%", "count", .2, .5),
                  ("贡献70%/90%", "contribution", .7, .9),
                  ("贡献80%/95%", "contribution", .8, .95),
                  ("贡献90%/98%", "contribution", .9, .98)]
    for warehouse in sorted({key[0] for key in totals}):
        for metric in ("仅出库", "入库+出库"):
            values = {key: item["出库"] + (item["入库"] if metric == "入库+出库" else 0)
                      for key, item in totals.items() if key[0] == warehouse}
            volume = sum(values.values())
            for name, method, a, b in candidates:
                grades = classify(values, method, a, b)
                summary = dict(warehouse=warehouse, metric=metric, scheme=name, total_boxes=str(volume),
                               entries=len(values), positive_entries=sum(v > 0 for v in values.values()))
                for grade in "ABC":
                    group = [values[key] for key in values if grades[key] == grade]
                    summary[grade + "_count"] = len(group)
                    summary[grade + "_share"] = float(sum(group) / volume) if volume else None
                    summary[grade + "_min_boxes"] = str(min(group)) if group else None
                    summary[grade + "_max_boxes"] = str(max(group)) if group else None
                summaries.append(summary)
                for key, qty in sorted(values.items(), key=lambda x: (-x[1], x[0][2], x[0][1])):
                    item = totals[key]
                    details.append(dict(warehouse=warehouse, owner=key[1], sku=key[2], name=item["name"],
                                        metric=metric, scheme=name, inbound_boxes=str(item["入库"]),
                                        outbound_boxes=str(item["出库"]), metric_boxes=str(qty), grade=grades[key]))
    return summaries, details


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=root / "data/slotting/orders.csv")
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=root / "results/turnover_analysis")
    args = parser.parse_args()
    totals, start, end, counts = load_totals(args.orders, args.end_date)
    summary, details = analyze(totals)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in (("summary", summary), ("details", details)):
        with (args.output / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    report = dict(source=str(args.orders.resolve()), start=str(start), end=str(end), row_counts=counts, summary=summary)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = [f"# 周转等级候选阈值分析\n\n统计期：{start} 至 {end}（连续30天）。",
            "按仓库＋货主＋SKU分别累计，仓库内统一排名；箱数=主单位数量÷转换率。",
            "入库+出库为总搬运箱数，两项相加而非相减。比较对象为窗口内出入库条目的并集，仅出库口径中零出库条目归C；整期无任何记录的货品不在此样本内。",
            "累计贡献法：达到阈值的边界条目纳入较高等级，同箱数条目整组保留，因此实际贡献可能超过目标；条目比例法按SKU、货主编码稳定排序，可能拆开同量条目。",
            "| 仓库 | 指标 | 方案 | A/B/C条目数 | A贡献 | A最低箱数 | B最低箱数 |\n|---|---|---|---|---|---|---|"]
    for r in summary:
        share = f"{r['A_share']:.1%}" if r['A_share'] is not None else "无流量"
        text.append(f"| {r['warehouse']} | {r['metric']} | {r['scheme']} | {r['A_count']}/{r['B_count']}/{r['C_count']} | {share} | {r['A_min_boxes']} | {r['B_min_boxes']} |")
    text.extend(["\n建议先将累计贡献80%/95%作为讨论起点：A级覆盖主要业务量，同时减少原三等分中被列为高周转的条目；B级覆盖至95%，其余为C。样例量较小，阈值尚未确认，不直接写入正式等级配置。候选70%/90%、90%/98%用于比较更集中或更宽的覆盖范围。",
                 "出库量反映拣货需求，出入库合计反映搬运活动；大批入库后尚未销售的货品会在合计口径中升档，不一定代表高出库需求。两者均非出库量÷平均库存的严格库存周转率。",
                 "新规则高周转优先第2层、第1层只放尾板；不能用A级条目数直接推算第一层需求，也不能用某个ABC阈值保证不拥挤。缺少每托箱数，尚不能计算各级实际占用托数、尾板数及第2层容量匹配。未做自动移位或修改正式算法。"])
    (args.output / "analysis.md").write_text("\n\n".join(text[:4]) + "\n\n" + "\n".join(text[4:]), encoding="utf-8")
    print(f"{start} ~ {end}; records={counts}; report={args.output / 'analysis.md'}")


if __name__ == "__main__":
    main()
