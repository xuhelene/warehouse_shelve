# 文件说明：区分只读资源根目录与可写运行根目录，让源码运行和 PyInstaller 打包版共用同一套路径逻辑。
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller onedir：资源解包在 <exe目录>/_internal，结果要落在 exe 同级目录，重装不丢。
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    RUNTIME_ROOT = Path(sys.executable).resolve().parent
else:
    RESOURCE_ROOT = Path(__file__).resolve().parents[1]
    RUNTIME_ROOT = RESOURCE_ROOT


def _ensure_writable_runtime_root(candidate: Path) -> Path:
    """exe 被放在 Program Files 之类只读目录时，退到用户目录，避免启动即崩。"""
    probe = candidate / "results"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        (probe / ".write_test").write_text("", encoding="utf-8")
        (probe / ".write_test").unlink()
        return candidate
    except OSError:
        fallback = Path.home() / "仓储优化演示"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


if FROZEN:
    RUNTIME_ROOT = _ensure_writable_runtime_root(RUNTIME_ROOT)


def resource_path(relative: str) -> Path:
    """只读资源（data/、apps/templates、apps/static 等随包发布的文件）。"""
    return (RESOURCE_ROOT / relative).resolve()


def runtime_path(relative: str) -> Path:
    """可写产物（results/ 下的任务输出、上传文件）。"""
    return (RUNTIME_ROOT / relative).resolve()


def default_input_path(relative: str) -> Path:
    """默认输入：优先用运行目录下用户自己放的同名文件，其次用随包发布的样例数据。"""
    runtime_candidate = runtime_path(relative)
    if runtime_candidate.exists():
        return runtime_candidate
    return resource_path(relative)
