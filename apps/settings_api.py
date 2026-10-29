"""设置 HTTP 接口，不修改现有规划任务参数。"""
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from core.algorithm_settings import SolverSettings, read_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings():
    try:
        return read_settings()
    except (OSError, ValueError, ValidationError) as error:
        raise HTTPException(500, "读取配置失败，请检查配置文件，未回退覆盖现有配置。") from error


@router.put("/{module}")
def update_settings(module: str, values: SolverSettings):
    if module not in {"slotting", "picking_path"}:
        raise HTTPException(404, "未知算法模块")
    try:
        return save_settings(module, values)
    except (OSError, ValueError, ValidationError) as error:
        raise HTTPException(500, "保存配置失败，未替换现有配置。") from error
