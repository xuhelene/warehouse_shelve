from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FixedAssignment:
    sku_id: str
    batch_id: str
    slot_id: str


@dataclass
class ForbiddenSlot:
    sku_id: str
    batch_id: str
    slot_id: str


@dataclass
class SolverConfig:
    fixed: List[FixedAssignment] = field(default_factory=list)
    forbidden: List[ForbiddenSlot] = field(default_factory=list)


@dataclass
class SolveOptions:
    backend: str = "copt"  # heuristic | copt | ortools
    time_limit_s: float = 60.0
    alpha_slots: float = 0.01
    use_demand_if_present: bool = True
    candidate_slots_per_item: Optional[int] = None
    num_search_workers: int = 8
    # 精确后端（copt / ortools）的相对 gap 容忍度；0 表示必须证明到最优。
    # 货位模型的目标曲面非常平坦，把最后千分之几的 gap 证完要花掉绝大部分时间，
    # 默认 0.5% 即可在秒级拿到与最优基本重合的解。
    mip_gap: float = 0.005
    # SKU 拆分到多个 slot 时，按拣货绕行距离惩罚。仅 ortools 后端使用；0 表示关闭。
    # alpha_proximity 是"每米都罚"的线性权重；proximity_max_distance 是弹性上限 K，
    # 只惩罚超出 K 的部分（权重 alpha_proximity_excess）。K 写成弹性而不是硬约束，
    # 是因为货位紧张时硬上限会让问题直接不可行——超限要的是"尽量近"，不是报错。
    alpha_proximity: float = 1.0
    proximity_max_distance: float = 0.0
    alpha_proximity_excess: float = 50.0


