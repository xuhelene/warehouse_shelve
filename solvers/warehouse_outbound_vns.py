"""后悔值插入初解；移动、交换、反转邻域及确定性扰动，不使用最近邻。"""
from random import Random
from time import monotonic


def search(evaluate, initial, initial_cost, deadline, tie_key, *, seed=0):
    best, best_cost = list(initial), initial_cost
    cache = {tuple(initial): initial_cost}
    stats = dict(evaluations=0, improvements=0, perturbations=0, time_limit_reached=False)

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

    try:
        # 比较插入位置对完整运输距离的影响；优先处理最佳、次佳位置差值大的任务。
        sequence, pending = [], set(initial)
        while pending:
            choices = []
            for index in sorted(pending):
                options = []
                for position in range(len(sequence) + 1):
                    candidate = sequence[:position] + [index] + sequence[position:]
                    options.append((cost(candidate), tie_key(candidate), candidate))
                options.sort(key=lambda option: (option[0], option[1]))
                regret = options[1][0] - options[0][0] if len(options) > 1 else 0
                choices.append((-regret, options[0][0], options[0][1], index, options[0][2]))
            _, _, _, index, sequence = min(choices)
            pending.remove(index)
        value = cost(sequence)
        if better(value, sequence, best_cost, best):
            best, best_cost = sequence[:], value
        current, current_cost = best[:], best_cost
        random = Random(seed)
        # 每次扰动后从移动邻域重新下降；只把更好的完整方案写入best。
        for restart in range(4):
            neighborhood = 0
            while neighborhood < 3:
                candidate_best, candidate_cost = current, current_cost
                for i in range(len(current)):
                    for j in range(len(current)):
                        if i == j or (neighborhood and i > j):
                            continue
                        candidate = current[:]
                        if neighborhood == 0:
                            candidate.insert(j, candidate.pop(i))
                        elif neighborhood == 1:
                            candidate[i], candidate[j] = candidate[j], candidate[i]
                        else:
                            candidate[i:j + 1] = reversed(candidate[i:j + 1])
                        value = cost(candidate)
                        if better(value, candidate, best_cost, best):
                            best, best_cost = candidate[:], value
                        if better(value, candidate, candidate_cost, candidate_best):
                            candidate_best, candidate_cost = candidate, value
                if better(candidate_cost, candidate_best, current_cost, current):
                    current, current_cost = candidate_best, candidate_cost
                    stats["improvements"] += 1
                    if better(current_cost, current, best_cost, best):
                        best, best_cost = current[:], current_cost
                    neighborhood = 0
                else:
                    neighborhood += 1
            if len(current) < 3 or restart == 3:
                break
            current = best[:]
            for _ in range(restart + 1):
                i, j = random.sample(range(len(current)), 2)
                current[i], current[j] = current[j], current[i]
            current_cost = cost(current)
            stats["perturbations"] += 1
    except TimeoutError:
        stats["time_limit_reached"] = True
    return best, best_cost, stats
