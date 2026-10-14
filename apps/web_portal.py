# 文件说明：FastAPI Web 入口，负责路由注册和服务启动。
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.web_jobs import (
    MODULES,
    get_job_or_404,
    launch_job,
    persist_upload_or_default,
    reconcile_stale_jobs_on_startup,
    register_job,
    safe_job_view,
    update_job,
)
from apps.web_pages import render_home, render_job_page, render_module_page, render_result_page
from apps.web_results import read_picking_path_result, read_slotting_layout
from core.paths import resource_path


app = FastAPI(title="Warehouse Optimization Portal")
app.mount("/static", StaticFiles(directory=str(resource_path("apps/static"))), name="static")


@app.on_event("startup")
def _cleanup_stale_jobs() -> None:
    reconciled = reconcile_stale_jobs_on_startup()
    if reconciled > 0:
        print(f"[startup] Marked {reconciled} interrupted job(s) as failed on restart.")


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return render_home()


@app.get("/warehouse-preview", response_class=HTMLResponse)
def warehouse_preview() -> FileResponse:
    return FileResponse(resource_path("apps/static/warehouse-preview.html"))


@app.get("/modules/{module_key}", response_class=HTMLResponse)
def module_page(module_key: str) -> HTMLResponse:
    return render_module_page(module_key)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str) -> HTMLResponse:
    return render_job_page(job_id)


@app.get("/results/{job_id}", response_class=HTMLResponse)
def result_page(job_id: str) -> HTMLResponse:
    return render_result_page(job_id)


@app.get("/api/modules")
def list_modules() -> JSONResponse:
    payload = []
    for key, config in MODULES.items():
        payload.append(
            {
                "key": key,
                "title": config["title"],
                "description": config["description"],
                "default_backend": config["default_backend"],
                "fields": config["fields"],
            }
        )
    return JSONResponse({"modules": payload})


@app.post("/api/jobs/slotting")
async def create_slotting_job(
    existing_inventory: Optional[UploadFile] = File(default=None),
    slots: Optional[UploadFile] = File(default=None),
    items: Optional[UploadFile] = File(default=None),
    picking_points: Optional[UploadFile] = File(default=None),
    picking_aisles: Optional[UploadFile] = File(default=None),
    backend: str = Form(default="copt"),
    time_limit: float = Form(default=60.0),
    alpha_slots: float = Form(default=0.01),
    alpha_proximity: float = Form(default=1.0),
    proximity_max_distance: float = Form(default=0.0),
    alpha_proximity_excess: float = Form(default=50.0),
    candidate_slots_per_item: Optional[int] = Form(default=None),
    num_search_workers: int = Form(default=8),
    mip_gap: float = Form(default=0.005),
) -> JSONResponse:
    params = {
        "backend": backend,
        "time_limit": time_limit,
        "alpha_slots": alpha_slots,
        "alpha_proximity": alpha_proximity,
        "proximity_max_distance": proximity_max_distance,
        "alpha_proximity_excess": alpha_proximity_excess,
        "candidate_slots_per_item": candidate_slots_per_item,
        "num_search_workers": num_search_workers,
        "mip_gap": mip_gap,
        "files": {},
    }
    job = register_job("slotting", params)
    uploads_dir = Path(job["job_dir"]) / "uploads"
    params["files"]["slots"] = await persist_upload_or_default(slots, "data/slotting/slots.csv", uploads_dir)
    params["files"]["items"] = await persist_upload_or_default(items, "data/slotting/inbound_items.csv", uploads_dir)
    params["files"]["existing_inventory"] = await persist_upload_or_default(existing_inventory, "data/slotting/existing_inventory.csv", uploads_dir)
    params["files"]["picking_points"] = await persist_upload_or_default(picking_points, "data/picking_path/picking_points.csv", uploads_dir)
    params["files"]["picking_aisles"] = await persist_upload_or_default(picking_aisles, "data/picking_path/picking_aisles.csv", uploads_dir)
    update_job(job["id"], params=params)
    launch_job(job["id"])
    return JSONResponse({"job_id": job["id"], "status": "queued"})


@app.post("/api/jobs/picking-path")
async def create_picking_path_job(
    slots: Optional[UploadFile] = File(default=None),
    order: Optional[UploadFile] = File(default=None),
    inventory: Optional[UploadFile] = File(default=None),
    aisles: Optional[UploadFile] = File(default=None),
    nodes: Optional[UploadFile] = File(default=None),
    pick_points: Optional[UploadFile] = File(default=None),
    network: Optional[UploadFile] = File(default=None),
    stations: Optional[UploadFile] = File(default=None),
    backend: str = Form(default="heuristic"),
    time_limit: float = Form(default=60.0),
    num_search_workers: int = Form(default=8),
    start_station: str = Form(default="PICK_START"),
    end_station: str = Form(default="OUTBOUND"),
    mode: str = Form(default="per-order"),
) -> JSONResponse:
    params = {
        "backend": backend,
        "time_limit": time_limit,
        "num_search_workers": num_search_workers,
        "start_station": start_station,
        "end_station": end_station,
        "mode": mode,
        "files": {},
    }
    job = register_job("picking-path", params)
    uploads_dir = Path(job["job_dir"]) / "uploads"
    params["files"]["slots"] = await persist_upload_or_default(slots, "data/slotting/slots.csv", uploads_dir)
    params["files"]["order"] = await persist_upload_or_default(order, "data/picking_path/orders_wave.csv", uploads_dir)
    params["files"]["inventory"] = await persist_upload_or_default(
        inventory, "results/slotting_solution.csv", uploads_dir
    )
    params["files"]["aisles"] = await persist_upload_or_default(aisles, "data/picking_path/picking_aisles.csv", uploads_dir)
    params["files"]["nodes"] = await persist_upload_or_default(nodes, "data/picking_path/picking_nodes.csv", uploads_dir)
    params["files"]["pick_points"] = await persist_upload_or_default(
        pick_points, "data/picking_path/picking_points.csv", uploads_dir
    )
    params["files"]["network"] = await persist_upload_or_default(network, "data/picking_path/picking_network.csv", uploads_dir)
    params["files"]["stations"] = await persist_upload_or_default(stations, "data/picking_path/picking_stations.csv", uploads_dir)
    update_job(job["id"], params=params)
    launch_job(job["id"])
    return JSONResponse({"job_id": job["id"], "status": "queued"})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    return JSONResponse(safe_job_view(get_job_or_404(job_id)))


@app.get("/api/jobs/{job_id}/slotting-layout")
def get_slotting_layout(job_id: str) -> JSONResponse:
    job = get_job_or_404(job_id)
    if job["module"] != "slotting":
        raise HTTPException(status_code=400, detail="Not a slotting job")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
    return JSONResponse(read_slotting_layout(job))


@app.get("/api/jobs/{job_id}/picking-path-result")
def get_picking_path_result(job_id: str) -> JSONResponse:
    job = get_job_or_404(job_id)
    if job["module"] != "picking-path":
        raise HTTPException(status_code=400, detail="Not a picking path job")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
    return JSONResponse(read_picking_path_result(job))


@app.get("/api/jobs/{job_id}/download/{file_key}")
def download_result(job_id: str, file_key: str):
    job = get_job_or_404(job_id)
    if file_key not in job["result_files"]:
        raise HTTPException(status_code=404, detail="Result file not found")
    path = Path(job["result_files"][file_key])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result file missing on disk")
    return FileResponse(path, filename=path.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified web portal for warehouse optimization.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("apps.web_portal:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
