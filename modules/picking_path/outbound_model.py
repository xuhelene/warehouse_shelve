"""按货主和任务构建带托盘容量的闭合路径模型。"""
from time import monotonic


def build_model(cp, copt, model, picks, boxes, limits, distance, deadline):
    # -1 为备货区。拣货点各有一条入弧和出弧，备货区允许多次进出。
    slots = [row["slot_id"] for row in picks]
    depot = -1
    nodes = range(len(slots))
    locations = {**dict(enumerate(slots)), depot: "STAGING"}
    arcs = {}
    maximum = float(max(limits))
    load = [model.addVar(lb=float(boxes[i]), ub=float(limits[i])) for i in nodes]
    capacity = [model.addVar(lb=float(boxes[i]), ub=float(limits[i])) for i in nodes]
    position = [model.addVar(lb=1, ub=len(slots)) for i in nodes]
    incoming = [[] for i in nodes]
    outgoing = [[] for i in nodes]
    for i in locations:
        for j in locations:
            if monotonic() >= deadline:
                raise TimeoutError("出库COPT建模达到时间上限")
            if i == j:
                continue
            if i != depot and j != depot and boxes[i] + boxes[j] > min(limits[i], limits[j]):
                continue
            arc = arcs[i, j] = model.addVar(vtype=copt.BINARY)
            if i != depot:
                outgoing[i].append(arc)
            if j != depot:
                incoming[j].append(arc)
                if i != depot:
                    # 托内载量递增，容量只能降低。序号约束排除零距离子回路。
                    model.addConstr(load[j] >= load[i] + float(boxes[j]) - maximum * (1 - arc))
                    model.addConstr(capacity[j] <= capacity[i] + maximum * (1 - arc))
                    model.addConstr(position[j] >= position[i] + 1 - len(slots) * (1 - arc))
    for i in nodes:
        model.addConstr(load[i] <= capacity[i])
        model.addConstr(cp.quicksum(incoming[i]) == 1)
        model.addConstr(cp.quicksum(outgoing[i]) == 1)
    model.setObjective(cp.quicksum(distance(locations[i], locations[j]) * arc
                                  for (i, j), arc in arcs.items()), copt.MINIMIZE)
    return arcs

def build_ortools_model(model, slots, boxes, limits, distance, deadline, *, prune=True):
    """用备货区作为公共起终点，每条闭合路径对应一托。"""
    locations = ["STAGING", *slots]
    loads = [None] + [model.new_int_var(q, cap, "") for q, cap in zip(boxes, limits)]
    capacities = [None] + [model.new_int_var(q, cap, "") for q, cap in zip(boxes, limits)]
    arcs, costs = {}, []
    for i, start in enumerate(locations):
        for j, end in enumerate(locations):
            if monotonic() >= deadline:
                raise TimeoutError
            if i == j:
                continue
            if prune and i and j and boxes[i - 1] + boxes[j - 1] > min(limits[i - 1], limits[j - 1]):
                continue
            arc = arcs[i, j] = model.new_bool_var("")
            costs.append(distance(start, end) * arc)
            if j and i:
                model.add(loads[j] == loads[i] + boxes[j - 1]).only_enforce_if(arc)
                model.add(capacities[j] <= capacities[i]).only_enforce_if(arc)
            elif j:
                model.add(loads[j] == boxes[j - 1]).only_enforce_if(arc)
    for load, cap in zip(loads[1:], capacities[1:]):
        model.add(load <= cap)
    model.add_multiple_circuit([(i, j, arc) for (i, j), arc in arcs.items()])
    model.minimize(sum(costs))
    return arcs
