"""入库页面输入服务，保留原入口。"""
from apps.warehouse_data import load_input as _load_input


def load_input(kind: str, content: bytes | None = None, filename: str = "") -> dict:
    return _load_input(kind, content, filename, operation="入库")
