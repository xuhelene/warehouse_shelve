# 文件说明：FastAPI Web 入口，负责路由注册和服务启动。
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.web_jobs import reconcile_stale_jobs_on_startup
from apps.web_pages import render_home, render_job_page, render_module_page, render_result_page
from apps.web_api import router as web_router
from core.paths import resource_path
from apps.settings_api import router as settings_router
from apps.turnover_api import router as turnover_router
from apps.warehouse_service import router as warehouse_router


app = FastAPI(title="Warehouse Optimization Portal")
app.include_router(settings_router)
app.include_router(turnover_router)
app.include_router(web_router)
app.include_router(warehouse_router)
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


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> FileResponse:
    return FileResponse(resource_path("apps/static/settings.html"))


@app.get("/sku-turnover", response_class=HTMLResponse)
def turnover_page() -> FileResponse:
    return FileResponse(resource_path("apps/static/sku-turnover.html"))


@app.get("/modules/{module_key}", response_class=HTMLResponse)
def module_page(module_key: str) -> HTMLResponse:
    if module_key == "slotting":
        return FileResponse(resource_path("apps/static/slotting.html"))
    if module_key == "picking-path":
        return FileResponse(resource_path("apps/static/picking-path.html"))
    return render_module_page(module_key)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str) -> HTMLResponse:
    return render_job_page(job_id)


@app.get("/results/{job_id}", response_class=HTMLResponse)
def result_page(job_id: str) -> HTMLResponse:
    return render_result_page(job_id)


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
