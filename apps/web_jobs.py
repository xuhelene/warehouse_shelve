# 文件说明：Web 任务配置、状态管理、上传文件保存和后台求解调度。
from __future__ import annotations

import copy
import json
import shutil
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, UploadFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import (
    SolveOptions,
    SolverConfig,
)
from modules.picking_path.service import solve_picking_path
from modules.slotting.service import solve_slotting
from apps.web_results import build_picking_path_summary, build_slotting_summary, normalize_meta
from core.io_utils import to_csv_safe
from core.paths import FROZEN, default_input_path, runtime_path


MODULES: dict[str, dict[str, Any]] = {
    "slotting": {
        "title": "货位推荐",
        "description": "以最小化总入库距离为目标，为新增入库货物实时推荐货位。",
        "default_backend": "copt",
        "fields": [
            {"name": "slots", "label": "货位文件 slots.csv", "default": "data/slotting/slots.csv"},
            {"name": "existing_inventory", "label": "已有货物 existing_inventory.csv", "default": "data/slotting/existing_inventory.csv"},
            {"name": "items", "label": "新增入库 inbound_items.csv", "default": "data/slotting/inbound_items.csv"},
            {"name": "picking_points", "label": "拣货点 picking_points.csv", "default": "data/picking_path/picking_points.csv"},
            {"name": "picking_aisles", "label": "拣货通道 picking_aisles.csv", "default": "data/picking_path/picking_aisles.csv"},
        ],
    },
    "picking-path": {
        "title": "拣货路径优化",
        "description": "以最小化总拣货路程为目标，配置拣货路径优化任务。",
        "default_backend": "heuristic",
        "fields": [
            {"name": "slots", "label": "货位数据 slots.csv", "default": "data/slotting/slots.csv"},
            {"name": "order", "label": "订单数据 orders_wave.csv", "default": "data/picking_path/orders_wave.csv"},
            {"name": "inventory", "label": "库存/货位推荐结果 slotting_solution.csv", "default": "results/slotting_solution.csv"},
            {"name": "aisles", "label": "拣货通道区域 picking_aisles.csv", "default": "data/picking_path/picking_aisles.csv"},
            {"name": "nodes", "label": "拣货路径节点 picking_nodes.csv", "default": "data/picking_path/picking_nodes.csv"},
            {"name": "pick_points", "label": "货位拣货点 picking_points.csv", "default": "data/picking_path/picking_points.csv"},
            {"name": "network", "label": "仓库通道网络 picking_network.csv", "default": "data/picking_path/picking_network.csv"},
            {"name": "stations", "label": "拣货起终点 picking_stations.csv", "default": "data/picking_path/picking_stations.csv"},
        ],
    },
}

# 打包版默认后端改用 ortools：交付机器一般没有 COPT 许可，copt 仍保留在页面上可手动选择。
if FROZEN:
    MODULES["slotting"]["default_backend"] = "ortools"

RESULTS_ROOT = runtime_path("results/web_jobs")
RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
# 进程内 JOBS dict 最多保留多少条任务记录；越界时按创建时间从老到新淘汰已结束任务。
MAX_JOBS_IN_MEMORY = 200


def _snapshot_job(job_id: str) -> dict[str, Any]:
    """在锁内深拷贝出 worker 可独占的快照，避免 update_job 在 worker 读字段时改 dict。"""
    with JOBS_LOCK:
        return copy.deepcopy(JOBS[job_id])


def _evict_old_jobs() -> None:
    """超出 MAX_JOBS_IN_MEMORY 时按 created_at 从老到新淘汰已结束的任务；queued/running 永不被淘汰。"""
    with JOBS_LOCK:
        if len(JOBS) <= MAX_JOBS_IN_MEMORY:
            return
        finished_items = [
            (jid, job)
            for jid, job in JOBS.items()
            if job.get("status") not in {"queued", "running"}
        ]
        finished_items.sort(key=lambda kv: kv[1].get("created_at", ""))
        excess = len(JOBS) - MAX_JOBS_IN_MEMORY
        for jid, _ in finished_items[:excess]:
            JOBS.pop(jid, None)

