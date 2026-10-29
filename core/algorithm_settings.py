"""算法配置持久化；新立体库规划当前仅支持启发式。"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.paths import runtime_path


class SolverSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    backend: Literal["copt", "ortools", "heuristic"] = "copt"
    time_limit_s: float = Field(default=60, gt=0)
    threads: int = Field(default=8, gt=0, strict=True)
    mip_gap: float = Field(default=0.005, ge=0, le=1)


class SettingsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=0, ge=0)
    updated_at: str | None = None
    slotting: SolverSettings = Field(default_factory=SolverSettings)
    picking_path: SolverSettings = Field(default_factory=SolverSettings)


_lock = threading.Lock()


def _read(path: Path) -> SettingsDocument:
    if not path.exists():
        return SettingsDocument()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or {"version", "updated_at", "slotting", "picking_path"} - raw.keys():
        raise ValueError("配置文件缺少必要字段")
    return SettingsDocument.model_validate(raw)


def read_settings(path: Path | None = None) -> dict:
    path = path or runtime_path("config/algorithm_config.json")
    with _lock:
        return {**_read(path).model_dump(), "persisted": path.exists(), "planning_connected": True}


def save_settings(module: str, values: SolverSettings, path: Path | None = None) -> dict:
    if module not in {"slotting", "picking_path"}:
        raise ValueError("未知算法模块")
    path = path or runtime_path("config/algorithm_config.json")
    with _lock:
        document = _read(path)
        setattr(document, module, SolverSettings.model_validate(values.model_dump()))
        document.version += 1
        document.updated_at = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(document.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {**document.model_dump(), "persisted": True, "planning_connected": True}
