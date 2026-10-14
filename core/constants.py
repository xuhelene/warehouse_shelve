import math

PALLET_L, PALLET_W = 1.2, 1.0
MAX_BOX_LAYERS_ON_PALLET = 5
PALLET_MAX_BOXES_CAP = 48

SLOT_L, SLOT_W = 7.0, 3.0
MAX_PALLET_STACK_LAYERS_IN_SLOT = 2


def max_boxes_per_layer_on_pallet(box_l: float, box_w: float) -> int:
    def fit_count(L: float, W: float, l: float, w: float) -> int:
        return int(math.floor(L / l)) * int(math.floor(W / w))

    return max(
        fit_count(PALLET_L, PALLET_W, box_l, box_w),
        fit_count(PALLET_L, PALLET_W, box_w, box_l),
    )


def boxes_per_pallet(box_l: float, box_w: float, box_h: float) -> int:
    per_layer = max_boxes_per_layer_on_pallet(box_l, box_w)
    cap = per_layer * MAX_BOX_LAYERS_ON_PALLET
    return int(min(PALLET_MAX_BOXES_CAP, cap))


def default_slot_pallet_cap() -> int:
    per_layer = int(math.floor(SLOT_L / PALLET_L)) * int(math.floor(SLOT_W / PALLET_W))
    return per_layer * MAX_PALLET_STACK_LAYERS_IN_SLOT