def utc_now_text() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def slugify_filename(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_files_in_params(params: Any) -> Any:
    """params.files.<name>.path 是服务器绝对路径，不能回传给前端；仅保留 name/source。"""
    if not isinstance(params, dict):
        return params
    masked = dict(params)
    files = masked.get("files")
    if isinstance(files, dict):
        masked_files = {}
        for slot_name, info in files.items():
            if isinstance(info, dict):
                masked_files[slot_name] = {
                    "name": info.get("name"),
                    "source": info.get("source"),
                }
            else:
                masked_files[slot_name] = info
        masked["files"] = masked_files
    return masked


def _mask_result_files(result_files: Any) -> dict[str, str]:
    """前端只需要知道有哪些下载入口（file_key），不需要绝对路径——下载用 file_key 路由就够了。"""
    if not isinstance(result_files, dict):
        return {}
    return {key: Path(value).name for key, value in result_files.items() if value}


def safe_job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "module": job["module"],
        "module_title": job["module_title"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "error": job["error"],
        "params": _mask_files_in_params(job.get("params")),
        "result_files": _mask_result_files(job.get("result_files")),
        "meta": job["meta"],
        "summary": job["summary"],
    }


def register_job(module_key: str, params: dict[str, Any]) -> dict[str, Any]:
    if module_key not in MODULES:
        raise HTTPException(status_code=404, detail="Unknown module")

    module_title = MODULES[module_key]["title"]
    job_id = f"{module_key}-{uuid.uuid4().hex[:10]}"
    job_dir = RESULTS_ROOT / job_id
    uploads_dir = job_dir / "uploads"
    outputs_dir = job_dir / "outputs"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "id": job_id,
        "module": module_key,
        "module_title": module_title,
        "status": "queued",
        "created_at": utc_now_text(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "params": params,
        "job_dir": str(job_dir),
        "outputs_dir": str(outputs_dir),
        "result_files": {},
        "meta": {},
        "summary": {},
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    # 落盘的是服务器内部完整视图（含路径，供 lazy-load 后下载/读结果用）；
    # 对外暴露的 mask 由 FastAPI 路由层的 safe_job_view 负责。
    write_json(job_dir / "job.json", job)
    _evict_old_jobs()
    return job


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(changes)
        snapshot = copy.deepcopy(job)
    write_json(Path(job["job_dir"]) / "job.json", snapshot)
    return job


def reconcile_stale_jobs_on_startup() -> int:
    """进程重启后，曾经处于 queued/running 状态的任务背后已经没有线程，把它们落盘标记为 failed。

    返回被回收的任务数；外层可用来打印启动日志。
    """
    reconciled = 0
    for job_path in RESULTS_ROOT.glob("*/job.json"):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if job.get("status") not in {"queued", "running"}:
            continue
        job["status"] = "failed"
        job["finished_at"] = utc_now_text()
        job["error"] = "Server process restarted while this job was still queued or running."
        try:
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            reconciled += 1
        except OSError:
            continue
    return reconciled


def get_job_or_404(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        job_path = RESULTS_ROOT / job_id / "job.json"
        if not job_path.exists():
            raise HTTPException(status_code=404, detail="Job not found")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["job_dir"] = str(RESULTS_ROOT / job_id)
        job["outputs_dir"] = str(RESULTS_ROOT / job_id / "outputs")
        with JOBS_LOCK:
            JOBS[job_id] = job
    return job


def _record_job_failure(job_id: str, exc: Exception) -> None:
    snapshot = _snapshot_job(job_id)
    try:
        (Path(snapshot["job_dir"]) / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        pass
    update_job(job_id, status="failed", finished_at=utc_now_text(), error=str(exc))


async def persist_upload_or_default(
    uploaded_file: Optional[UploadFile],
    default_relative_path: str,
    destination_dir: Path,
) -> dict[str, str]:
    default_path = default_input_path(default_relative_path)
    if uploaded_file is None or not uploaded_file.filename:
        if not default_path.exists():
            raise HTTPException(status_code=400, detail=f"Default file not found: {default_relative_path}")
        return {"path": str(default_path), "source": "default", "name": default_path.name}

    target_path = destination_dir / slugify_filename(uploaded_file.filename)
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(uploaded_file.file, buffer)
    return {"path": str(target_path), "source": "upload", "name": target_path.name}


def run_slotting_job(job_id: str) -> None:
    snapshot = _snapshot_job(job_id)
    outputs_dir = Path(snapshot["outputs_dir"])
    params = snapshot["params"]
    update_job(job_id, status="running", started_at=utc_now_text())
    try:
        options = SolveOptions(
            backend=params["backend"],
            time_limit_s=params["time_limit"],
            alpha_slots=params["alpha_slots"],
            alpha_proximity=params.get("alpha_proximity", 1.0),
            proximity_max_distance=params.get("proximity_max_distance", 0.0),
            alpha_proximity_excess=params.get("alpha_proximity_excess", 50.0),
            candidate_slots_per_item=params["candidate_slots_per_item"],
            num_search_workers=params["num_search_workers"],
            mip_gap=params.get("mip_gap", 0.005),
        )
        solution, meta = solve_slotting(
            slots_path=params["files"]["slots"]["path"],
            items_virtual_path=params["files"]["items"]["path"],
            existing_inventory_path=params["files"].get("existing_inventory", {}).get("path"),
            picking_points_path=params["files"].get("picking_points", {}).get("path"),
            picking_aisles_path=params["files"].get("picking_aisles", {}).get("path"),
            cfg=SolverConfig(),
            options=options,
        )
        solution_path = outputs_dir / "slotting_solution.csv"
        to_csv_safe(solution, solution_path)
        update_job(
            job_id,
            status="completed",
            finished_at=utc_now_text(),
            result_files={"solution": str(solution_path)},
            meta=normalize_meta(meta),
            summary=build_slotting_summary(solution),
        )
    except Exception as exc:  # pragma: no cover
        _record_job_failure(job_id, exc)


def run_picking_path_job(job_id: str) -> None:
    snapshot = _snapshot_job(job_id)
    outputs_dir = Path(snapshot["outputs_dir"])
    params = snapshot["params"]
    update_job(job_id, status="running", started_at=utc_now_text())
    try:
        route, reports, meta = solve_picking_path(
            order_path=params["files"]["order"]["path"],
            inventory_path=params["files"]["inventory"]["path"],
            nodes_path=params["files"]["nodes"]["path"],
            network_path=params["files"]["network"]["path"],
            stations_path=params["files"]["stations"]["path"],
            pick_points_path=params["files"]["pick_points"]["path"],
            backend=params["backend"],
            mode=params.get("mode", "per-order"),
            start_station=params["start_station"],
            end_station=params["end_station"],
            time_limit_s=params["time_limit"],
            num_search_workers=params["num_search_workers"],
        )
        route_path = outputs_dir / "picking_route.csv"
        order_summary_path = outputs_dir / "picking_order_summary.csv"
        unfulfilled_path = outputs_dir / "picking_unfulfilled.csv"
        to_csv_safe(route, route_path)
        to_csv_safe(reports["order_summary"], order_summary_path)
        to_csv_safe(reports["unfulfilled"], unfulfilled_path)
        update_job(
            job_id,
            status="completed",
            finished_at=utc_now_text(),
            result_files={
                "route": str(route_path),
                "order_summary": str(order_summary_path),
                "unfulfilled": str(unfulfilled_path),
            },
            meta=normalize_meta(meta),
            summary=build_picking_path_summary(route, reports),
        )
    except Exception as exc:  # pragma: no cover
        _record_job_failure(job_id, exc)


def launch_job(job_id: str) -> None:
    job = get_job_or_404(job_id)
    if job["module"] == "slotting":
        target = run_slotting_job
    else:
        target = run_picking_path_job
    thread = threading.Thread(target=target, args=(job_id,), daemon=True)
    thread.start()
