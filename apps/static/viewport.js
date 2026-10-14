// 仓库布局 / 拣货路线视图的 zoom + pan 控制器，slotting 与 picking-path 两个结果页共用。
// 各结果页提供 viewport / scene / 缩放按钮的 DOM id 与可选初始 offset，控制器自己绑定事件。
(function () {
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  window.escapeHtml = function escapeHtml(value) {
    return String(value == null ? "-" : value).replace(/[&<>"'`]/g, function (ch) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
        "`": "&#96;",
      }[ch];
    });
  };

  window.renderTable = function renderTable(rows) {
    if (!rows || !rows.length) return "<div class='muted'>暂无数据</div>";
    const columns = Object.keys(rows[0]);
    const head = columns.map(function (col) { return "<th>" + window.escapeHtml(col) + "</th>"; }).join("");
    const body = rows.map(function (row) {
      return "<tr>" + columns.map(function (col) {
        const v = row[col];
        return "<td>" + (v == null ? "" : window.escapeHtml(v)) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
  };

  // initViewport({ viewportId, sceneId, zoomBadgeId, zoomInId, zoomOutId, resetId,
  //               initialOffsetX = 0, initialOffsetY = 0,
  //               snapToOriginWhenZoomedOut = false,   // slotting：scale<=1 时把 offset 归零
  //               resizeViewportToScene = false        // slotting：根据 layout 尺寸调 viewport 大小
  // })
  //
  // 返回 { state, applyTransform, reset, zoomAt, setSceneSize }，调用方可在拿到布局尺寸后调 setSceneSize。
  window.initViewport = function initViewport(opts) {
    const viewport = document.getElementById(opts.viewportId);
    const scene = document.getElementById(opts.sceneId);
    const zoomBadge = document.getElementById(opts.zoomBadgeId);

    const state = {
      view: {
        scale: 1,
        offsetX: opts.initialOffsetX || 0,
        offsetY: opts.initialOffsetY || 0,
      },
      layout: { width: 0, height: 0 },
      drag: { active: false, startX: 0, startY: 0, baseX: 0, baseY: 0 },
    };

    function applyTransform() {
      scene.style.transform =
        "translate(" + state.view.offsetX + "px, " + state.view.offsetY + "px) scale(" + state.view.scale + ")";
      if (opts.resizeViewportToScene && state.layout.width && state.layout.height) {
        viewport.style.width = state.layout.width * state.view.scale + "px";
        viewport.style.height = state.layout.height * state.view.scale + "px";
      }
      if (zoomBadge) zoomBadge.textContent = Math.round(state.view.scale * 100) + "%";
    }

    function reset() {
      state.view.scale = 1;
      state.view.offsetX = opts.initialOffsetX || 0;
      state.view.offsetY = opts.initialOffsetY || 0;
      applyTransform();
    }

    function zoomAt(nextScale, originX, originY) {
      const previousScale = state.view.scale;
      const scale = clamp(nextScale, 0.35, 3.5);
      state.view.scale = scale;
      if (opts.snapToOriginWhenZoomedOut && scale <= 1) {
        state.view.offsetX = 0;
        state.view.offsetY = 0;
      } else {
        const worldX = (originX - state.view.offsetX) / previousScale;
        const worldY = (originY - state.view.offsetY) / previousScale;
        state.view.offsetX = originX - worldX * scale;
        state.view.offsetY = originY - worldY * scale;
      }
      applyTransform();
    }

    function setSceneSize(width, height) {
      state.layout.width = width;
      state.layout.height = height;
      applyTransform();
    }

    viewport.addEventListener("wheel", function (event) {
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const originX = event.clientX - rect.left;
      const originY = event.clientY - rect.top;
      const factor = event.deltaY < 0 ? 1.12 : 0.89;
      zoomAt(state.view.scale * factor, originX, originY);
    }, { passive: false });

    viewport.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      state.drag.active = true;
      state.drag.startX = event.clientX;
      state.drag.startY = event.clientY;
      state.drag.baseX = state.view.offsetX;
      state.drag.baseY = state.view.offsetY;
      viewport.classList.add("dragging");
    });

    window.addEventListener("pointermove", function (event) {
      if (!state.drag.active) return;
      state.view.offsetX = state.drag.baseX + (event.clientX - state.drag.startX);
      state.view.offsetY = state.drag.baseY + (event.clientY - state.drag.startY);
      applyTransform();
    });

    window.addEventListener("pointerup", function () {
      state.drag.active = false;
      viewport.classList.remove("dragging");
    });

    if (opts.zoomInId) {
      const btn = document.getElementById(opts.zoomInId);
      if (btn) btn.addEventListener("click", function () {
        const rect = viewport.getBoundingClientRect();
        zoomAt(state.view.scale * 1.15, rect.width / 2, rect.height / 2);
      });
    }
    if (opts.zoomOutId) {
      const btn = document.getElementById(opts.zoomOutId);
      if (btn) btn.addEventListener("click", function () {
        const rect = viewport.getBoundingClientRect();
        zoomAt(state.view.scale * 0.87, rect.width / 2, rect.height / 2);
      });
    }
    if (opts.resetId) {
      const btn = document.getElementById(opts.resetId);
      if (btn) btn.addEventListener("click", reset);
    }

    return { state: state, applyTransform: applyTransform, reset: reset, zoomAt: zoomAt, setSceneSize: setSceneSize };
  };
})();
