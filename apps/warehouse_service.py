"""统一仓储入口：HTTP接收、数据获取、任务分发和结果保存。"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import re
from uuid import uuid4
from zipfile import BadZipFile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from apps.warehouse_data import load_input, load_specs, number, prepare
from core.algorithm_settings import read_settings
from core.paths import resource_path, runtime_path
from core.warehouse_layout import WarehouseLayout
from modules.slotting import planning_service as inbound_service
from modules.picking_path import planning_service as outbound_service

router = APIRouter(tags=["warehouse-planning"])


def get_inventory_snapshot(request: dict) -> list[dict]:
    """平台库存GET调用边界；联调时在此完成请求和响应标准化。"""
    raise NotImplementedError("远程库存GET尚未联调，请使用文件快照模式")


def get_sku_master_data(sku_ids: list[str], request: dict) -> list[dict]:
    """平台SKU GET调用边界；request保留仓库、货主等调用上下文。"""
    raise NotImplementedError("远程SKU GET尚未联调，请使用文件规格数据")


async def _load_input(module, kind, content=None, filename=""):
    operation = {"slotting": "入库", "picking-path": "出库"}.get(module)
    if operation is None:
        raise HTTPException(status_code=404, detail="未知任务")
    try:
        return await run_in_threadpool(load_input, kind, content, filename, operation=operation)
    except (ValueError, OSError, BadZipFile) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/{module}/inputs/{kind}/sample")
async def sample_input(module: str, kind: str):
    return await _load_input(module, kind)


@router.post("/api/{module}/inputs/{kind}")
async def upload_input(module: str, kind: str, file: UploadFile = File(...)):
    return await _load_input(module, kind, await file.read(), file.filename or "")


@router.post("/api/warehouse/specs")
async def upload_specs(file: UploadFile = File(...)):
    try:
        return await run_in_threadpool(load_specs, await file.read(), file.filename or "")
    except (ValueError, OSError, BadZipFile) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/api/slotting/plan")
async def plan_inbound(payload: dict):
    return await _plan("slotting", payload)


@router.post("/api/picking-path/plan")
async def plan_outbound(payload: dict):
    return await _plan("picking_path", payload)


async def _plan(operation, payload):
    source = payload.get("source", "file_snapshot")
    if source not in {"file_snapshot", "platform"}:
        raise HTTPException(status_code=422, detail="未知数据来源")
    if not isinstance(payload.get("orders"), list) or (source == "file_snapshot" and not isinstance(payload.get("inventory"), list)):
        raise HTTPException(status_code=422, detail="请加载本次库存快照和订单")
    if any(not isinstance(row, dict) for row in payload["orders"]) or (source == "file_snapshot" and any(not isinstance(row, dict) for row in payload["inventory"])):
        raise HTTPException(status_code=422, detail="库存和订单明细格式无效")
    if payload.get("specs") is not None and (not isinstance(payload["specs"], list) or any(not isinstance(row, dict) for row in payload["specs"])):
        raise HTTPException(status_code=422, detail="SKU规格格式无效")
    try:
        return await run_in_threadpool(plan, operation, payload)
    except (ValueError, OSError, BadZipFile) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/warehouse/runs/{run_id}")
def read_run(run_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    path = runtime_path(f"results/warehouse/{run_id}/result.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/warehouse-results/{run_id}")
def result_page(run_id: str):
    read_run(run_id)
    return FileResponse(resource_path("apps/static/warehouse-result.html"))


def plan(operation, payload, *, output_root=None, settings=None):
    if operation not in {"slotting", "picking_path"}:
        raise ValueError("未知任务")
    settings = settings or read_settings()
    config = settings[operation]
    run_id = uuid4().hex
    folder = (output_root or runtime_path("results/warehouse")) / run_id
    folder.mkdir(parents=True)
    request = deepcopy(payload)
    request.update(operation=operation, settings=settings)
    (folder / "input.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    result = dict(run_id=run_id, operation=operation, status="failed", backend=config["backend"],
                  settings=settings, source=payload.get("source", "file_snapshot"), started_at=datetime.now(timezone.utc).isoformat(),
                  shortages=[], unassigned=[], warnings=[], layer_weight=payload.get("layer_weight", 1))
    try:
        if config["backend"] != "heuristic":
            raise ValueError("本轮立体库规则仅支持启发式，请在算法设置中选择启发式后规划")
        source = payload.get("source", "file_snapshot")
        if source == "platform":
            # 正式请求不接受调用方的旧快照；每次都从统一入口获取新数据。
            payload = deepcopy(payload)
            payload["inventory"] = get_inventory_snapshot(payload)
            sku_ids = sorted({row["sku_id"] for row in payload["inventory"] + payload["orders"]})
            payload["specs"] = get_sku_master_data(sku_ids, payload)
            request.update(payload)
            (folder / "input.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        elif source != "file_snapshot":
            raise ValueError("未知数据来源")
        weight = float(number(payload.get("layer_weight", 1), "层间距离权重"))
        if not math.isfinite(weight):
            raise ValueError("层间距离权重数值过大")
        layout = WarehouseLayout(weight)
        specs = payload.get("specs")
        if specs is None:
            specs = load_specs()["rows"]
        inventory, orders, capacities = prepare(payload.get("inventory", []), payload.get("orders", []), specs, layout, operation)
        task_service = inbound_service if operation == "slotting" else outbound_service
        result.update(task_service.plan(inventory, orders, capacities, layout, payload, config))
        result["warnings"].append("启发式为单线程；threads及mip_gap不生效，结果不保证全局最优。")
        result["layout"] = dict(slots=list(layout.slots.values()), nodes=layout.nodes,
                                edges=[dict(start=a, end=b, distance=d) for a, targets in layout.graph.items() for b, d in targets.items() if a < b])
    except (ValueError, TimeoutError, OSError, BadZipFile, NotImplementedError) as error:
        result.update(status="failed", message=str(error))
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    # Decimal保留为十进制字符串，避免把1瓶回写为1.002瓶。
    text = json.dumps(result, ensure_ascii=False, indent=2, default=lambda x: str(x) if isinstance(x, Decimal) else x)
    (folder / "result.json").write_text(text, encoding="utf-8")
    return json.loads(text)
