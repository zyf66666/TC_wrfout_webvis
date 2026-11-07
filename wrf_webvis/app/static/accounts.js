// 账号管理对话框（只有 root 看得到“账号管理”按钮；接口 /api/users 也只给 root）。2026-11-06。
(function (root) {
  "use strict";

  const doc = root.document;
  const dialog = doc.querySelector("#accountsDialog");
  const openButton = doc.querySelector("#openAccounts");
  if (!dialog || !openButton) return;
  const listEl = doc.querySelector("#accountsList");
  const formEl = doc.querySelector("#accountForm");
  const statusEl = doc.querySelector("#accountsStatus");
  const state = { users: [], pages: {}, dashboards: [], selected: null };

  function setStatus(text) { statusEl.textContent = text || ""; }

  async function request(url, options = {}) {
    const response = await root.fetch(url, {
      method: options.method || "GET",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
    if (!response.ok) {
      let message = `请求失败（${response.status}）`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") message = payload.detail;
        else if (payload.detail?.message) message = payload.detail.message;
        else if (Array.isArray(payload.detail)) message = payload.detail.map((item) => item.msg).join("；");
      } catch (_) { /* 非 JSON */ }
      throw new Error(message);
    }
    return response.json();
  }

  function element(tag, props = {}, ...children) {
    const node = doc.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    node.append(...children.filter((child) => child !== null && child !== undefined));
    return node;
  }

  function dashboardSummary(user) {
    if (user.role === "root" || user.dashboards === "*") return "全部看板";
    const titles = (user.dashboards || []).map((id) => state.dashboards.find((item) => item.id === id)?.title).filter(Boolean);
    return titles.length ? titles.join("、") : "没有可见看板";
  }

  function renderList() {
    listEl.replaceChildren();
    if (!state.users.length) {
      listEl.append(element("p", { class: "annot-muted", textContent: "还没有账号。点“新建账号”。" }));
      return;
    }
    for (const user of state.users) {
      const pages = user.role === "root" ? "全部页面" : (user.pages.map((page) => state.pages[page] || page).join("、") || "只有看板");
      const row = element("button", {
        type: "button",
        class: `account-row${state.selected === user.username ? " active" : ""}${user.disabled ? " disabled" : ""}`,
      },
      element("strong", { textContent: `${user.username}${user.role === "root" ? "（root）" : ""}${user.disabled ? "（已停用）" : ""}` }),
      element("small", { textContent: user.role === "root" ? "全部权限" : `${[
        user.can_annotate ? "可批注" : "",
        user.can_plot ? "可出图" : (user.can_view_plot_settings ? "可看绘图设置" : ""),
      ].filter(Boolean).join("、") || "只看"} · ${pages}` }),
      element("small", { textContent: dashboardSummary(user) }));
      row.setAttribute("role", "listitem");
      row.addEventListener("click", () => { state.selected = user.username; renderList(); renderForm(user); });
      listEl.append(row);
    }
  }

  // 看板勾选树：勾父看板时子看板自动算在内（显示为已勾、不可单独取消）。
  function dashboardTree(chosen) {
    const ids = new Set(state.dashboards.map((item) => item.id));
    const roots = state.dashboards.filter((item) => !item.parent_id || !ids.has(item.parent_id));
    const tree = element("div", { class: "account-tree" });
    const boxes = new Map();
    const sync = () => {
      for (const item of state.dashboards) {
        if (!item.parent_id || !boxes.has(item.parent_id)) continue;
        const parentChecked = boxes.get(item.parent_id).checked;
        const box = boxes.get(item.id);
        box.disabled = parentChecked;
        box.closest("label").classList.toggle("inherited", parentChecked);
        if (parentChecked) box.checked = true;
      }
    };
    const add = (item, child) => {
      const box = element("input", { type: "checkbox", value: item.id, checked: chosen.has(item.id) });
      box.dataset.dashboard = item.id;
      box.addEventListener("change", sync);
      boxes.set(item.id, box);
      const kids = state.dashboards.filter((other) => other.parent_id === item.id);
      const label = element("label", { class: child ? "child" : "" }, box,
        doc.createTextNode(`${item.title}${kids.length ? `（含 ${kids.length} 个子看板）` : ""}`));
      tree.append(label);
      kids.forEach((kid) => add(kid, true));
    };
    roots.forEach((item) => add(item, false));
    sync();
    return { tree, selected: () => {
      // 父看板已勾时只存父看板（以后新建的子看板自动可见）
      const result = [];
      for (const [id, box] of boxes) {
        if (!box.checked) continue;
        const item = state.dashboards.find((entry) => entry.id === id);
        if (item?.parent_id && boxes.get(item.parent_id)?.checked) continue;
        result.push(id);
      }
      return result;
    } };
  }

  function renderForm(user) {
    const isNew = !user;
    formEl.hidden = false;
    formEl.replaceChildren();
    const data = user || { username: "", role: "user", can_annotate: false, pages: [], dashboards: [], disabled: false };
    formEl.append(element("h4", { textContent: isNew ? "新建账号" : `账号：${data.username}` }));

    const username = element("input", { type: "text", name: "username", value: data.username, maxLength: 32, required: true,
      placeholder: "字母、数字、_ . -", autocomplete: "off" });
    const password = element("input", { type: "password", name: "password", maxLength: 256, autocomplete: "new-password",
      placeholder: isNew ? "至少 6 位" : "留空表示不改" });
    const basics = element("div", { class: "inline-row" });
    if (isNew) basics.append(element("label", { class: "field" }, doc.createTextNode("用户名"), username));
    basics.append(element("label", { class: "field" }, doc.createTextNode(isNew ? "密码" : "重设密码"), password));
    const role = element("select", { name: "role" },
      element("option", { value: "user", textContent: "普通账号" }),
      element("option", { value: "root", textContent: "root（全部权限）" }));
    role.value = data.role;
    basics.append(element("label", { class: "field" }, doc.createTextNode("角色"), role));
    formEl.append(basics);

    const limited = element("div", { class: "account-limits" });
    const annotate = element("input", { type: "checkbox", name: "can_annotate", checked: Boolean(data.can_annotate) });
    const plotSettings = element("input", { type: "checkbox", name: "can_view_plot_settings",
      checked: Boolean(data.can_view_plot_settings || data.can_plot) });
    const plot = element("input", { type: "checkbox", name: "can_plot", checked: Boolean(data.can_plot) });
    // 能出图就一定能看参数
    const syncPlot = () => { if (plot.checked) plotSettings.checked = true; plotSettings.disabled = plot.checked; };
    plot.addEventListener("change", syncPlot);
    syncPlot();
    limited.append(element("fieldset", {}, element("legend", { textContent: "看板里能做什么" }),
      element("label", {}, annotate, doc.createTextNode("可以在图上画批注、写图片下方的文字批注（只能改删自己写的）")),
      element("label", {}, plotSettings, doc.createTextNode("可以查看绘图设置（单击图片看出图参数，只读）")),
      element("label", {}, plot, doc.createTextNode("可以出图：用绘图界面往自己能看的看板里加新图（不能删图，不能运行自定义代码，只能用网页的数据集目录）"))));

    const pageBoxes = Object.entries(state.pages).map(([key, label]) => {
      const box = element("input", { type: "checkbox", value: key, checked: (data.pages || []).includes(key) });
      return { key, box, label: element("label", {}, box, doc.createTextNode(label)) };
    });
    limited.append(element("fieldset", {}, element("legend", { textContent: "除看板外还能打开的页面" }),
      element("div", { class: "check-grid" }, ...pageBoxes.map((item) => item.label))));

    const allBox = element("input", { type: "radio", name: "dashboard_scope", value: "all", checked: data.dashboards === "*" });
    const someBox = element("input", { type: "radio", name: "dashboard_scope", value: "some", checked: data.dashboards !== "*" });
    const chosen = new Set(Array.isArray(data.dashboards) ? data.dashboards : []);
    const tree = dashboardTree(chosen);
    const syncScope = () => { tree.tree.hidden = allBox.checked; };
    allBox.addEventListener("change", syncScope); someBox.addEventListener("change", syncScope);
    syncScope();
    limited.append(element("fieldset", {}, element("legend", { textContent: "能看哪些看板" }),
      element("div", { class: "inline-row" },
        element("label", {}, allBox, doc.createTextNode("全部看板（含以后新建的）")),
        element("label", {}, someBox, doc.createTextNode("只看勾选的"))),
      tree.tree));
    formEl.append(limited);
    const syncRole = () => { limited.hidden = role.value === "root"; };
    role.addEventListener("change", syncRole);
    syncRole();

    let disabled = null;
    if (!isNew) {
      disabled = element("input", { type: "checkbox", name: "disabled", checked: Boolean(data.disabled) });
      formEl.append(element("label", {}, disabled, doc.createTextNode("停用这个账号（不能登录，已登录的立即退出）")));
    }

    const save = element("button", { type: "submit", class: "primary", textContent: isNew ? "建立账号" : "保存修改" });
    const actions = element("div", { class: "form-actions" }, save);
    if (!isNew) {
      const remove = element("button", { type: "button", class: "danger", textContent: "删除账号" });
      remove.addEventListener("click", () => deleteUser(data.username));
      actions.append(remove);
    }
    formEl.append(actions);

    formEl.onsubmit = async (event) => {
      event.preventDefault();
      const body = {
        role: role.value,
        can_annotate: annotate.checked,
        can_view_plot_settings: plotSettings.checked || plot.checked,
        can_plot: plot.checked,
        pages: pageBoxes.filter((item) => item.box.checked).map((item) => item.key),
        dashboards: allBox.checked ? "*" : tree.selected(),
      };
      save.disabled = true;
      try {
        if (isNew) {
          await request("/api/users", { method: "POST", body: { ...body, username: username.value.trim(), password: password.value } });
          state.selected = username.value.trim();
          setStatus(`已建立账号 ${state.selected}。`);
        } else {
          await request(`/api/users/${encodeURIComponent(data.username)}`, { method: "PATCH", body: { ...body, disabled: disabled.checked } });
          if (password.value) await request(`/api/users/${encodeURIComponent(data.username)}/password`, { method: "POST", body: { password: password.value } });
          setStatus(`已保存 ${data.username}${password.value ? "，密码已重设（该账号需重新登录）" : ""}。`);
        }
        await refresh();
      } catch (error) {
        setStatus(error.message);
      } finally {
        save.disabled = false;
      }
    };
  }

  async function deleteUser(username) {
    if (!root.confirm(`删除账号「${username}」？删除后不能恢复，需要时可重新建立。`)) return;
    try {
      await request(`/api/users/${encodeURIComponent(username)}`, { method: "DELETE" });
      state.selected = null;
      formEl.hidden = true;
      setStatus(`已删除账号 ${username}。`);
      await refresh();
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function refresh() {
    const [users, dashboards] = await Promise.all([request("/api/users"), request("/api/dashboards?summary=1")]);
    state.users = users.users || [];
    state.pages = users.pages || {};
    state.dashboards = (dashboards || []).map((item) => ({ id: item.id, title: item.title, parent_id: item.parent_id || null }));
    renderList();
    const current = state.users.find((user) => user.username === state.selected);
    if (current) renderForm(current);
  }

  openButton.addEventListener("click", async () => {
    setStatus("");
    formEl.hidden = true;
    state.selected = null;
    dialog.showModal();
    try { await refresh(); } catch (error) { setStatus(error.message); }
  });
  doc.querySelector("#closeAccounts")?.addEventListener("click", () => dialog.close());
  doc.querySelector("#newAccount")?.addEventListener("click", () => {
    state.selected = null;
    renderList();
    renderForm(null);
    setStatus("");
  });
})(window);
