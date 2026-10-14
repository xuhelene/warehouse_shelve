from __future__ import annotations

import math
import time
from typing import Dict, Tuple
import pandas as pd


def solve_with_copt(
    slots: pd.DataFrame,
    items: pd.DataFrame,
    cand: pd.DataFrame,
    time_limit_s: float = 60.0,
    alpha_slots: float = 0.01,
    use_demand_if_present: bool = True,
    use_symmetry_break: bool = True,
    mip_gap: float = 0.005,
) -> Tuple[pd.DataFrame, Dict]:
    from solvers.copt_runtime import create_env, load_copt

    cp, COPT = load_copt()
    env = create_env(cp)
    m = env.createModel("slotting_copt")
    m.setParam(COPT.Param.TimeLimit, float(time_limit_s))
    # 目标曲面平坦，最后千分之几的 gap 要花掉绝大部分时间，默认容忍 0.5%。
    if mip_gap > 0:
        m.setParam(COPT.Param.RelGap, float(mip_gap))

    demand_available = use_demand_if_present and ("monthly_outbound_boxes" in items.columns)

    # ---- 参数字典：把 DataFrame 转成 (id -> value) 便于建模时 O(1) 查表 ----
    qty_map = items.set_index("item_id")["qty_boxes"].astype(int).to_dict()               # item_id -> 待上架箱数 qty_i
    bpp_map = items.set_index("item_id")["boxes_per_pallet"].astype(int).to_dict()        # item_id -> 每托盘可放箱数 bpp_i
    pallet_cap_map = slots.set_index("slot_id")["pallet_cap"].astype(int).to_dict()       # slot_id -> 该货位可放托盘数 cap_p_s
    slot_cost_map = slots.set_index("slot_id")["slot_cost"].astype(float).to_dict()       # slot_id -> 单位访问成本 c_s
    # item_id -> 月出库频次 d_i；若数据缺失或用户关闭需求加权，则全部退化为 1（等权）
    demand_map = items.set_index("item_id")["monthly_outbound_boxes"].astype(float).to_dict() if demand_available else {iid: 1.0 for iid in items["item_id"]}
    # item_id -> 该 item 一共需要多少托盘 P_i = ⌈qty_i / bpp_i⌉；用于收紧 big-M（见下）
    need_pallets_map = {
        iid: int(math.ceil(int(qty_map[iid]) / max(int(bpp_map[iid]), 1))) for iid in qty_map
    }

    # ---- 决策变量：按 (item, slot) 候选对逐个建立 ----
    # y[i,s] ∈ {0,1}：物品 i 是否分配到货位 s（是否"开启"该 item-slot 对）
    # q[i,s] ∈ Z≥0 ：物品 i 放入货位 s 的箱数，上界 = min(该 item 总箱数, 货位箱容量)
    # p[i,s] ∈ Z≥0 ：物品 i 在货位 s 占用的托盘数，上界 = min(货位托盘容量, 该 item 需要的托盘数)
    y, q, p = {}, {}, {}
    for r in cand.itertuples(index=False):
        key = (r.item_id, str(r.slot_id))
        cap_boxes = int(r.cap_boxes)     # 该货位对该 item 的箱容量 = bpp_i * cap_p_s
        # 托盘数上界收紧为 min(货位托盘容量, 该 item 总共需要的托盘数)：
        # item 一共只要 P_i 个托盘，在任何单个货位上都不可能超过 P_i。
        cap_p = min(int(r.pallet_cap), int(need_pallets_map[r.item_id]))
        q_ub = min(int(qty_map[r.item_id]), cap_boxes)
        y[key] = m.addVar(vtype=COPT.BINARY, name=f"y[{r.item_id},{r.slot_id}]")
        q[key] = m.addVar(vtype=COPT.INTEGER, lb=0, ub=q_ub, name=f"q[{r.item_id},{r.slot_id}]")
        p[key] = m.addVar(vtype=COPT.INTEGER, lb=0, ub=cap_p, name=f"p[{r.item_id},{r.slot_id}]")

    # ---- 约束 1：每个货位最多容纳一个 item（即 sku||batch 组合） ----
    # ∀s: Σ_i y[i,s] ≤ 1，保证货位不被多个 item 混用
    for slot_id, sub in cand.groupby("slot_id"):
        sid = str(slot_id)
        m.addConstr(cp.quicksum(y[(iid, sid)] for iid in sub["item_id"].tolist()) <= 1)

    # ---- 约束 2：每个 item 的箱数必须刚好被完整安置 ----
    # ∀i: Σ_s q[i,s] = qty_i，全部上架、不多不少
    # 同时：只需要 1 个货位的 item（P_i ≤ 它所有候选货位里最小的托盘容量，说明任何一个
    # 候选都装得下整件）直接强制 Σ_s y[i,s] = 1。拆开放永远不会更优（多付 alpha_slots、
    # 还要占更贵的第二个货位），这条约束砍掉大量"同一 item 在多货位间摊开"的对称解。
    min_cap_by_item = cand.groupby("item_id")["pallet_cap"].min().to_dict()
    single_slot_items = {
        str(item_id)
        for item_id in items["item_id"].astype(str)
        if int(need_pallets_map[item_id]) <= int(min_cap_by_item.get(item_id, 0))
    }
    for item_id, sub in cand.groupby("item_id"):
        keys = [(item_id, str(sid)) for sid in sub["slot_id"].tolist()]
        m.addConstr(cp.quicksum(q[k] for k in keys) == int(qty_map[item_id]))
        if str(item_id) in single_slot_items:
            m.addConstr(cp.quicksum(y[k] for k in keys) == 1)

    # ---- 约束 3：单个 (item, slot) 上 y / q / p 之间的耦合与容量关系 ----
    for r in cand.itertuples(index=False):
        key = (r.item_id, str(r.slot_id))
        bpp = int(bpp_map[r.item_id])
        # big-M 用 min(货位托盘容量, 该 item 需要的托盘数 P_i)，而不是恒取货位容量。
        # 原来 y ≥ p / cap_p_s（cap_p_s=30）允许 LP 把一个货位摊给多个 item，
        # 而整数解里一个货位只放一个 item，松弛界因此偏低 5% 以上；收紧后 LP 界基本贴合最优。
        cap_p = min(int(pallet_cap_map[str(r.slot_id)]), int(need_pallets_map[r.item_id]))
        cap_boxes = min(int(r.cap_boxes), int(qty_map[r.item_id]))
        # 3a) p[i,s] ≤ min(cap_p_s, P_i) * y[i,s]：未开启该 item-slot 对时不能占用托盘位
        m.addConstr(p[key] <= cap_p * y[key])
        # 3b) p[i,s] ≥ y[i,s]：只要开启该 item-slot 对，就至少占用 1 个托盘位（避免"占位不放货"的退化解）
        m.addConstr(p[key] >= y[key])
        # 3c) q[i,s] ≤ bpp_i * p[i,s]：箱数不能超过"托盘数 × 每托盘箱数"，即托盘装载物理上限
        m.addConstr(q[key] <= bpp * p[key])
        # 3d) q[i,s] ≤ min(cap_boxes, qty_i) * y[i,s]：箱数上界同样乘 y 挂到开关变量上，
        #     未开启时强制 q=0，比原来不带 y 的裸上界多提供一条 linking 不等式
        m.addConstr(q[key] <= cap_boxes * y[key])

    # ---- 约束 4（有效不等式 / valid inequality）：每个 item 的总托盘数下界 ----
    # ∀i: Σ_s p[i,s] ≥ ⌈qty_i / bpp_i⌉
    # 该式在整数解下由约束 2 + 3c 隐含成立，但可显著收紧 LP 松弛的对偶界；
    # demo 数据上把求解时间从 >60s（触发 TimeLimit）压到约 22s。
    for item_id, sub in cand.groupby("item_id"):
        bpp = int(bpp_map[item_id])
        pallets_needed = math.ceil(int(qty_map[item_id]) / bpp)
        keys = [(item_id, str(sid)) for sid in sub["slot_id"].tolist()]
        m.addConstr(cp.quicksum(p[k] for k in keys) >= pallets_needed)

    # ---- 约束 5（对称性破除 / symmetry breaking，可选）：等价槽位强制字典序使用 ----
    # 将 (slot_cost, pallet_cap) 完全相同的槽位归为一个等价类，在类内按 slot_id 字典序排序。
    # 对每对相邻的等价槽 (s_j, s_{j+1})：Σ_i y[i, s_{j+1}] ≤ Σ_i y[i, s_j]
    # 含义：s_{j+1} 只能在 s_j 已经被某个 item 占用之后才可用，从而消除 S_n 的槽位置换对称。
    # 本数据集下 4 条巷道（2C1~2C4）存在大量镜像等价槽（同 cost + 同 pallet_cap），
    # demo slots.csv 共 119 槽被压到 ~34 个等价类，每类 3~4 槽，符号对称严重。
    sym_pairs_added = 0
    if use_symmetry_break:
        # 分组：等价类 key = (slot_cost, pallet_cap)；类内按 slot_id 字典序
        slots_by_class: Dict[Tuple[float, int], list] = {}
        for r in slots.itertuples(index=False):
            key = (float(r.slot_cost), int(r.pallet_cap))
            slots_by_class.setdefault(key, []).append(str(r.slot_id))
        # 对每个含 item 候选的 slot 建 used[s] = Σ_i y[i, s]
        y_by_slot: Dict[str, list] = {}
        for (item_id, slot_id), yvar in y.items():
            y_by_slot.setdefault(slot_id, []).append(yvar)
        for key, sid_list in slots_by_class.items():
            sid_list_sorted = sorted(sid_list)
            for j in range(len(sid_list_sorted) - 1):
                s_prev, s_next = sid_list_sorted[j], sid_list_sorted[j + 1]
                # 只有当两个槽都进入模型（有候选 item）才添加，否则约束退化为 0≤0 或单边冗余
                if s_prev in y_by_slot and s_next in y_by_slot:
                    m.addConstr(
                        cp.quicksum(y_by_slot[s_next]) <= cp.quicksum(y_by_slot[s_prev])
                    )
                    sym_pairs_added += 1

    # ---- 目标函数：最小化 (货位访问成本 × 出库频率 × 托盘占用) + 开启货位数量惩罚 ----
    # min Σ_(i,s) [ c_s * d_i * p[i,s] + alpha_slots * y[i,s] ]
    #   - 主项：高访问成本货位少占托盘，且高频出库 item 优先占低成本货位（ABC 分区效应）
    #   - alpha_slots：对每个被开启的 (item, slot) 对加一个小惩罚，避免同 item 无谓地分裂到多货位
    obj_terms = []
    for (item_id, slot_id), yvar in y.items():
        coef = float(slot_cost_map[slot_id]) * float(demand_map[item_id])
        obj_terms.append(coef * p[(item_id, slot_id)] + float(alpha_slots) * yvar)
    m.setObjective(cp.quicksum(obj_terms), sense=COPT.MINIMIZE)

    solve_start = time.perf_counter()
    m.solve()
    elapsed_s = time.perf_counter() - solve_start

    print(f"m.status: {m.status}")
    if m.status not in (COPT.OPTIMAL, COPT.TIMEOUT):
        raise RuntimeError(f"COPT ended with status={m.status}")

    try:
        _ = m.objval
    except Exception as exc:
        raise RuntimeError(f"COPT stopped with status={m.status}, but no readable feasible solution was found.") from exc

    out_rows = []
    for (item_id, slot_id), yvar in y.items():
        if yvar.x > 0.5:
            out_rows.append((
                item_id,
                slot_id,
                int(round(q[(item_id, slot_id)].x)),
                int(round(p[(item_id, slot_id)].x)),
                float(slot_cost_map[slot_id]),
            ))

    sol = pd.DataFrame(out_rows, columns=["item_id", "slot_id", "qty_boxes_in_slot", "pallets_used_in_slot", "slot_cost"])

    meta = {
        "backend": "copt",
        "status": getattr(m, "status", None),
        "solve_time_s": elapsed_s,
        "use_symmetry_break": use_symmetry_break,
        "mip_gap_limit": float(mip_gap),
        "single_slot_items": len(single_slot_items),
        "symmetry_pairs_added": sym_pairs_added,
    }
    for attr in ["objval", "bestbnd", "mipgap"]:
        if hasattr(m, attr):
            meta[attr] = getattr(m, attr)
    return sol, meta
