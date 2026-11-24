"""精确距离整数化与批次缓存；不满足数值边界时拒绝合并。"""
from fractions import Fraction
from math import gcd, isfinite, lcm
from time import perf_counter

from core.warehouse_layout import WarehouseLayout
from modules.slotting.inbound_model import check_deadline


PREPROCESS_TIMING_FIELDS = ("distance_cache_check_s", "distance_geometry_s", "distance_integerize_s", "combined_expression_s")


class ExactDistanceCache:
    """仅缓存最后一种完整几何的精确距离，不缓存库存、解或目标权重。"""
    def __init__(self):
        self.signature = None
        self.distances = {}
        self.horizontal = {}
        self.stats = dict(checks=0, generations=0, invalidations=0, bypassed=0, slot_hits=0, slot_misses=0)

    def prepare(self, layout):
        self.stats["checks"] += 1
        # 自定义距离函数不作缓存假设，仍走原逐次几何验证。
        wrapped = getattr(getattr(layout, "shortest", None), "__wrapped__", None)
        if (type(layout) is not WarehouseLayout or "leg" in vars(layout)
                or getattr(wrapped, "__func__", None) is not WarehouseLayout.shortest):
            self.stats["bypassed"] += 1
            return False, False
        signature = (
            WarehouseLayout.leg, WarehouseLayout.shortest, str(layout.layer_weight),
            tuple(sorted((n, str(p["x"]), str(p["y"])) for n, p in layout.nodes.items())),
            tuple(sorted((n, tuple(sorted((end, str(cost)) for end, cost in edges.items())))
                         for n, edges in layout.graph.items())),
            tuple(sorted((s, p["node"], str(p["level"])) for s, p in layout.slots.items())),
        )
        changed = signature != self.signature
        if changed:
            self.stats["invalidations"] += int(self.signature is not None)
            self.stats["generations"] += 1
            self.signature = signature
            self.distances.clear()
            self.horizontal.clear()
        return True, changed

    def report(self):
        return dict(self.stats, cached_slots=len(self.distances), cached_nodes=len(self.horizontal))


def integer_distance_costs(layout, slots, group_count, deadline, *, cache=None):
    horizontal, distances = {}, {}
    weight = group_count + 1
    timings = dict.fromkeys(PREPROCESS_TIMING_FIELDS, 0.0)
    cache_info = dict(requested=cache is not None, enabled=False, changed=False, slot_hits=0, slot_misses=0)
    def result(costs=None, **metadata):
        return costs, dict(metadata, preprocess_timings=timings, geometry_cache=cache_info)
    if not slots:
        return result(fallback_reason="no_assignment_variables")
    try:
        vertical_step = Fraction("1.7") * Fraction(str(layout.layer_weight))
        if vertical_step < 0:
            return result(fallback_reason="negative_layer_weight")
        tick = perf_counter()
        try:
            if cache is not None:
                enabled, changed = cache.prepare(layout)
                cache_info.update(enabled=enabled, changed=changed)
                if enabled:
                    horizontal = cache.horizontal
                    cache_info["generation"] = cache.stats["generations"]
        finally:
            timings["distance_cache_check_s"] += perf_counter() - tick
        tick = perf_counter()
        try:
            for slot in slots:
                check_deadline(deadline)
                if cache_info["enabled"] and slot in cache.distances:
                    distances[slot] = cache.distances[slot]
                    cache_info["slot_hits"] += 1
                    cache.stats["slot_hits"] += 1
                    continue
                cache_info["slot_misses"] += 1
                if cache_info["enabled"]:
                    cache.stats["slot_misses"] += 1
                info = layout.slots[slot]
                leg = layout.leg("STAGING", slot)
                node = info["node"]
                if node not in horizontal:
                    points = [(Fraction(str(p["x"])), Fraction(str(p["y"]))) for p in leg["path"]]
                    horizontal[node] = sum((abs(x - a) + abs(y - b)
                        for (x, y), (a, b) in zip(points, points[1:])), Fraction(0))
                distance = horizontal[node] + (info["level"] - 1) * vertical_step
                if distance < 0 or not isfinite(leg["distance"]) or abs(float(distance) - leg["distance"]) > 1e-8:
                    return result(fallback_reason="unsupported_distance_geometry")
                distances[slot] = distance
                if cache_info["enabled"]:
                    cache.distances[slot] = distance
        finally:
            timings["distance_geometry_s"] += perf_counter() - tick
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return result(fallback_reason="unsupported_distance_geometry")
    tick = perf_counter()
    try:
        # 只对本次候选做整数化，不能把缓存中不参与本次模型的货位混进来。
        scale = lcm(*(d.denominator for d in distances.values()))
        costs = {s: int(d * scale) for s, d in distances.items()}
        divisor = gcd(*costs.values()) or 1
        costs = {s: c // divisor for s, c in costs.items()}
        maximum = max(costs.values())
        metadata = dict(distance_unit=str(Fraction(divisor, scale)), preference_weight=weight,
                        max_combined_coefficient=maximum * weight + 1,
                        combined_objective_upper_bound=maximum * weight * group_count + group_count)
        if metadata["max_combined_coefficient"] > 10**7 or metadata["combined_objective_upper_bound"] > 10**12:
            return result(**metadata, fallback_reason="integer_coefficients_too_large")
        return result(costs, **metadata)
    finally:
        timings["distance_integerize_s"] += perf_counter() - tick
