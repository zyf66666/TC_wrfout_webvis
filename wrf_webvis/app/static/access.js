(function (global) {
  "use strict";

  // 2026-11-06：账号登录。/api/access 返回 {authenticated, username, role, is_local, read_only, can_write, can_annotate, pages}。
  // can_write 只有 root（含本机）为 true；普通账号按勾选可批注、可打开部分页面。拿不到或格式不对时一律按只读处理。
  let writable = false;
  let annotate = false;
  let plotSettings = false;
  let plot = false;
  let pages = new Set();
  let info = { authenticated: false, username: "", role: "anonymous", is_local: false };
  const doc = global.document;
  const state = { ready: false };
  const writeMethod = method => !["GET", "HEAD", "OPTIONS"].includes(String(method || "GET").toUpperCase());
  const annotationWrite = /^\/api\/dashboards\/[^/]+\/images\/[^/]+\/(note|annotations|annotations\/[^/]+)(\?.*)?$/;
  const plotWrite = /^(\/api\/dashboards\/[^/]+\/images\/jobs|\/api\/plots\/dry-run|\/api\/jobs\/[^/]+\/cancel|\/api\/dashboards\/[^/]+\/images\/[^/]+\/restore)(\?.*)?$/;
  const imageDelete = /^\/api\/dashboards\/[^/]+\/images\/[^/]+(\?.*)?$/;

  function redirectToLogin() {
    const location = global.location;
    if (!location || String(location.pathname || "").startsWith("/login")) return;
    location.replace(`/login?next=${encodeURIComponent(`${location.pathname}${location.search || ""}`)}`);
  }

  async function logout() {
    try { await global.fetch("/api/auth/logout", { method: "POST" }); } catch (_) { /* 照样跳回登录页 */ }
    global.location?.replace("/login");
  }

  async function changePassword() {
    const oldPassword = global.prompt?.("当前密码");
    if (!oldPassword) return;
    const newPassword = global.prompt?.("新密码（至少 6 位）");
    if (!newPassword) return;
    if (global.prompt?.("再输一遍新密码") !== newPassword) { global.alert?.("两次输入不一致，没有修改。"); return; }
    const response = await global.fetch("/api/auth/password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    let message = response.ok ? "密码已修改。" : `修改失败（${response.status}）`;
    if (!response.ok) {
      try { const payload = await response.json(); message = payload?.detail?.message || (typeof payload?.detail === "string" ? payload.detail : message); } catch (_) { /* 非 JSON */ }
    }
    global.alert?.(message);
  }

  function renderAccountChip() {
    const topbar = doc.querySelector(".topbar");
    if (!topbar) return;
    let chip = doc.querySelector("#accountChip");
    if (!info.authenticated) { chip?.remove(); return; }
    if (!chip) {
      chip = doc.createElement("span");
      chip.id = "accountChip";
      chip.className = "account-chip";
      topbar.append(chip);
    }
    chip.replaceChildren();
    const name = doc.createElement("span");
    name.className = "account-name";
    name.textContent = info.is_local ? "本机（root）" : `${info.username}${info.role === "root" ? "（root）" : ""}`;
    chip.append(name);
    if (!info.is_local) {
      const password = doc.createElement("button");
      password.type = "button"; password.textContent = "改密码"; password.className = "account-button";
      password.addEventListener("click", () => changePassword().catch(error => global.alert?.(error.message)));
      const exit = doc.createElement("button");
      exit.type = "button"; exit.textContent = "退出"; exit.className = "account-button";
      exit.addEventListener("click", logout);
      chip.append(password, exit);
    }
  }

  function applyState() {
    if (!doc?.body) return;
    doc.body.classList.toggle("readonly", !writable);
    doc.body.classList.toggle("can-annotate", annotate);
    doc.body.classList.toggle("can-plot-settings", plotSettings);
    doc.body.classList.toggle("can-plot", plot);
    for (const name of ["data"]) doc.body.classList.toggle(`page-${name}`, pages.has(name));
    doc.body.dataset.accessReady = state.ready ? "true" : "false";
    let badge = doc.querySelector("#readonlyBadge");
    if (!writable && state.ready && !annotate) {
      if (!badge) {
        badge = doc.createElement("span");
        badge.id = "readonlyBadge";
        badge.className = "readonly-badge";
        badge.textContent = "只读模式";
        badge.setAttribute("role", "status");
        doc.querySelector(".topbar")?.append(badge);
      }
    } else {
      badge?.remove();
    }
    if (state.ready) renderAccountChip();
  }

  applyState();
  const controller = global.AbortController ? new global.AbortController() : null;
  const timeout = global.setTimeout ? global.setTimeout(() => controller?.abort(), 5000) : null;
  const ready = global.fetch("/api/access", { cache: "no-store", ...(controller ? { signal: controller.signal } : {}) })
    .then(response => {
      if (!response?.ok) throw new Error(`access request failed: ${response?.status || "unknown"}`);
      return response.json();
    })
    .then(payload => {
      if (typeof payload?.can_write !== "boolean" || typeof payload?.read_only !== "boolean") throw new Error("invalid access response");
      if (payload.authenticated === false) {
        redirectToLogin();
        throw new Error("not logged in");
      }
      writable = payload.can_write === true && payload.read_only === false;
      annotate = writable || payload.can_annotate === true;
      plot = writable || payload.can_plot === true;
      plotSettings = plot || payload.can_view_plot_settings === true;
      pages = new Set(writable ? ["data"] : (Array.isArray(payload.pages) ? payload.pages : []));
      info = {
        authenticated: true,
        username: String(payload.username || ""),
        role: String(payload.role || (writable ? "root" : "user")),
        is_local: payload.is_local === true,
      };
      return payload;
    })
    .catch(() => {
      writable = false;
      annotate = false;
      plot = false;
      plotSettings = false;
      pages = new Set();
      return { read_only: true, can_write: false };
    })
    .finally(() => {
      if (timeout && global.clearTimeout) global.clearTimeout(timeout);
      state.ready = true;
      applyState();
    });

  global.WRFWebVisAccess = {
    ready,
    canWrite: () => writable,
    canAnnotate: () => annotate,
    canViewPlotSettings: () => plotSettings,
    canPlot: () => plot,
    canSee: page => writable || pages.has(page),
    user: () => ({ ...info }),
    logout,
    // url 可选：普通账号勾了“可批注”时，批注和图片备注接口放行（后端还会再查）。
    assertWrite: async (method, url) => {
      await ready;
      if (!writeMethod(method) || writable) return true;
      const path = String(url || "");
      if (path.startsWith("/api/auth/")) return true;
      if (annotate && annotationWrite.test(path)) return true;
      if (plot && plotWrite.test(path)) return true;
      if (plot && String(method).toUpperCase() === "DELETE" && imageDelete.test(path)) return true;
      throw new Error("操作被拒绝：当前为只读模式");
    },
    isWriteMethod: writeMethod,
  };
})(window);
