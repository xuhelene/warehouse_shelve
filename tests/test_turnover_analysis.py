from decimal import Decimal

from tools.analyze_turnover_thresholds import analyze, classify


def test_contribution_boundary_ties_and_zero():
    values = {("W", "A", str(i)): Decimal(v) for i, v in enumerate([80, 10, 10, 0])}
    grades = classify(values, "contribution", .8, .95)
    assert list(grades.values()) == ["A", "B", "B", "C"]
    assert list(classify(values, "thirds", 1/3, 2/3).values()) == ["A", "B", "C", "C"]


def test_original_thirds_rounding():
    values = {("W", "A", str(i)): Decimal(10-i) for i in range(4)}
    assert list(classify(values, "thirds", 1/3, 2/3).values()) == ["A", "A", "B", "B"]


def test_inbound_and_owner_isolation():
    totals = {("W", "A", "001"): {"name": "x", "入库": Decimal(100), "出库": Decimal(0)},
              ("W", "B", "001"): {"name": "x", "入库": Decimal(0), "出库": Decimal(10)}}
    summary, detail = analyze(totals)
    assert {r["total_boxes"] for r in summary if r["metric"] == "仅出库"} == {"10"}
    assert {r["total_boxes"] for r in summary if r["metric"] == "入库+出库"} == {"110"}
    assert all(r["grade"] == "C" for r in detail if r["metric"] == "仅出库" and r["owner"] == "A")
