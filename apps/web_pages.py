# 文件说明：用 Jinja2 模板渲染各页面，静态资源在 apps/static/。
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from apps.web_jobs import MODULES, get_job_or_404
from core.paths import RUNTIME_ROOT, resource_path


PROJECT_ROOT = RUNTIME_ROOT
TEMPLATES_DIR = resource_path("apps/templates")

# 模块页右侧内嵌的结果视图：partial 模板 + 其暴露的初始化函数名（结果页也复用同一份 partial）。
RESULT_VIEWS: dict[str, tuple[str, str]] = {
    "slotting": ("partials/result_slotting.html", "initSlottingResult"),
    "picking-path": ("partials/result_picking_path.html", "initPickingPathResult"),
}

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _relative_outputs_dir(job: dict[str, Any]) -> str:
    """渲染给前端时只展示相对项目根目录的路径，避免泄漏服务器绝对路径。"""
    raw = job.get("outputs_dir") or ""
    try:
        return str(Path(raw).resolve().relative_to(PROJECT_ROOT))
    except (ValueError, OSError):
        return Path(raw).name or raw


def _static_version() -> int:
    """给 portal.css 带上按 mtime 生成的版本号，改样式后浏览器不会继续用旧缓存。"""
    try:
        return int(resource_path("apps/static/portal.css").stat().st_mtime)
    except OSError:
        return 0


def _render(template_name: str, *, title: str, active_module_key: str | None = None, **ctx: Any) -> HTMLResponse:
    template = _env.get_template(template_name)
    html = template.render(
        title=title,
        modules=MODULES,
        active_module_key=active_module_key,
        static_version=_static_version(),
        **ctx,
    )
    return HTMLResponse(html)


def render_home() -> HTMLResponse:
    return _render("home.html", title="Warehouse Optimization Portal")


def render_module_page(module_key: str) -> HTMLResponse:
    if module_key not in MODULES:
        raise HTTPException(status_code=404, detail="Unknown module")
    config = MODULES[module_key]
    ctx = _build_module_context(module_key, config)
    return _render(
        "module.html",
        title=f"{ctx['display_title']} - Warehouse Optimization Portal",
        active_module_key=module_key,
        wide_page=True,
        **ctx,
    )


def render_job_page(job_id: str) -> HTMLResponse:
    job = get_job_or_404(job_id)
    return _render(
        "job.html",
        title=f"Job {job_id}",
        active_module_key=job["module"],
        job_id=job_id,
        module_title=job["module_title"],
        module_key=job["module"],
    )




def render_result_page(job_id: str) -> HTMLResponse:
    job = get_job_or_404(job_id)
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
    outputs_dir = _relative_outputs_dir(job)
    module = job["module"]
    common = {
        "title": f"Result {job_id}",
        "active_module_key": module,
        "job_id": job_id,
        "module_title": job["module_title"],
        "outputs_dir": outputs_dir,
        # 结果 partial 由配置页和结果页共用，"返回任务页" 只在独立结果页出现。
        "result_extra_actions": f'<a class="button secondary" href="/jobs/{html.escape(job_id)}">返回任务页</a>',
    }
    if module == "slotting":
        return _render("result_slotting.html", **common)
    return _render("result_picking_path.html", **common)


def _build_module_context(module_key: str, config: dict[str, Any]) -> dict[str, Any]:
    """把原 web_pages.py 里 if/elif 大段拼字符串的逻辑搬到这里，输出 module.html 模板所需的扁平 ctx。"""
    display_title = config["title"]
    display_description = config["description"]
    field_label_overrides = {}
    file_hint = "如未上传文件，则使用默认路径:"
    form_title = "任务配置"
    backend_label = "Backend"
    time_limit_label = "时间上限 (s)"
    submit_label = "开始求解"
    reset_label = "恢复默认"
    loading_message = "Creating job..."
    error_message = "Failed to create job"
    advanced_html = """
  <div class="field">
    <label class="label" for="num_search_workers">求解线程数</label>
    <input id="num_search_workers" name="num_search_workers" type="number" min="1" value="8">
  </div>
    """
    if module_key == "slotting":
        advanced_html += """
      <div class="field">
        <label class="label" for="alpha_slots">alpha_slots</label>
        <input id="alpha_slots" name="alpha_slots" type="number" step="0.01" value="0.01">
      </div>
      <div class="field">
        <label class="label" for="alpha_proximity">alpha_proximity（SKU 拆分时的紧凑性权重，仅 OR-Tools）</label>
        <input id="alpha_proximity" name="alpha_proximity" type="number" step="0.1" min="0" value="1.0">
      </div>
      <div class="field">
        <label class="label" for="candidate_slots_per_item">候选货位数量</label>
        <input id="candidate_slots_per_item" name="candidate_slots_per_item" type="number" min="1" placeholder="Optional">
      </div>
    """
    elif module_key == "picking-path":
        display_title = "拣货路径优化"
        display_description = "配置拣货路径优化输入数据、求解后端和路径策略参数。"
        backend_label = "求解后端"
        loading_message = "正在创建拣货路径优化配置..."
        error_message = "创建配置失败"
        advanced_html += """
      <div class="field">
        <label class="label" for="start_station">起点工作站</label>
        <input id="start_station" name="start_station" type="text" value="PICK_START">
      </div>
      <div class="field">
        <label class="label" for="end_station">终点工作站</label>
        <input id="end_station" name="end_station" type="text" value="OUTBOUND">
      </div>
      <div class="field">
        <label class="label" for="mode">拣货模式</label>
        <select id="mode" name="mode">
          <option value="per-order">一单一趟</option>
          <option value="wave">波次合并（整份订单表合并成一条路径）</option>
        </select>
      </div>
    """

    if module_key in {"slotting", "picking-path"}:
        backend_options = [
            {"value": "heuristic", "label": "Heuristic"},
            {"value": "copt", "label": "COPT"},
            {"value": "ortools", "label": "OR-Tools"},
        ]
    else:
        backend_options = [{"value": "heuristic", "label": "Heuristic"}]

    file_fields = [
        {
            "name": field["name"],
            "label": field_label_overrides.get(field["name"], field["label"]),
            "default": field["default"],
        }
        for field in config["fields"]
    ]

    # 结果直接内嵌在配置页右侧。
    result_partial, result_init = RESULT_VIEWS.get(module_key, ("", ""))

    return {
        "module_key": module_key,
        "display_title": display_title,
        "display_description": display_description,
        "file_fields": file_fields,
        "file_hint": file_hint,
        "form_title": form_title,
        "backend_label": backend_label,
        "backend_options": backend_options,
        "time_limit_label": time_limit_label,
        "submit_label": submit_label,
        "reset_label": reset_label,
        "loading_message": loading_message,
        "error_message": error_message,
        "advanced_html": advanced_html,
        "result_partial": result_partial,
        "result_init": result_init,
    }
