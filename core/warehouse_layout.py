"""现有 C14—C16 布局的米制双向通道；只在前端及20—22列贯通处横穿。"""
import heapq
from functools import lru_cache


class WarehouseLayout:
    def __init__(self, layer_weight=1.0):
        self.layer_weight = layer_weight
        self.shortest = lru_cache(maxsize=None)(self.shortest)
        self.slots, self.nodes, self.graph = {}, {}, {}
        lanes = {"C14": -3.4375, "C15": 2.3125, "C16": 2.3125}
        ys = sorted({-1.75, 23.575} | {round((c - .5) * 1.15, 6) for c in range(1, 46)})
        for lane, x in enumerate(sorted(set(lanes.values()))):
            previous = None
            for y in ys:
                node = f"{lane}:{y}"
                self.nodes[node] = dict(x=x, y=y)
                self.graph[node] = {}
                if previous is not None:
                    self.edge(previous, node)
                previous = node
        for y in (-1.75, 23.575):
            self.edge(f"0:{y}", f"1:{y}")
        self.nodes["STAGING"] = dict(x=-.5625, y=-3.5)
        self.nodes["FRONT"] = dict(x=-.5625, y=-1.75)
        self.graph.update(STAGING={}, FRONT={})
        # 将前通道拆开，使备货区接入点与两侧通道真实连通。
        del self.graph["0:-1.75"]["1:-1.75"]
        del self.graph["1:-1.75"]["0:-1.75"]
        self.edge("STAGING", "FRONT")
        self.edge("FRONT", "0:-1.75")
        self.edge("FRONT", "1:-1.75")
        for rack, x in lanes.items():
            for column in range(1, 46):
                y = round((column - .5) * 1.15, 6)
                node = f"{0 if x < 0 else 1}:{y}"
                for level in range(1, 6):
                    if 20 <= column <= 22 and level <= 2:
                        continue
                    slot = f"{rack}-{column}-{level}"
                    self.slots[slot] = dict(slot_id=slot, rack=rack, column=column, level=level, node=node)

    def edge(self, a, b):
        p, q = self.nodes[a], self.nodes[b]
        distance = abs(p["x"] - q["x"]) + abs(p["y"] - q["y"])
        self.graph[a][b] = self.graph[b][a] = distance

    def shortest(self, start, end):
        queue, distances, prev = [(0., start)], {start: 0.}, {}
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == end:
                path = [end]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                return distance, list(reversed(path))
            for nxt, length in self.graph.get(node, {}).items():
                total = distance + length
                if total < distances.get(nxt, float("inf")):
                    distances[nxt], prev[nxt] = total, node
                    heapq.heappush(queue, (total, nxt))
        raise ValueError(f"通道不可达：{start} → {end}")

    def leg(self, start, end):
        a, b = self.slots.get(start), self.slots.get(end)
        distance, path = self.shortest(a["node"] if a else start, b["node"] if b else end)
        vertical = abs((a["level"] - 1 if a else 0) - (b["level"] - 1 if b else 0)) * 1.7 * self.layer_weight
        return dict(start=start, end=end, distance=distance + vertical,
                    horizontal_distance=distance, vertical_distance=vertical,
                    path=[self.nodes[n] for n in path])
