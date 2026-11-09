"""ALNS实验后端：自适应移除/修复、退火接受；复用FIFO和精确容量解码。"""
from functools import partial
from math import exp
from random import Random
from time import monotonic


def search(evaluate, initial, initial_cost, deadline, tie_key, *, seed=0):
    random = Random(seed)
    best, current = list(initial), list(initial)
    best_cost = current_cost = initial_cost
    cache = {tuple(initial): initial_cost}
    destroy_names, repair_names = ["random", "worst", "segment"], ["cheapest", "regret2"]
    weights = [[1.] * 3, [1.] * 2]
    uses = [[0] * 3, [0] * 2]
    stats = dict(evaluations=0, improvements=0, perturbations=0, time_limit_reached=False,
                 iterations=0, accepted=0, seed=seed, best_trace=[initial_cost])

    def cost(sequence):
        if monotonic() >= deadline:
            raise TimeoutError
        key = tuple(sequence)
        if key not in cache:
            cache[key] = evaluate(sequence)
            stats["evaluations"] += 1
        return cache[key]

    def better(value, sequence, old_value, old_sequence):
        return value < old_value - 1e-9 or (abs(value - old_value) <= 1e-9 and tie_key(sequence) < tie_key(old_sequence))

    temperature = max(initial_cost * .05, 1.)
    stale = 0
    try:
        for _ in range(max(30, min(150, 5 * len(initial)))):
            if len(initial) < 2:
                break
            destroy = random.choices(range(3), weights=weights[0])[0]
            repair = random.choices(range(2), weights=weights[1])[0]
            count = random.randint(1, min(len(initial), max(2, min(8, len(initial) // 3))))
            if destroy == 0:
                removed = random.sample(current, count)
            elif destroy == 1:
                savings = [(current_cost - cost([j for j in current if j != i]), i) for i in current]
                removed = [i for _, i in sorted(savings, reverse=True)[:count]]
            else:
                start = random.randrange(len(current))
                removed = [current[(start + i) % len(current)] for i in range(count)]
            candidate = [i for i in current if i not in removed]
            pending = set(removed)
            while pending:
                choices = []
                for index in sorted(pending):
                    options = []
                    for position in range(len(candidate) + 1):
                        sequence = candidate[:position] + [index] + candidate[position:]
                        options.append((cost(sequence), tie_key(sequence), sequence))
                    options.sort(key=lambda option: (option[0], option[1]))
                    regret = options[1][0] - options[0][0] if repair == 1 and len(options) > 1 else 0
                    choices.append((-regret, options[0][0], options[0][1], index, options[0][2]))
                _, _, _, index, candidate = min(choices)
                pending.remove(index)
            value = cost(candidate)
            stats["iterations"] += 1
            stats["perturbations"] += 1
            reward, stale = 0., stale + 1
            if better(value, candidate, best_cost, best):
                best, best_cost = candidate[:], value
                stats["improvements"] += 1
                stats["best_trace"].append(best_cost)
                reward, stale = 8., 0
            if value <= current_cost or random.random() < exp(min(0., (current_cost - value) / temperature)):
                reward = max(reward, 4. if value < current_cost - 1e-9 else 1.)
                current, current_cost = candidate, value
                stats["accepted"] += 1
            for kind, index in enumerate((destroy, repair)):
                uses[kind][index] += 1
                weights[kind][index] = max(.1, .8 * weights[kind][index] + .2 * reward)
            temperature = max(temperature * .97, 1e-6)
            if stale >= 30:
                break
    except TimeoutError:
        stats["time_limit_reached"] = True
    stats["operators"] = {name: dict(weight=weights[kind][i], uses=uses[kind][i])
                          for kind, names in enumerate((destroy_names, repair_names)) for i, name in enumerate(names)}
    return best, best_cost, stats


def solve(inventory, orders, capacities, layout, flag, deadline, *, seed=0):
    from solvers.warehouse_outbound_heuristic import solve as shared_solve
    return shared_solve(inventory, orders, capacities, layout, flag, deadline,
                        search_method=partial(search, seed=seed), route_strategy="ALNS")
