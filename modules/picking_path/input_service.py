"""出库页面输入服务；共用库存及订单解析，按出库类型筛选。"""
from apps.warehouse_data import load_input as _load_input


def load_input(kind: str, content: bytes | None = None, filename: str = "") -> dict:
    return _load_input(kind, content, filename, operation="出库")
