from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Slotting Viewer</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 251, 245, 0.88);
      --ink: #1f2a2e;
      --muted: #6f7a73;
      --line: rgba(31, 42, 46, 0.12);
      --accent: #b74f2a;
      --empty: #d9d2c7;
      --shadow: 0 18px 40px rgba(67, 47, 28, 0.12);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(183, 79, 42, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(46, 125, 91, 0.15), transparent 25%),
        linear-gradient(180deg, #fbf7f1 0%, var(--bg) 100%);
      min-height: 100vh;
    }

    .shell {
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: 100vh;
      transition: grid-template-columns 180ms ease;
    }

    .shell.sidebar-collapsed {
      grid-template-columns: 0 1fr;
    }

    .sidebar {
      padding: 28px 22px;
      background: rgba(255, 250, 243, 0.76);
      border-right: 1px solid var(--line);
      backdrop-filter: blur(14px);
      overflow: hidden;
      transition: opacity 180ms ease, transform 180ms ease, padding 180ms ease, border-color 180ms ease;
    }

    .shell.sidebar-collapsed .sidebar {
      opacity: 0;
      transform: translateX(-16px);
      padding-left: 0;
      padding-right: 0;
      border-color: transparent;
    }

    .main {
      padding: 24px;
      overflow: auto;
    }

    .eyebrow {
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(183, 79, 42, 0.1);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    h1 {
      margin: 14px 0 10px;
      font-size: 32px;
      line-height: 1.05;
    }

    .sub {
      margin: 0 0 22px;
      color: var(--muted);
      line-height: 1.6;
      font-size: 14px;
    }

    .section {
      margin-top: 24px;
    }

    .label {
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-weight: 700;
    }

    select, input {
      width: 100%;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.9);
      color: var(--ink);
      font-size: 14px;
      outline: none;
    }

    .stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 16px;
    }

    .stat {
      padding: 14px;
      border-radius: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }

    .stat .k {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .stat .v {
      font-size: 24px;
      font-weight: 700;
      line-height: 1;
    }

    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
      font-size: 12px;
      color: var(--muted);
    }

    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .swatch {
      width: 12px;
      height: 12px;
      border-radius: 999px;
      border: 1px solid rgba(0, 0, 0, 0.08);
    }

    .toolbar {
      display: flex;
      gap: 14px;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }

    .toolbar-title {
      font-size: 22px;
      font-weight: 700;
    }

    .toolbar-meta {
      color: var(--muted);
      font-size: 14px;
    }

    .toolbar-actions {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    .tool-btn {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: var(--shadow);
    }

    .toggle-btn {
      background: rgba(183, 79, 42, 0.1);
      color: var(--accent);
    }

    .zoom-badge {
      min-width: 72px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    .canvas-wrap {
      position: relative;
      padding: 18px;
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.86), rgba(255, 248, 238, 0.78)),
        repeating-linear-gradient(
          90deg,
          rgba(31, 42, 46, 0.025) 0,
          rgba(31, 42, 46, 0.025) 1px,
          transparent 1px,
          transparent 80px
        );
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      min-height: 700px;
      overflow: hidden;
    }

    .viewport {
      position: relative;
      min-height: 640px;
      overflow: hidden;
      border-radius: 20px;
      cursor: grab;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0.08));
      touch-action: none;
    }

    .viewport.dragging {
      cursor: grabbing;
    }

    .scene {
      position: relative;
      transform-origin: 0 0;
      will-change: transform;
    }

    .warehouse {
      position: relative;
      min-height: 640px;
      min-width: 900px;
      border-radius: 20px;
    }

    .slot {
      position: absolute;
      border-radius: 16px;
      border: 1px solid rgba(31, 42, 46, 0.12);
      background: var(--empty);
      color: var(--ink);
      padding: 10px;
      overflow: hidden;
      transition: transform 120ms ease, box-shadow 120ms ease, opacity 120ms ease;
      cursor: pointer;
    }

    .slot.occupied {
      box-shadow: 0 10px 20px rgba(47, 34, 20, 0.12);
    }

    .slot.match {
      outline: 3px solid rgba(183, 79, 42, 0.32);
      outline-offset: 2px;
    }

    .slot:hover {
      transform: translateY(-2px);
      box-shadow: 0 16px 30px rgba(47, 34, 20, 0.18);
      z-index: 3;
    }

    .slot-id {
      font-size: 11px;
      color: rgba(31, 42, 46, 0.72);
      margin-bottom: 6px;
      font-weight: 700;
      letter-spacing: 0.04em;
    }

    .slot-sku {
      font-size: 16px;
      font-weight: 700;
      line-height: 1.1;
      margin-bottom: 5px;
    }

    .slot-batch, .slot-qty {
      font-size: 12px;
      color: rgba(31, 42, 46, 0.78);
      line-height: 1.25;
    }

    .slot.empty .slot-sku, .slot.empty .slot-batch, .slot.empty .slot-qty {
      color: rgba(31, 42, 46, 0.4);
    }

    .details {
      margin-top: 18px;
      padding: 16px;
      border-radius: 22px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      min-height: 210px;
    }

    .details h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }

    .details-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 14px;
      font-size: 14px;
    }

    .details-grid div {
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.7);
      border: 1px solid rgba(31, 42, 46, 0.08);
    }

    .details-grid strong {
      display: block;
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    .note {
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .error {
      color: #9c2c18;
      background: rgba(183, 79, 42, 0.09);
      border: 1px solid rgba(183, 79, 42, 0.18);
      border-radius: 16px;
      padding: 12px 14px;
      margin-bottom: 14px;
    }

    @media (max-width: 1100px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { border-right: none; border-bottom: 1px solid var(--line); }
      .warehouse { min-width: 700px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <span class="eyebrow">Warehouse UI</span>
      <h1>货位推荐可视化</h1>
      <p class="sub">按 <code>slots.csv</code> 中的 <code>x / y</code> 坐标展开货位布局，直接查看每个货位当前分配到的 SKU、批次、箱数和托盘数。</p>

      <div class="section">
        <label class="label" for="solutionSelect">结果文件</label>
        <select id="solutionSelect"></select>
      </div>

      <div class="section">
        <label class="label" for="searchInput">搜索</label>
        <input id="searchInput" type="text" placeholder="输入 slot_id / sku_id / batch_id">
      </div>

      <div class="stats">
        <div class="stat"><div class="k">总货位</div><div class="v" id="statTotalSlots">-</div></div>
        <div class="stat"><div class="k">已分配</div><div class="v" id="statOccupiedSlots">-</div></div>
        <div class="stat"><div class="k">占用率</div><div class="v" id="statOccupancyRate">-</div></div>
        <div class="stat"><div class="k">SKU 数</div><div class="v" id="statSkuCount">-</div></div>
      </div>

      <div class="legend" id="legend"></div>

      <div class="details" id="details">
        <h2>货位详情</h2>
        <div class="note">点击右侧货位卡片查看详细信息。</div>
      </div>
    </aside>

    <main class="main">
      <div id="errorBox"></div>
      <div class="toolbar">
        <div>
          <div class="toolbar-title">仓库平面视图</div>
          <div class="toolbar-meta" id="toolbarMeta">等待加载结果</div>
        </div>
        <div class="toolbar-actions">
          <button class="tool-btn toggle-btn" id="toggleSidebarBtn" type="button">收起侧栏</button>
          <button class="tool-btn" id="zoomOutBtn" type="button">缩小</button>
          <div class="zoom-badge" id="zoomBadge">100%</div>
          <button class="tool-btn" id="zoomInBtn" type="button">放大</button>
          <button class="tool-btn" id="resetViewBtn" type="button">重置视图</button>
          <button class="tool-btn" id="exportBtn" type="button">导出截图</button>
        </div>
      </div>

      <div class="canvas-wrap">
        <div class="viewport" id="viewport">
          <div class="scene" id="scene">
            <div class="warehouse" id="warehouse"></div>
          </div>
        </div>
      </div>
    </main>
  </div>

  <script>
    const emptySlotColor = "#d9d2c7";
    const fullSlotColor = "#d96c4d";
    const availableSlotColor = "#4c9e77";
    const state = {
      solutions: [],
      currentData: null,
      selectedSlotId: null,
      view: { scale: 1, offsetX: 20, offsetY: 20 },
      drag: { active: false, startX: 0, startY: 0, baseX: 0, baseY: 0 }
    };

    const solutionSelect = document.getElementById("solutionSelect");
    const searchInput = document.getElementById("searchInput");
    const shell = document.querySelector(".shell");
    const warehouse = document.getElementById("warehouse");
    const viewport = document.getElementById("viewport");
    const scene = document.getElementById("scene");
    const details = document.getElementById("details");
    const errorBox = document.getElementById("errorBox");
    const zoomBadge = document.getElementById("zoomBadge");
    const toggleSidebarBtn = document.getElementById("toggleSidebarBtn");

    function clamp(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function escapeXml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&apos;");
    }

    function applyViewTransform() {
      scene.style.transform = `translate(${state.view.offsetX}px, ${state.view.offsetY}px) scale(${state.view.scale})`;
      zoomBadge.textContent = `${Math.round(state.view.scale * 100)}%`;
    }

    function resetView() {
      state.view.scale = 1;
      state.view.offsetX = 20;
      state.view.offsetY = 20;
      applyViewTransform();
    }

    function zoomAt(nextScale, originX, originY) {
      const prevScale = state.view.scale;
      const scale = clamp(nextScale, 0.35, 3.5);
      const worldX = (originX - state.view.offsetX) / prevScale;
      const worldY = (originY - state.view.offsetY) / prevScale;
      state.view.scale = scale;
      state.view.offsetX = originX - worldX * scale;
      state.view.offsetY = originY - worldY * scale;
      applyViewTransform();
    }

    async function fetchJson(url) {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      return response.json();
    }

    function renderError(message) {
      errorBox.innerHTML = message ? `<div class="error">${message}</div>` : "";
    }

    function slotColor(slot) {
      const allocated = slot.allocation;
      if (!allocated) return emptySlotColor;
      const assignedBoxes = Number(allocated.qty_boxes_in_slot);
      const boxesPerPallet = Number(allocated.boxes_per_pallet);
      const palletCap = Number(slot.pallet_cap);
      const capacityBoxes = boxesPerPallet * palletCap;
      const isFull = Number.isFinite(capacityBoxes) && capacityBoxes > 0 && assignedBoxes >= capacityBoxes;
      return isFull ? fullSlotColor : availableSlotColor;
    }

    function updateStats(summary) {
      document.getElementById("statTotalSlots").textContent = summary.total_slots;
      document.getElementById("statOccupiedSlots").textContent = summary.occupied_slots;
      document.getElementById("statOccupancyRate").textContent = `${summary.occupancy_rate}%`;
      document.getElementById("statSkuCount").textContent = summary.distinct_skus;
      document.getElementById("toolbarMeta").textContent = `${summary.solution_name} · ${summary.occupied_slots}/${summary.total_slots} 个货位已分配`;
    }

    function renderLegend() {
      document.getElementById("legend").innerHTML = [
        `<div class="legend-item"><span class="swatch" style="background:${emptySlotColor}"></span><span>未使用货位</span></div>`,
        `<div class="legend-item"><span class="swatch" style="background:${fullSlotColor}"></span><span>满量货位</span></div>`,
        `<div class="legend-item"><span class="swatch" style="background:${availableSlotColor}"></span><span>余量货位</span></div>`
      ].join("");
    }

    function renderDetails(slot) {
      if (!slot) {
        details.innerHTML = `<h2>货位详情</h2><div class="note">点击右侧货位卡片查看详细信息。</div>`;
        return;
      }

      const allocated = slot.allocation;
      details.innerHTML = `
        <h2>${slot.slot_id}</h2>
        <div class="details-grid">
          <div><strong>坐标</strong>${slot.x}, ${slot.y}</div>
          <div><strong>货位成本</strong>${slot.slot_cost ?? "-"}</div>
          <div><strong>托盘容量</strong>${slot.pallet_cap ?? "-"}</div>
          <div><strong>状态</strong>${allocated ? "已分配" : "空货位"}</div>
          <div><strong>SKU</strong>${allocated?.sku_id ?? "-"}</div>
          <div><strong>批次</strong>${allocated?.batch_id ?? "-"}</div>
          <div><strong>分配箱数</strong>${allocated?.qty_boxes_in_slot ?? "-"}</div>
          <div><strong>使用托盘</strong>${allocated?.pallets_used_in_slot ?? "-"}</div>
          <div><strong>总需求箱数</strong>${allocated?.qty_boxes ?? "-"}</div>
          <div><strong>已分配箱数</strong>${allocated?.assigned_boxes ?? "-"}</div>
        </div>
        <div class="note">当前界面按货位坐标排布。灰色表示未使用货位，红色表示已满量货位，绿色表示本次使用但仍有余量的货位。</div>
      `;
    }

    function renderWarehouse(data) {
      warehouse.innerHTML = "";
      const slots = data.slots || [];
      if (!slots.length) {
        warehouse.innerHTML = `<div class="note">没有可展示的货位数据。</div>`;
        return;
      }

      const xs = slots.map(s => s.x);
      const ys = slots.map(s => s.y);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const unit = 34;
      const cardW = 92;
      const cardH = 92;
      const pad = 36;
      const width = Math.max(900, (maxX - minX + 1) * unit + cardW + pad * 2);
      const height = Math.max(640, (maxY - minY + 1) * unit + cardH + pad * 2);
      warehouse.style.width = `${width}px`;
      warehouse.style.height = `${height}px`;
      scene.style.width = `${width}px`;
      scene.style.height = `${height}px`;

      const query = searchInput.value.trim().toLowerCase();

      slots.forEach((slot, index) => {
        const allocated = slot.allocation;
        const bg = slotColor(slot);
        const searchable = [slot.slot_id, allocated?.sku_id || "", allocated?.batch_id || ""].join(" ").toLowerCase();
        const isMatch = query && searchable.includes(query);
        const div = document.createElement("div");
        div.className = `slot ${allocated ? "occupied" : "empty"} ${isMatch ? "match" : ""}`;
        div.style.left = `${pad + (slot.x - minX) * unit}px`;
        div.style.top = `${pad + (slot.y - minY) * unit}px`;
        div.style.width = `${cardW}px`;
        div.style.height = `${cardH}px`;
        div.style.background = allocated ? `linear-gradient(180deg, ${bg}, ${bg}CC)` : "#e6ded1";
        div.style.animation = `fadeIn 220ms ease ${Math.min(index * 8, 300)}ms both`;
        div.innerHTML = `
          <div class="slot-id">${slot.slot_id}</div>
          <div class="slot-sku">${allocated ? allocated.sku_id : "EMPTY"}</div>
          <div class="slot-batch">${allocated ? `批次 ${allocated.batch_id}` : "未分配"}</div>
          <div class="slot-qty">${allocated ? `${allocated.qty_boxes_in_slot} 箱 / ${allocated.pallets_used_in_slot} 托` : ""}</div>
        `;
        div.addEventListener("click", () => {
          state.selectedSlotId = slot.slot_id;
          renderDetails(slot);
        });
        warehouse.appendChild(div);
      });

      const selected = slots.find(s => s.slot_id === state.selectedSlotId);
      renderDetails(selected || null);
      applyViewTransform();
    }

    function exportCurrentView() {
      if (!state.currentData) return;
      const slots = state.currentData.slots || [];
      if (!slots.length) return;

      const xs = slots.map(s => s.x);
      const ys = slots.map(s => s.y);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const unit = 34;
      const cardW = 92;
      const cardH = 92;
      const pad = 36;
      const width = Math.max(900, (maxX - minX + 1) * unit + cardW + pad * 2);
      const height = Math.max(640, (maxY - minY + 1) * unit + cardH + pad * 2);

      const cards = slots.map(slot => {
        const allocated = slot.allocation;
        const left = pad + (slot.x - minX) * unit;
        const top = pad + (slot.y - minY) * unit;
        const bg = slotColor(slot);
        const sku = allocated ? allocated.sku_id : "EMPTY";
        const batch = allocated ? `批次 ${allocated.batch_id}` : "未分配";
        const qty = allocated ? `${allocated.qty_boxes_in_slot} 箱 / ${allocated.pallets_used_in_slot} 托` : "";
        return `
          <g transform="translate(${left},${top})">
            <rect x="0" y="0" rx="16" ry="16" width="${cardW}" height="${cardH}" fill="${bg}" stroke="rgba(31,42,46,0.12)" />
            <text x="10" y="16" font-size="11" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="rgba(31,42,46,0.72)" font-weight="700">${escapeXml(slot.slot_id)}</text>
            <text x="10" y="40" font-size="16" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="#1f2a2e" font-weight="700">${escapeXml(sku)}</text>
            <text x="10" y="60" font-size="12" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="rgba(31,42,46,0.78)">${escapeXml(batch)}</text>
            <text x="10" y="77" font-size="12" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="rgba(31,42,46,0.78)">${escapeXml(qty)}</text>
          </g>
        `;
      }).join("");

      const title = `${state.currentData.summary.solution_name} · 货位推荐视图`;
      const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height + 64}" viewBox="0 0 ${width} ${height + 64}">
          <rect width="100%" height="100%" fill="#f4efe7" />
          <rect x="0" y="0" width="${width}" height="64" fill="#fbf7f1" />
          <text x="24" y="30" font-size="24" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="#1f2a2e" font-weight="700">${escapeXml(title)}</text>
          <text x="24" y="50" font-size="13" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" fill="#6f7a73">occupied ${state.currentData.summary.occupied_slots}/${state.currentData.summary.total_slots} · sku ${state.currentData.summary.distinct_skus}</text>
          <g transform="translate(0,64)">${cards}</g>
        </svg>
      `.trim();

      const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height + 64;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#f4efe7";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        const link = document.createElement("a");
        link.href = canvas.toDataURL("image/png");
        link.download = `${state.currentData.summary.solution_name.replace(/\\.csv$/i, "")}_layout.png`;
        link.click();
      };
      img.src = url;
    }

    async function loadSolutions() {
      const data = await fetchJson("/api/solutions");
      state.solutions = data.solutions || [];
      solutionSelect.innerHTML = state.solutions.map(name => `<option value="${name}">${name}</option>`).join("");
      if (!state.solutions.length) {
        renderError("results 目录下未找到 slotting_solution*.csv 文件。");
        return;
      }
      const preferred = state.solutions.find(name => name.includes("copt")) || state.solutions[0];
      solutionSelect.value = preferred;
      await loadLayout(preferred);
    }

    async function loadLayout(solutionName) {
      renderError("");
      const data = await fetchJson(`/api/layout?solution=${encodeURIComponent(solutionName)}`);
      state.currentData = data;
      state.selectedSlotId = null;
      updateStats(data.summary);
      renderLegend();
      renderWarehouse(data);
      resetView();
    }

    solutionSelect.addEventListener("change", async event => {
      await loadLayout(event.target.value);
    });

    searchInput.addEventListener("input", () => {
      if (state.currentData) renderWarehouse(state.currentData);
    });

    viewport.addEventListener("wheel", event => {
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const originX = event.clientX - rect.left;
      const originY = event.clientY - rect.top;
      const factor = event.deltaY < 0 ? 1.12 : 0.89;
      zoomAt(state.view.scale * factor, originX, originY);
    }, { passive: false });

    viewport.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      state.drag.active = true;
      state.drag.startX = event.clientX;
      state.drag.startY = event.clientY;
      state.drag.baseX = state.view.offsetX;
      state.drag.baseY = state.view.offsetY;
      viewport.classList.add("dragging");
    });

    window.addEventListener("pointermove", event => {
      if (!state.drag.active) return;
      state.view.offsetX = state.drag.baseX + (event.clientX - state.drag.startX);
      state.view.offsetY = state.drag.baseY + (event.clientY - state.drag.startY);
      applyViewTransform();
    });

    window.addEventListener("pointerup", () => {
      state.drag.active = false;
      viewport.classList.remove("dragging");
    });

    document.getElementById("zoomInBtn").addEventListener("click", () => {
      const rect = viewport.getBoundingClientRect();
      zoomAt(state.view.scale * 1.15, rect.width / 2, rect.height / 2);
    });

    document.getElementById("zoomOutBtn").addEventListener("click", () => {
      const rect = viewport.getBoundingClientRect();
      zoomAt(state.view.scale * 0.87, rect.width / 2, rect.height / 2);
    });

    document.getElementById("resetViewBtn").addEventListener("click", resetView);
    document.getElementById("exportBtn").addEventListener("click", exportCurrentView);
    toggleSidebarBtn.addEventListener("click", () => {
      const collapsed = shell.classList.toggle("sidebar-collapsed");
      toggleSidebarBtn.textContent = collapsed ? "展开侧栏" : "收起侧栏";
      window.setTimeout(() => {
        applyViewTransform();
      }, 190);
    });

    loadSolutions().catch(error => {
      renderError(error.message);
    });
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local UI for viewing slotting solutions.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--slots", default="data/slotting/slots.csv")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--solution-pattern", default="slotting_solution*.csv")
    return parser.parse_args()


def read_slots(slots_path: Path) -> pd.DataFrame:
    slots = pd.read_csv(slots_path).copy()
    required = {"slot_id", "slot_cost", "x", "y"}
    missing = required - set(slots.columns)
    if missing:
        raise ValueError(f"slots file missing columns: {sorted(missing)}")

    slots["slot_id"] = slots["slot_id"].astype(str)
    slots["slot_cost"] = pd.to_numeric(slots["slot_cost"], errors="coerce")
    slots["x"] = pd.to_numeric(slots["x"], errors="coerce")
    slots["y"] = pd.to_numeric(slots["y"], errors="coerce")
    if "pallet_cap" in slots.columns:
      slots["pallet_cap"] = pd.to_numeric(slots["pallet_cap"], errors="coerce")
    else:
      slots["pallet_cap"] = None
    return slots


def read_solution(solution_path: Path) -> pd.DataFrame:
    solution = pd.read_csv(solution_path).copy()
    required = {"slot_id", "sku_id", "batch_id", "qty_boxes_in_slot", "pallets_used_in_slot"}
    missing = required - set(solution.columns)
    if missing:
        raise ValueError(f"solution file missing columns: {sorted(missing)}")

    solution["slot_id"] = solution["slot_id"].astype(str)
    solution["sku_id"] = solution["sku_id"].astype(str)
    solution["batch_id"] = solution["batch_id"].astype(str)
    for col in ["qty_boxes_in_slot", "pallets_used_in_slot", "qty_boxes", "assigned_boxes"]:
        if col in solution.columns:
            solution[col] = pd.to_numeric(solution[col], errors="coerce")
    return solution


def build_layout_payload(slots_path: Path, solution_path: Path) -> dict:
    slots = read_slots(slots_path)
    solution = read_solution(solution_path)
    alloc_map = {
        row["slot_id"]: {key: (None if pd.isna(value) else value) for key, value in row.items()}
        for _, row in solution.drop_duplicates("slot_id").to_dict(orient="index").items()
    }

    slots_payload = []
    for _, row in slots.iterrows():
        slot_id = row["slot_id"]
        slots_payload.append(
            {
                "slot_id": slot_id,
                "slot_cost": None if pd.isna(row["slot_cost"]) else float(row["slot_cost"]),
                "pallet_cap": None if pd.isna(row["pallet_cap"]) else int(row["pallet_cap"]),
                "x": float(row["x"]),
                "y": float(row["y"]),
                "allocation": alloc_map.get(slot_id),
            }
        )

    occupied = solution["slot_id"].nunique()
    total_slots = int(len(slots))
    top_skus = (
        solution.groupby("sku_id")["qty_boxes_in_slot"].sum().sort_values(ascending=False).head(8).index.tolist()
    )
    summary = {
        "solution_name": solution_path.name,
        "total_slots": total_slots,
        "occupied_slots": int(occupied),
        "occupancy_rate": round((occupied / total_slots) * 100, 2) if total_slots else 0.0,
        "distinct_skus": int(solution["sku_id"].nunique()),
        "top_skus": top_skus,
    }
    return {"summary": summary, "slots": slots_payload}


class SlottingViewerHandler(BaseHTTPRequestHandler):
    slots_path: Path = PROJECT_ROOT / "data" / "slotting" / "slots.csv"
    results_dir: Path = PROJECT_ROOT / "results"
    solution_pattern: str = "slotting_solution*.csv"

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: int = HTTPStatus.OK) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(HTML_PAGE)
                return
            if parsed.path == "/api/solutions":
                solutions = sorted(path.name for path in self.results_dir.glob(self.solution_pattern) if path.is_file())
                self._send_json({"solutions": solutions})
                return
            if parsed.path == "/api/layout":
                solution_name = parse_qs(parsed.query).get("solution", [""])[0]
                if not solution_name:
                    self._send_json({"error": "missing solution parameter"}, HTTPStatus.BAD_REQUEST)
                    return
                solution_path = (self.results_dir / solution_name).resolve()
                if solution_path.parent != self.results_dir.resolve() or not solution_path.exists():
                    self._send_json({"error": "solution file not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(build_layout_payload(self.slots_path, solution_path))
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> None:
    args = parse_args()
    SlottingViewerHandler.slots_path = (PROJECT_ROOT / args.slots).resolve()
    SlottingViewerHandler.results_dir = (PROJECT_ROOT / args.results_dir).resolve()
    SlottingViewerHandler.solution_pattern = args.solution_pattern

    server = ThreadingHTTPServer((args.host, args.port), SlottingViewerHandler)
    print(f"Slotting viewer running at http://{args.host}:{args.port}")
    print(f"Slots file: {SlottingViewerHandler.slots_path}")
    print(f"Results dir: {SlottingViewerHandler.results_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
