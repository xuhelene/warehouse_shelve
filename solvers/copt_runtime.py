# 文件说明：统一 COPT 的导入与环境创建，缺许可/缺依赖时给出面向使用者的中文提示。
from __future__ import annotations

_HINT = "COPT 后端不可用：{reason}。本机未安装 COPT 或没有有效许可，请在页面上改选 OR-Tools 或 Heuristic 后端后重试。"


def load_copt():
    """返回 (coptpy, COPT) 两个模块对象。"""
    try:
        import coptpy as cp
        from coptpy import COPT
    except Exception as exc:  # 打包版通常是 ImportError，装了但没许可则可能是别的异常
        raise RuntimeError(_HINT.format(reason=exc)) from exc
    return cp, COPT


def create_env(cp):
    """创建 COPT Envr，许可校验失败时抛出同样的中文提示。"""
    try:
        return cp.Envr()
    except Exception as exc:
        raise RuntimeError(_HINT.format(reason=exc)) from exc
