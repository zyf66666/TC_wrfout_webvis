(function (global) {
  "use strict";

  function splitList(input) {
    return String(input || "").split(/[,，|\n]+/).map(value => value.trim()).filter(Boolean);
  }

  // 只允许整数与 + - * / ( )，例如 "103*60"、"(24+6)*60"；不使用 eval。
  function evaluateIntegerExpression(text) {
    const source = String(text).trim();
    if (!source || !/^[0-9+\-*/()\s]+$/.test(source)) throw new Error(`无法识别“${source}”：只能使用整数和 + - * / ( )`);
    const tokens = source.match(/\d+|[+\-*/()]/g) || [];
    let position = 0;
    const peek = () => tokens[position];
    const take = () => tokens[position++];
    function primary() {
      const token = take();
      if (token === "(") {
        const inner = sum();
        if (take() !== ")") throw new Error(`“${source}”的括号不匹配`);
        return inner;
      }
      if (token === "-") return -primary();
      if (token === "+") return primary();
      if (token !== undefined && /^\d+$/.test(token)) return Number(token);
      throw new Error(`无法识别“${source}”`);
    }
    function product() {
      let result = primary();
      while (peek() === "*" || peek() === "/") result = take() === "*" ? result * primary() : result / primary();
      return result;
    }
    function sum() {
      let result = product();
      while (peek() === "+" || peek() === "-") result = take() === "+" ? result + product() : result - product();
      return result;
    }
    const result = sum();
    if (position !== tokens.length) throw new Error(`无法识别“${source}”`);
    if (!Number.isInteger(result)) throw new Error(`“${source}”的结果必须为整数`);
    return result;
  }

  function parseIntegerSelection(input, limit) {
    const text = String(input || "").trim();
    if (!text) return [];
    const values = [];
    for (const token of splitList(text)) {
      if (/[:：]/.test(token)) {
        const parts = token.split(/[:：]/);
        if (parts.length !== 3) throw new Error("范围格式应为 起点:终点:步长，例如 0:120:60 或 0:103*60:60");
        const [start, end, step] = parts.map(evaluateIntegerExpression);
        if (step === 0 || Math.sign(step) !== Math.sign(end - start || step)) throw new Error("步长必须朝向终点且不能为 0");
        const count = Math.floor((end - start) / step) + 1;
        if (count > limit || values.length + count > limit) throw new Error(`选择数量不能超过 ${limit}`);
        for (let index = 0; index < count; index++) values.push(start + index * step);
      } else {
        values.push(evaluateIntegerExpression(token));
      }
      if (values.length > limit) throw new Error(`选择数量不能超过 ${limit}`);
    }
    return [...new Set(values)];
  }

  const MAX_TIMES = 2000;

  function requiredNumber(value, name) {
    const number = Number(value);
    if (String(value).trim() === "" || !Number.isFinite(number)) throw new Error(`${name} 必须填写有效数字`);
    return number;
  }

  function optionalNumber(value, name) {
    return value === undefined || value === null || String(value).trim() === "" ? undefined : requiredNumber(value, name);
  }

  function buildProcessingPayload(values) {
    const domain = requiredNumber(values.domain, "domain");
    if (!Number.isInteger(domain) || domain < 1 || domain > 99) throw new Error("domain 必须是 1–99 的整数");
    const variables = splitList(values.variables);
    const experiments = [...new Set(splitList(values.experiments ?? values.experiment))];
    // 2026-11-02：时次上限 2000（120 h 的 5 min 序列为 1441 个），与后端一致。
    const times = parseIntegerSelection(values.times_minutes, MAX_TIMES);
    const heights = parseIntegerSelection(values.heights_m, 100);
    if (!experiments.length) throw new Error("请至少选择一个试验");
    if (!variables.length || !times.length) throw new Error("请选择变量和时次");
    if (times.some(value => value < 0) || heights.some(value => value <= 0)) throw new Error("时次不能为负，高度必须大于 0");
    const options = {
      radius_km_by_domain: { [domain]: requiredNumber(values.radius_km, "极坐标半径") },
      radius_resolution_km: requiredNumber(values.radius_resolution_km, "极坐标径向间隔"),
      ...(values.center_scheme ? {} : { center_options_by_domain: { [domain]: { search_radius_km: requiredNumber(values.center_search_radius_km, "中心搜索半径") } } }),
      theta_environment_domain: requiredNumber(values.theta_environment_domain, "环境参考 domain"),
      theta_environment_ring_km: [
        requiredNumber(values.theta_ring_inner_km, "环境环内半径"),
        requiredNumber(values.theta_ring_outer_km, "环境环外半径"),
      ],
      shear_source_domain: requiredNumber(values.shear_source_domain, "风切变来源 domain"),
      shear_radius_inner_km: requiredNumber(values.shear_radius_inner_km, "风切变内半径"),
      shear_radius_outer_km: requiredNumber(values.shear_radius_outer_km, "风切变外半径"),
    };
    // 2026-11-04 非对称度参数：空着时用服务器默认值（100 km、3000 m）。
    const asymmetryRadius = optionalNumber(values.asymmetry_radius_km, "非对称度圆盘半径");
    const dbzAsymmetryHeight = optionalNumber(values.dbz_asymmetry_height_m, "dbz 非对称度高度");
    if (asymmetryRadius !== undefined) options.asymmetry_radius_km = asymmetryRadius;
    if (dbzAsymmetryHeight !== undefined) options.dbz_asymmetry_height_m = dbzAsymmetryHeight;
    return {
      experiments, domain, variables: [...new Set(variables)],
      times_minutes: times, heights_m: heights,
      profile_id: String(values.profile_id || "default"), options,
      force: Boolean(values.force),
      clip_to_source_end: Boolean(values.clip_to_source_end),
      // 2026-11-02 中心方案：选了方案时中心参数由方案决定，不再发送“中心搜索半径”。
      ...(values.center_scheme ? { center_scheme: String(values.center_scheme) } : {}),
      // 2026-11-02 数据集（输出根目录）：post = 网页处理专用目录；不发送时后端写 processed_v3。
      ...(values.dataset ? { dataset: String(values.dataset) } : {}),
      ...(values.label ? { label: String(values.label) } : {}),
    };
  }

  // 整数列表压缩成“起点:终点:步长”写法，例如 100…1000 每 100、1250…10000 每 250 → "100:1000:100,1250:10000:250"。
  function compactIntegers(values) {
    const sorted = [...new Set((values || []).map(Number))].sort((a, b) => a - b);
    const parts = [];
    let index = 0;
    while (index < sorted.length) {
      let end = index;
      const step = sorted[index + 1] - sorted[index];
      while (end + 1 < sorted.length && sorted[end + 1] - sorted[end] === step) end++;
      if (end - index >= 2) { parts.push(`${sorted[index]}:${sorted[end]}:${step}`); index = end + 1; }
      else { parts.push(String(sorted[index])); index++; }
    }
    return parts.join(",");
  }

  // 处理套餐（2026-11-02）：勾选的组 → 合并 → 按时间分段 → 每段 × 每个试验一个普通处理请求。
  // 合并：时间步长相同、高度相同的组合成一个请求；只有标量/地面变量（高度留空）的组并入同步长的有高度请求。
  //   同一请求里同一时次的 wrfout 只打开一次，三维气压、高度坐标、dbz 等共用，中心也只算一次。
  //   dimensionOf(name) 给出变量维度时，高度留空却含三维变量的组不并入（留给后端报“三维变量必须选择高度”）。
  // 分段：每个合并后的请求按时次切成 parts 段连续区间，作为独立任务同时运行。
  function mergePresetGroups(groups, dimensionOf = null, merge = true) {
    const chosen = (groups || []).filter(group => group.enabled);
    if (!chosen.length) throw new Error("请至少勾选套餐里的一个组");
    const normalized = chosen.map((group, order) => {
      const name = group.label || group.id;
      const step = requiredNumber(group.time_step_minutes, `${name} 的时间步长`);
      if (!Number.isInteger(step) || step <= 0) throw new Error(`${name} 的时间步长必须是正整数分钟`);
      const variables = Array.isArray(group.variables) ? group.variables.map(String) : splitList(group.variables);
      if (!variables.length) throw new Error(`${name}：请填写变量`);
      const heights = Array.isArray(group.heights) ? [...new Set(group.heights.map(Number))].sort((a, b) => a - b) : parseIntegerSelection(group.heights, 100).sort((a, b) => a - b);
      const needsHeights = typeof dimensionOf === "function" && variables.some(variable => ["height", "height_scalar", "height_vector"].includes(dimensionOf(variable)));
      return { ids: [group.id], labels: [name], step, variables, heights, order, standalone: !heights.length && needsHeights };
    });
    if (!merge) {
      // 套餐设了 merge_groups=false：每组各自一个请求（各组变量多、互不共用原始场时，分开才能同时运行）。
      return normalized.map(item => ({ ids: item.ids, labels: item.labels, step: item.step, heights: item.heights, order: item.order, standalone: item.standalone, variables: [...new Set(item.variables)] }));
    }
    const merged = [];
    for (const group of normalized.filter(item => item.heights.length || item.standalone)) {
      const key = group.heights.join(",");
      const target = !group.standalone && merged.find(item => !item.standalone && item.step === group.step && item.heights.join(",") === key);
      if (target) absorb(target, group);
      else merged.push({ ...group, ids: [...group.ids], labels: [...group.labels], variables: [...group.variables] });
    }
    for (const group of normalized.filter(item => !item.heights.length && !item.standalone)) {
      const target = merged.find(item => !item.standalone && item.step === group.step);
      if (target) absorb(target, group);
      else merged.push({ ...group, ids: [...group.ids], labels: [...group.labels], variables: [...group.variables] });
    }
    // 按套餐里先出现的组排序；合并后的组名、变量也按原顺序排列。
    return merged
      .map(item => {
        const members = item.members || [{ order: item.order, id: item.ids[0], label: item.labels[0], variables: item.variables }];
        members.sort((a, b) => a.order - b.order);
        return {
          ids: members.map(member => member.id), labels: members.map(member => member.label),
          step: item.step, heights: item.heights, order: members[0].order, standalone: item.standalone,
          variables: [...new Set(members.flatMap(member => member.variables))],
        };
      })
      .sort((a, b) => a.order - b.order);

    function absorb(target, group) {
      if (!target.members) target.members = [{ order: target.order, id: target.ids[0], label: target.labels[0], variables: [...target.variables] }];
      target.members.push({ order: group.order, id: group.ids[0], label: group.labels[0], variables: group.variables });
      target.ids.push(...group.ids); target.labels.push(...group.labels); target.variables.push(...group.variables);
    }
  }

  // 把 start..end（含两端）按 step 取的时次切成至多 parts 段连续区间，返回 [{start, end}]（分钟）。
  function splitTimeRange(start, end, step, parts) {
    const count = Math.floor((end - start) / step) + 1;
    const pieces = Math.max(1, Math.min(Math.floor(Number(parts) || 1), count));
    const ranges = [];
    let index = 0;
    for (let piece = 0; piece < pieces; piece++) {
      const size = Math.floor(count / pieces) + (piece < count % pieces ? 1 : 0);
      ranges.push({ start: start + index * step, end: start + (index + size - 1) * step });
      index += size;
    }
    return ranges;
  }

  function hoursText(minutes) {
    return trimNumber(minutes / 60);
  }

  const DEFAULT_END_HOURS = 120;

  // 每个试验的结束分钟：填了结束时间就用它；留空时用该试验的模拟时长（catalog.experiment_end_hours，
  // 来自 project_config.EXPERIMENT_END_HOURS），表里没有的试验用 120 h（再由“自动截断”按 wrfout 截）。
  function presetEndMinutes(experiment, endHour, endHoursByExperiment) {
    const blank = endHour === undefined || endHour === null || String(endHour).trim() === "";
    if (!blank) return Math.round(requiredNumber(endHour, "套餐结束时间") * 60);
    const hours = Number((endHoursByExperiment || {})[experiment]);
    return Math.round((Number.isFinite(hours) && hours >= 0 ? hours : DEFAULT_END_HOURS) * 60);
  }

  function buildPresetPayloads(values, groups, startHour, endHour, parts = 1, dimensionOf = null, endHoursByExperiment = null, merge = true) {
    const start = Math.round(requiredNumber(startHour, "套餐起始时间") * 60);
    if (start < 0) throw new Error("套餐起始时间不能为负");
    const experiments = [...new Set(splitList(values.experiments ?? values.experiment))];
    if (!experiments.length) throw new Error("请至少选择一个试验");
    const ends = Object.fromEntries(experiments.map(experiment => [experiment, presetEndMinutes(experiment, endHour, endHoursByExperiment)]));
    if (Object.values(ends).every(value => value < start)) throw new Error("套餐时间需要 0 ≤ 起始 ≤ 结束（小时）");
    const entries = [];
    for (const request of mergePresetGroups(groups, dimensionOf, merge)) {
      // 合并后的名字只留各组名的主干（去掉括号里的说明），如“各层中心 + 涡旋倾斜 + 内核区风切变 + 雷达反射率”。
      const label = request.labels.map(name => String(name).replace(/[（(][^）)]*[）)]/g, "").trim() || name).join(" + ");
      for (const experiment of experiments) {
        const end = ends[experiment];
        const base = { experiment, group: request.ids.join("+") };
        if (end < start) {
          // 起始时间晚于这个试验的模拟时长：不发请求，在计划里显示“跳过”。
          entries.push({ ...base, label: `${label}（每 ${request.step} min）`, skipped: `模拟只到 ${hoursText(end)} h，早于起始时间`, payload: null });
          continue;
        }
        const ranges = splitTimeRange(start, end, request.step, parts);
        ranges.forEach((range, index) => {
          const chunk = ranges.length > 1 ? ` · 第 ${index + 1}/${ranges.length} 段 ${hoursText(range.start)}–${hoursText(range.end)} h` : ` · ${hoursText(range.start)}–${hoursText(range.end)} h`;
          let payload;
          try {
            payload = buildProcessingPayload({
              ...values, experiments: experiment, variables: request.variables.join(","), heights_m: compactIntegers(request.heights),
              times_minutes: `${range.start}:${range.end}:${request.step}`, label: `${label}（每 ${request.step} min）${chunk}`,
            });
          } catch (error) {
            throw new Error(`${label}：${error.message}`);
          }
          entries.push({ ...base, label: payload.label, payload: payloadForExperiment(payload, experiment) });
        });
      }
    }
    return entries;
  }

  function filterInventory(items, filters) {
    const variable = String(filters.variable || "").trim().toLowerCase();
    return items.filter(item =>
      (!filters.dataset || (item.dataset || "v3") === filters.dataset) &&
      (!filters.profile_id || item.profile_id === filters.profile_id) &&
      (!filters.experiment || item.experiment === filters.experiment) &&
      (!filters.domain || String(item.domain) === String(filters.domain)) &&
      (!variable || item.variable.toLowerCase().includes(variable))
    );
  }

  function paginateInventory(items, page, size = 50) {
    const pageCount = Math.max(1, Math.ceil(items.length / size));
    const current = Math.max(1, Math.min(pageCount, page));
    return { items: items.slice((current - 1) * size, current * size), total: items.length, page: current, pageCount };
  }

  // 绘图成员键：base 方案用试验名，其他方案写成“试验@方案”。
  function memberKey(experiment, scheme) {
    return !scheme || scheme === "base" ? String(experiment) : `${experiment}@${scheme}`;
  }

  // 后端每次只处理一个试验：多选时为每个试验生成独立请求。
  function payloadForExperiment(payload, experiment) {
    const { experiments, ...rest } = payload;
    return { ...rest, experiment: String(experiment) };
  }

  // “其他可处理变量”默认不列 catalog.hidden_variables（内部辅助场、别名、网格坐标）；已选的、勾选“显示全部”时照常显示。
  function visiblePickerVariables(variables, hidden, selected = [], showAll = false) {
    if (showAll) return [...variables];
    const hide = new Set(hidden || []);
    const keep = new Set(selected || []);
    return variables.filter(name => !hide.has(name) || keep.has(name));
  }

  function filterProcessingVariables(variables, query) {
    const needle = String(query || "").trim().toLowerCase();
    return variables.filter(name => name.toLowerCase().includes(needle));
  }

  function toggleProcessingVariable(text, name) {
    const selected = [...new Set(String(text || "").split(/[,|\n]+/).map(value => value.trim()).filter(Boolean))];
    return (selected.includes(name) ? selected.filter(value => value !== name) : [...selected, name]).join(",");
  }

  function trimNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
  }

  // 已完成切片的时间/高度范围，例如 “0–102 h · 103 个 · 每 1 h”。
  function formatRange(range) {
    if (!range || !range.count) return "—";
    const unit = range.unit ? ` ${range.unit}` : "";
    if (range.count === 1) return `${trimNumber(range.min)}${unit}`;
    const step = range.step ? ` · 每 ${trimNumber(range.step)}${unit}` : "";
    return `${trimNumber(range.min)}–${trimNumber(range.max)}${unit} · ${range.count} 个${step}`;
  }

  function formatSpatial(spatial) {
    if (!spatial) return "—";
    if (spatial.radius !== undefined) {
      const extent = spatial.radius_max_km != null ? `（0–${trimNumber(spatial.radius_max_km)} km` + (spatial.radius_step_km ? `，每 ${trimNumber(spatial.radius_step_km)} km）` : "）") : "";
      return `方位 ${spatial.azimuth} × 半径 ${spatial.radius}${extent}`;
    }
    if (spatial.y !== undefined) return `y ${spatial.y} × x ${spatial.x} 格点`;
    if (spatial.component !== undefined) return `${spatial.component} 个分量`;
    return "—";
  }

  // 没有 dataset 字段的旧条目仍用四段写法（后端视为 processed_v3）。
  function inventoryKey(item) {
    const key = [item.profile_id, item.experiment, item.domain, item.variable];
    return (item.dataset ? [item.dataset, ...key] : key).join("|");
  }

  // 变量说明浮窗的各行（[标签, 内容]），缺项不显示。
  function describeVariable(info) {
    if (!info) return [["说明", "没有找到这个变量的说明"]];
    const rows = [
      ["单位", info.units],
      ["维度", info.dimension_label],
      ["含义", info.meaning],
      ["算法", info.method],
      ["依赖", (info.dependencies || []).join("、")],
      ["分类", info.category],
    ];
    return rows.filter(([, text]) => text);
  }

  const HISTORY_STATUS = { queued: "排队中", running: "运行中", complete: "已完成", failed: "失败", interrupted: "已中断", canceled: "已取消", cancelled: "已取消", cancelling: "取消中" };
  function historyStatusLabel(status) {
    return HISTORY_STATUS[status] || status || "未知";
  }

  // 2026-11-03 任务状态对应的底色类名：绿 = 已完成，红 = 出错（失败、网页重启时被中断），黄 = 排队等待，蓝 = 运行中；
  // 已取消等其余状态不上色。用于右侧任务下拉列表（app.js 的 progressStatusClass 与此一致，样式见 styles.css 的 .job-status-*）。
  const STATUS_TONE = { complete: "complete", failed: "failed", interrupted: "failed", queued: "queued", pending: "queued", running: "running" };
  function jobStatusClass(status) {
    return STATUS_TONE[status] ? `job-status-${STATUS_TONE[status]}` : "";
  }

  function summarizeHistory(jobs) {
    const counts = {};
    for (const job of jobs) counts[job.status] = (counts[job.status] || 0) + 1;
    const active = (counts.running || 0) + (counts.queued || 0);
    const parts = [`共 ${jobs.length} 个`];
    if (active) parts.push(`进行中 ${active}`);
    if (counts.failed) parts.push(`失败 ${counts.failed}`);
    return { text: parts.join(" · "), active };
  }

  function buildPlotTransfer(state, layout = "") {
    const variable = splitList(state.variables)[0];
    if (!variable) throw new Error("请先选择要绘制的变量");
    const polar = variable.endsWith("_polar");
    const scalar = layout.includes("scalar");
    const times = parseIntegerSelection(state.times_minutes, MAX_TIMES).map(value => value / 60);
    const heights = parseIntegerSelection(state.heights_m, 100);
    const result = {
      name: polar ? variable.slice(0, -6) : variable,
      fig_type: scalar ? 5 : (polar ? 1 : 0),
      plot_type: scalar ? "line" : "contourf",
      time: scalar ? times.join(",") : String(times[0]),
      height: scalar ? "" : (polar ? heights.join(",") : String(heights[0] ?? "")),
      use_polar: polar,
    };
    if (polar) {
      result.polar_max_radius_km = requiredNumber(state.radius_km, "极坐标半径");
      result.polar_radius_resolution_km = requiredNumber(state.radius_resolution_km, "极坐标径向间隔");
      // 网页请求使用km；后端再按径向分辨率转换为数组索引。
      result.radius = `0,${result.polar_max_radius_km}`;
    }
    return result;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { presetEndMinutes, compactIntegers, buildPresetPayloads, mergePresetGroups, splitTimeRange, visiblePickerVariables, splitList, evaluateIntegerExpression, parseIntegerSelection, buildProcessingPayload, buildPlotTransfer, payloadForExperiment, filterInventory, paginateInventory, filterProcessingVariables, toggleProcessingVariable, formatRange, formatSpatial, inventoryKey, describeVariable, historyStatusLabel, jobStatusClass, summarizeHistory, memberKey };
  }
  if (typeof document === "undefined") return;

  const form = document.querySelector("#processingForm");
  if (!form) return;
  const profileSelect = document.querySelector("#dataProfile");
  const experimentInput = document.querySelector("#dataExperiments");
  const experimentChoices = document.querySelector("#dataExperimentChoices");
  const experimentCount = document.querySelector("#dataExperimentCount");
  const statusLine = document.querySelector("#processingStatus");
  const variableList = document.querySelector("#dataVariableList");
  const variableInput = form.elements.namedItem("variables");
  const variableSearch = document.querySelector("#dataVariableSearch");
  const variableChoices = document.querySelector("#dataVariableChoices");
  const variableCount = document.querySelector("#dataVariableCount");
  const inventorySelected = document.querySelector("#inventorySelected");
  const planSummary = document.querySelector("#processingPlan");
  const historyPanel = document.querySelector("#processingHistory");
  const historyCount = document.querySelector("#processingHistoryCount");
  const experimentAddToggle = document.querySelector("#dataExperimentAddToggle");
  const experimentAddPanel = document.querySelector("#dataExperimentAdd");
  const experimentNewName = document.querySelector("#dataExperimentNewName");
  const experimentNewPath = document.querySelector("#dataExperimentNewPath");
  const experimentAddSubmit = document.querySelector("#dataExperimentAddSubmit");
  const experimentAddStatus = document.querySelector("#dataExperimentAddStatus");
  const experimentCustomList = document.querySelector("#dataExperimentCustomList");
  const variableTooltip = document.createElement("div");
  variableTooltip.className = "var-tooltip";
  variableTooltip.hidden = true;
  variableTooltip.setAttribute("role", "tooltip");
  document.body.appendChild(variableTooltip);
  const inventoryDetails = new Map();
  const runButton = document.querySelector("#runProcessing");
  // 2026-11-04 同时运行任务数：网页上直接改，立即生效并保存（PUT /api/processing/v3/settings）。
  const workersSelect = document.querySelector("#processingWorkers");
  const workersStatus = document.querySelector("#processingWorkersStatus");
  workersSelect?.addEventListener("change", async () => {
    const workers = Number(workersSelect.value);
    try {
      const result = await request("/api/processing/v3/settings", { method: "PUT", body: { workers } });
      workersSelect.value = String(result.workers);
      if (catalog) catalog.processing_workers = result.workers;
      if (workersStatus) workersStatus.textContent = `已改为 ${result.workers}，立即生效（当前运行 ${result.running} 个）`;
    } catch (error) {
      if (catalog?.processing_workers) workersSelect.value = String(catalog.processing_workers);
      if (workersStatus) workersStatus.textContent = `没有改成：${error.message}`;
    }
  });
  const plotButton = document.querySelector("#plotFromData");
  const inventorySummary = document.querySelector("#inventorySummary");
  const inventoryList = document.querySelector("#inventoryList");
  const inventoryPageLabel = document.querySelector("#inventoryPage");
  const inventoryPrevious = document.querySelector("#inventoryPrevious");
  const inventoryNext = document.querySelector("#inventoryNext");
  const inventoryFilters = {
    profile_id: document.querySelector("#inventoryProfile"),
    experiment: document.querySelector("#inventoryExperiment"),
    domain: document.querySelector("#inventoryDomain"),
    variable: document.querySelector("#inventoryVariable"),
  };
  const centerSchemeSelect = document.querySelector("#dataCenterScheme");
  const centerSchemeHint = document.querySelector("#dataCenterSchemeHint");
  // 2026-11-02 数据集与处理套餐
  const datasetSelect = document.querySelector("#dataDataset");
  const datasetHint = document.querySelector("#dataDatasetHint");
  const presetSelect = document.querySelector("#dataPreset");
  const presetStart = document.querySelector("#dataPresetStart");
  const presetEnd = document.querySelector("#dataPresetEnd");
  const presetDescription = document.querySelector("#dataPresetDescription");
  const presetGroupsEl = document.querySelector("#dataPresetGroups");
  const presetEndHint = document.querySelector("#dataPresetEndHint");
  const runPresetButton = document.querySelector("#runPreset");
  const presetStatusLine = document.querySelector("#presetStatus");
  const variableShowAll = document.querySelector("#dataVariableShowAll");
  let planSource = "single";  // 当前“处理计划”来自哪一块：preset（处理套餐）或 single（其他可处理变量）
  let presetMerge = true;  // 当前套餐是否合并同步长同高度的组（processing_presets.json 的 merge_groups）
  const dataRootChoices = document.querySelector("#dataRootChoices");
  inventoryFilters.dataset = document.querySelector("#inventoryDataset");
  let presetGroups = [];
  let renderedPresetId = null;
  let catalogRequestId = 0;
  let catalog = null;
  let currentPlans = null;
  let planRequestId = 0;
  let currentSummary = null;
  let inventoryItems = [];
  let inventoryPage = 1;
  let selectedInventory = null;
  let coverageRequestId = 0;

  function formValues() {
    return Object.fromEntries(new FormData(form).entries());
  }

  async function request(url, options = {}) {
    const method = options.method || "GET";
    if (window.WRFWebVisAccess) await window.WRFWebVisAccess.assertWrite(method);
    else if (!['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase())) throw new Error("写操作被拒绝：权限状态不可用");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 120000);
    try {
      const response = await fetch(url, {
        method: options.method || "GET",
        headers: options.body ? { "Content-Type": "application/json" } : undefined,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message || `HTTP ${response.status}`);
      return body;
    } finally {
      clearTimeout(timeout);
    }
  }

  function value(name, next) {
    const input = form.elements.namedItem(name);
    if (input) input.value = String(next ?? "");
    if (name === "variables") renderVariablePicker();
    if (name === "experiments") renderExperimentPicker();
  }

  function setStatus(text, isError = false, source = planSource) {
    const line = source === "preset" && presetStatusLine ? presetStatusLine : statusLine;
    for (const other of [statusLine, presetStatusLine]) if (other && other !== line) other.textContent = "";
    line.textContent = text;
    line.className = isError ? "data-status data-warning" : "data-status";
  }

  function showError(error, source = planSource) {
    planSummary.textContent = error.message;
    setStatus(error.message, true, source);
  }

  function selectedExperiments() {
    return [...new Set(splitList(experimentInput.value))];
  }

  function setExperiments(names) {
    experimentInput.value = names.join(",");
    renderExperimentPicker();
    experimentInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // 结束时间留空时，列出所选试验各自算到几点（project_config 的模拟时长）。
  function renderPresetEndHint() {
    if (!presetEndHint || !catalog) return;
    const names = selectedExperiments();
    if (String(presetEnd?.value || "").trim()) {
      presetEndHint.textContent = `所有所选试验都算到 ${presetEnd.value} h（超过 wrfout 最后时次的部分按“自动截断”去掉）。`;
      return;
    }
    const table = catalog.experiment_end_hours || {};
    const parts = names.map(name => `${name} ${name in table ? trimNumber(Number(table[name])) : `${DEFAULT_END_HOURS}（未登记，按 wrfout 截断）`} h`);
    presetEndHint.textContent = names.length ? `结束时间留空：按各试验模拟时长算到 ${parts.join("、")}。` : "";
  }

  function renderExperimentPicker() {
    renderPresetEndHint();
    if (!catalog) return;
    const selected = new Set(selectedExperiments());
    experimentCount.textContent = `已选 ${selected.size} / ${catalog.experiments.length} 个`;
    const details = new Map((catalog.experiment_details || []).map(item => [item.name, item]));
    experimentChoices.replaceChildren(...catalog.experiments.map(name => {
      const button = document.createElement("button");
      const detail = details.get(name);
      button.type = "button";
      button.textContent = name;
      button.className = [selected.has(name) ? "selected" : "", detail?.custom ? "custom" : "", detail && !detail.available ? "unavailable" : ""].filter(Boolean).join(" ");
      if (detail) button.title = `${detail.custom ? "自定义试验" : "固定试验（project_config.py）"}\n${detail.path}${detail.available ? "" : "\n⚠ 目录当前不可访问（外接盘未接上？）"}`;
      button.setAttribute("aria-pressed", String(selected.has(name)));
      button.addEventListener("click", () => setExperiments(splitList(toggleProcessingVariable(experimentInput.value, name))));
      return button;
    }));
  }

  function renderCustomExperiments() {
    if (!catalog || !experimentCustomList) return;
    const custom = (catalog.experiment_details || []).filter(item => item.custom);
    if (!custom.length) {
      experimentCustomList.textContent = "还没有自定义试验。";
      return;
    }
    experimentCustomList.replaceChildren(...custom.map(item => {
      const row = document.createElement("div");
      const text = document.createElement("span");
      text.textContent = `${item.name} → ${item.path}${item.available ? "" : "（目录当前不可访问）"}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.writeOnly = "true";
      remove.textContent = "移除登记";
      remove.title = "只从网页试验列表移除，不删除 wrfout 和已处理数据";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`从试验列表移除 ${item.name}？\n只移除网页登记，不会删除 wrfout 或已处理的数据。`)) return;
        try {
          await request(`/api/data/experiments/${encodeURIComponent(item.name)}`, { method: "DELETE" });
          experimentAddStatus.textContent = `已移除 ${item.name} 的登记。`;
          experimentAddStatus.className = "data-status";
          await loadCatalog();
        } catch (error) {
          experimentAddStatus.textContent = error.message;
          experimentAddStatus.className = "data-status data-warning";
        }
      });
      row.append(text, remove);
      return row;
    }));
  }

  async function addExperiment() {
    const name = experimentNewName.value.trim();
    const path = experimentNewPath.value.trim();
    experimentAddStatus.className = "data-status";
    experimentAddStatus.textContent = "正在检查目录里的 wrfout 文件…";
    experimentAddSubmit.disabled = true;
    try {
      const added = await request("/api/data/experiments", { method: "POST", body: { name, path } });
      experimentAddStatus.textContent = `已添加 ${added.name}：找到 ${added.domains.map(domain => `d${String(domain).padStart(2, "0")}`).join("、")} 的 wrfout。已自动选中，可直接预览依赖计划。`;
      experimentNewName.value = "";
      experimentNewPath.value = "";
      await loadCatalog();
      setExperiments([added.name]);
    } catch (error) {
      experimentAddStatus.textContent = error.message;
      experimentAddStatus.className = "data-status data-warning";
    } finally {
      experimentAddSubmit.disabled = false;
    }
  }

  function showVariableTooltip(button, name) {
    const info = catalog?.variable_info?.[name];
    const title = document.createElement("strong");
    title.textContent = name;
    const list = document.createElement("dl");
    for (const [label, text] of describeVariable(info)) {
      const term = document.createElement("dt"); term.textContent = label;
      const value = document.createElement("dd"); value.textContent = text;
      list.append(term, value);
    }
    variableTooltip.replaceChildren(title, list);
    variableTooltip.hidden = false;
    const box = button.getBoundingClientRect();
    const tip = variableTooltip.getBoundingClientRect();
    const left = Math.max(12, Math.min(window.innerWidth - tip.width - 12, box.left));
    const below = box.bottom + 8;
    const top = below + tip.height > window.innerHeight - 8 ? Math.max(8, box.top - tip.height - 8) : below;
    variableTooltip.style.left = `${left}px`;
    variableTooltip.style.top = `${top}px`;
  }

  function hideVariableTooltip() {
    variableTooltip.hidden = true;
  }

  function resetRunButton() {
    for (const button of [runButton, runPresetButton]) {
      if (!button) continue;
      button.disabled = true;
      button.textContent = "提交处理";
    }
  }

  function renderVariablePicker() {
    if (!catalog) return;
    const selected = new Set(String(variableInput.value || "").split(/[,|\n]+/).map(name => name.trim()).filter(Boolean));
    const visible = visiblePickerVariables(catalog.variables, catalog.hidden_variables, [...selected], Boolean(variableShowAll?.checked));
    const names = filterProcessingVariables(visible, variableSearch.value);
    const hiddenCount = catalog.variables.length - visible.length;
    variableCount.textContent = `显示 ${names.length} / ${catalog.variables.length} 个${hiddenCount ? `（${hiddenCount} 个内部变量未列出）` : ""} · 已选 ${selected.size} 个`;
    const buttons = names.map(name => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = name;
      button.className = selected.has(name) ? "selected" : "";
      button.setAttribute("aria-pressed", String(selected.has(name)));
      const info = catalog.variable_info?.[name];
      if (info?.meaning) button.setAttribute("aria-description", `${info.units || ""} ${info.meaning}`);
      button.addEventListener("click", () => {
        variableInput.value = toggleProcessingVariable(variableInput.value, name);
        variableInput.dispatchEvent(new Event("input", { bubbles: true }));
      });
      button.addEventListener("mouseenter", () => showVariableTooltip(button, name));
      button.addEventListener("focus", () => showVariableTooltip(button, name));
      button.addEventListener("mouseleave", hideVariableTooltip);
      button.addEventListener("blur", hideVariableTooltip);
      return button;
    });
    hideVariableTooltip();
    variableChoices.replaceChildren(...buttons);
    if (!buttons.length) variableChoices.textContent = "没有匹配的变量";
  }

  function optionValue(options, key, domain, fallback) {
    if (key === "radius_km") return options.radius_km_by_domain?.[domain] ?? options.radius_km ?? fallback;
    if (key === "center_search_radius_km") return options.center_options_by_domain?.[domain]?.search_radius_km ?? fallback;
    return options[key] ?? fallback;
  }

  function fillOptions() {
    if (!catalog) return;
    const domain = Number(formValues().domain || 3);
    const selected = catalog.profiles.find(item => item.id === profileSelect.value);
    const options = selected?.options || catalog.default_options;
    const defaults = catalog.default_options || {};
    for (const key of ["radius_km", "radius_resolution_km", "center_search_radius_km", "theta_environment_domain", "shear_source_domain", "shear_radius_inner_km", "shear_radius_outer_km", "asymmetry_radius_km", "dbz_asymmetry_height_m"]) {
      value(key, optionValue(options, key, domain, optionValue(defaults, key, domain, "")));
    }
    const ring = options.theta_environment_ring_km || defaults.theta_environment_ring_km || [400, 500];
    value("theta_ring_inner_km", ring[0]);
    value("theta_ring_outer_km", ring[1]);
  }

  function currentDataset() {
    return datasetSelect?.value || "post";
  }

  function datasetLabel(id) {
    return (catalog?.datasets || []).find(item => item.id === id)?.label || (id === "v3" ? "processed_v3（旧数据）" : String(id || "processed_v3"));
  }

  function renderDatasets() {
    if (!datasetSelect || !catalog?.datasets?.length) return;
    const previous = datasetSelect.value;
    datasetSelect.replaceChildren(...catalog.datasets.map(item => new Option(item.label, item.id)));
    datasetSelect.value = catalog.datasets.some(item => item.id === previous) ? previous : catalog.dataset;
    const info = catalog.datasets.find(item => item.id === datasetSelect.value);
    if (datasetHint && info) {
      datasetHint.textContent = `写入 ${info.root}${info.exists ? "" : "（目录还不存在，第一次提交处理时自动建立）"}。` +
        (info.id === "post" ? " post 只放你在网页上亲自选择处理的数据，与 processed_v3 互不复用（中心等依赖在 post 里重新计算）。" : " 这里有很多以前让 AI 算的数据。");
    }
    if (dataRootChoices) {
      dataRootChoices.replaceChildren(...catalog.datasets.map(item => { const option = new Option(item.label, item.root); option.textContent = item.label; return option; }));
    }
  }

  function renderPresets() {
    if (!presetSelect || !presetGroupsEl) return;
    const presets = catalog?.processing_presets || [];
    if (catalog?.processing_presets_error) {
      presetGroupsEl.textContent = catalog.processing_presets_error;
      presetGroupsEl.className = "data-preset-groups data-preset-error";
      return;
    }
    presetGroupsEl.className = "data-preset-groups";
    const previous = presetSelect.value;
    presetSelect.replaceChildren(...presets.map(item => new Option(`${item.id} · ${item.label}`, item.id)));
    if (!presets.length) { presetGroupsEl.textContent = "processing_presets.json 里还没有套餐。"; return; }
    presetSelect.value = presets.some(item => item.id === previous) ? previous : presets[0].id;
    if (renderedPresetId !== presetSelect.value) renderPresetGroups(presets.find(item => item.id === presetSelect.value));
  }

  // 套餐各组的可编辑行：只改本次请求，不写回 JSON。
  function renderPresetGroups(preset) {
    renderedPresetId = preset?.id || null;
    presetMerge = preset?.merge_groups !== false;
    presetDescription.textContent = (preset?.description || "") + (preset ? (presetMerge ? " 时间步长和高度相同的组合成一个请求。" : " 各组分别作为一个请求运行。") : "");
    presetGroups = (preset?.groups || []).map(group => ({
      id: group.id, label: group.label, enabled: group.enabled,
      variables: group.variables.join(","), time_step_minutes: group.time_step_minutes, heights: compactIntegers(group.heights_m),
    }));
    presetGroupsEl.replaceChildren(...presetGroups.map(group => {
      const row = document.createElement("div");
      row.className = `data-preset-group${group.enabled ? "" : " disabled"}`;
      const toggle = document.createElement("label"); toggle.className = "data-preset-toggle";
      const box = document.createElement("input"); box.type = "checkbox"; box.checked = group.enabled;
      box.addEventListener("change", () => { group.enabled = box.checked; row.classList.toggle("disabled", !box.checked); });
      toggle.append(box, document.createTextNode(group.label));
      const field = (labelText, key, type, placeholder = "") => {
        const label = document.createElement("label"); label.textContent = labelText;
        const input = document.createElement("input"); input.type = type; input.value = String(group[key] ?? "");
        if (type === "number") { input.min = "1"; input.step = "1"; }
        input.placeholder = placeholder;
        input.addEventListener("input", () => { group[key] = type === "number" ? Number(input.value) : input.value; });
        label.appendChild(input);
        return label;
      };
      row.append(
        toggle,
        field("变量（逗号分隔）", "variables", "text"),
        field("步长 (min)", "time_step_minutes", "number"),
        field("高度 (m)", "heights", "text", "标量/地面变量留空"),
      );
      return row;
    }));
  }

  async function loadCatalog() {
    const requestId = ++catalogRequestId;
    const next = await request(`/api/data/catalog?dataset=${encodeURIComponent(currentDataset())}`);
    if (requestId !== catalogRequestId) return;
    catalog = next;
    renderDatasets();
    renderPresets();
    const previousExperiments = selectedExperiments().filter(name => catalog.experiments.includes(name));
    const previousProfile = profileSelect.value;
    profileSelect.replaceChildren(...catalog.profiles.map(profile => new Option(profileLabel(profile.id), profile.id)));
    experimentInput.value = (previousExperiments.length ? previousExperiments : catalog.experiments.slice(0, 1)).join(",");
    renderExperimentPicker();
    renderCustomExperiments();
    if (catalog.profiles.some(item => item.id === previousProfile)) profileSelect.value = previousProfile;
    variableList.replaceChildren(...catalog.variables.map(name => new Option(name)));
    renderVariablePicker();
    if (centerSchemeSelect) {
      const previousScheme = centerSchemeSelect.value || "base";
      centerSchemeSelect.replaceChildren(
        ...(catalog.center_schemes || []).map(item => {
          const option = new Option(`${item.id} · ${item.label}`, item.id);
          option.title = item.description || "";
          return option;
        }),
        new Option("不按方案（沿用参数配置里的中心参数）", ""),
      );
      centerSchemeSelect.value = [...centerSchemeSelect.options].some(option => option.value === previousScheme) ? previousScheme : "base";
    }
    fillOptions();
    updateCenterSchemeFields();
    if (workersSelect && catalog.processing_workers) workersSelect.value = String(catalog.processing_workers);
    if (inventoryItems.length) { populateInventoryFilters(); renderInventory(); }  // 清单先到时补上参数配置的名字
  }

  function updateCenterSchemeFields() {
    if (!centerSchemeSelect) return;
    const scheme = centerSchemeSelect.value;
    const radius = form.elements.namedItem("center_search_radius_km");
    if (radius) radius.disabled = Boolean(scheme);
    const info = (catalog?.center_schemes || []).find(item => item.id === scheme);
    if (centerSchemeHint) {
      centerSchemeHint.textContent = scheme
        ? `${info?.description || ""} 依赖中心的变量（vt1_polar、tilt、rmw…）${scheme === "base" ? `写在数据集根目录（${currentDataset() === "v3" ? "processed_v3" : currentDataset()}）` : `单独写到 ${currentDataset() === "v3" ? "processed_v3" : currentDataset()}/centers/${scheme}/`}；与中心无关的变量（ua、theta…）各方案共用。`
        : "不按方案：中心参数取自参数配置和下方“中心搜索半径”。";
    }
  }

  // 参数配置的可读名字：编号 p-xxxx 是参数的哈希指纹，后面跟“与默认不同的参数”（由后端 catalog 给出）。
  function profileLabel(id) {
    if (id === "default") return "默认配置";
    const label = (catalog?.profiles || []).find(item => item.id === id)?.label;
    return label ? `${label}（${String(id).slice(0, 10)}…）` : String(id);
  }

  function inventoryFilterValues() {
    return Object.fromEntries(Object.entries(inventoryFilters).map(([key, element]) => [key, element.value]));
  }

  function populateInventoryFilters() {
    for (const [key, label] of [["dataset", "全部数据集"], ["profile_id", "全部配置"], ["experiment", "全部试验"], ["domain", "全部 domain"]]) {
      const element = inventoryFilters[key];
      if (!element) continue;
      const previous = element.value;
      const values = [...new Set(inventoryItems.map(item => String(key === "dataset" ? item.dataset || "v3" : item[key])))].sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true }));
      element.replaceChildren(new Option(label, ""), ...values.map(value => new Option(key === "profile_id" ? profileLabel(value) : key === "dataset" ? datasetLabel(value) : value, value)));
      if (values.includes(previous)) element.value = previous;
    }
  }

  function renderInventory() {
    const filtered = filterInventory(inventoryItems, inventoryFilterValues());
    const page = paginateInventory(filtered, inventoryPage);
    inventoryPage = page.page;
    inventorySummary.textContent = `共有 ${inventoryItems.length} 个变量文件 · 当前筛选 ${page.total} 个。时间、高度列是已经算好的范围；“统计中…”表示服务器正在后台读完成标记（大文件第一次要十几秒，之后有缓存）。点击变量名查看详细信息。`;
    inventoryPageLabel.textContent = `第 ${page.page} / ${page.pageCount} 页 · 每页 50 项`;
    inventoryPrevious.disabled = page.page <= 1;
    inventoryNext.disabled = page.page >= page.pageCount;
    const table = document.createElement("table");
    table.className = "inventory-table";
    const header = document.createElement("tr");
    for (const title of ["数据集", "配置", "试验", "domain", "变量", "数据形态", "时间（已完成）", "高度（已完成）", "水平维度", "完成切片", "大小", "修改时间"]) {
      const cell = document.createElement("th"); cell.textContent = title; header.appendChild(cell);
    }
    table.appendChild(header);
    for (const item of page.items) {
      const row = document.createElement("tr");
      if (selectedInventory && inventoryKey(selectedInventory) === inventoryKey(item)) row.className = "selected";
      for (const text of [item.dataset === "v3" || !item.dataset ? "processed_v3" : item.dataset, item.profile_id === "default" ? "默认配置" : profileLabel(item.profile_id), item.experiment, `d${String(item.domain).padStart(2, "0")}`]) {
        const cell = document.createElement("td"); cell.textContent = text; row.appendChild(cell);
      }
      const variableCell = document.createElement("td");
      const button = document.createElement("button"); button.type = "button"; button.textContent = item.variable;
      button.title = item.path;
      button.addEventListener("click", () => selectInventoryItem(item).catch(error => { inventorySummary.textContent = error.message; }));
      variableCell.appendChild(button); row.appendChild(variableCell);
      const detailCells = Array.from({ length: 5 }, () => {
        const cell = document.createElement("td"); cell.className = "dim-cell"; cell.textContent = "…"; row.appendChild(cell); return cell;
      });
      row.dataset.key = inventoryKey(item);
      fillInventoryDetails(detailCells, inventoryDetails.get(`${row.dataset.key}@${item.modified_at}`));
      for (const text of [`${(item.size_bytes / 1024 / 1024).toFixed(1)} MiB`, new Date(item.modified_at).toLocaleString("zh-CN")]) {
        const cell = document.createElement("td"); cell.textContent = text; row.appendChild(cell);
      }
      table.appendChild(row);
    }
    inventoryList.replaceChildren(table);
    loadInventoryDetails(page.items, table).catch(() => {});
  }

  function fillInventoryDetails(cells, detail) {
    if (!detail) return;
    if (detail.error) {
      cells[0].textContent = detail.error;
      cells[0].className = "dim-cell dim-error";
      for (const cell of cells.slice(1)) cell.textContent = "—";
      return;
    }
    const pending = detail.completed_pending ? "统计中…" : (detail.completed_error ? "统计失败" : "—");
    const heightText = detail.file_height_count
      ? (detail.heights_m ? formatRange(detail.heights_m) : pending)
      : (detail.has_lowest_level ? "最低模式层" : "地面/单层");
    const values = [
      `${detail.layout_label}${detail.units ? ` · ${detail.units}` : ""}`,
      formatRange(detail.time_hours),
      heightText,
      formatSpatial(detail.spatial),
      detail.completed_slices == null ? pending : `${detail.completed_slices} / ${detail.total_slots}`,
    ];
    values.forEach((text, index) => { cells[index].textContent = text; });
    cells[0].title = `data 维度：${detail.dims.map(([name, size]) => `${name}=${size}`).join(" × ")}`;
    cells[4].title = "已完成切片 / 文件里预留的 时次×高度 格子数（新文件预留 66 个标准高度，未算的高度也计入分母）";
  }

  // 2026-11-04：同一组文件的摘要请求还没回来时不再重复发（连点变量名曾一次发出 7 个相同请求）。
  const detailRequests = new Map();
  function requestDetails(query, options) {
    const key = String(query);
    if (!detailRequests.has(key)) {
      detailRequests.set(key, request(`/api/data/inventory/details?${key}`, options).finally(() => detailRequests.delete(key)));
    }
    return detailRequests.get(key);
  }

  // 先拿到坐标信息（很快）；完成标记由服务器后台统计，“统计中…”的行每 3 秒再问一次。
  async function loadInventoryDetails(items, table) {
    let missing = items.filter(item => !inventoryDetails.has(`${inventoryKey(item)}@${item.modified_at}`));
    while (missing.length && table.isConnected) {
      const pending = [];
      for (let start = 0; start < missing.length; start += 25) {
        const chunk = missing.slice(start, start + 25);
        const query = new URLSearchParams();
        for (const item of chunk) query.append("key", inventoryKey(item));
        const response = await requestDetails(query, { timeoutMs: 180000 });
        if (!table.isConnected) return;
        response.items.forEach((detail, index) => {
          const item = chunk[index];
          if (detail.completed_pending) pending.push(item);
          else inventoryDetails.set(`${inventoryKey(item)}@${item.modified_at}`, detail);
          const row = [...table.querySelectorAll("tr[data-key]")].find(entry => entry.dataset.key === inventoryKey(item));
          if (row) fillInventoryDetails([...row.querySelectorAll("td.dim-cell")], detail);
          if (selectedInventory && inventoryKey(selectedInventory) === inventoryKey(item)) {
            currentSummary = detail;
            renderSelectedFile(item, detail);
          }
        });
      }
      missing = pending;
      if (missing.length) await new Promise(resolve => setTimeout(resolve, 3000));
    }
  }

  async function loadInventory() {
    const inventory = await request("/api/data/inventory");
    inventoryItems = inventory.items;
    populateInventoryFilters();
    renderInventory();
  }

  async function selectInventoryItem(item) {
    selectedInventory = item;
    renderInventory();
    const itemDataset = item.dataset || "v3";
    if (datasetSelect && currentDataset() !== itemDataset && [...datasetSelect.options].some(option => option.value === itemDataset)) {
      datasetSelect.value = itemDataset;
      await loadCatalog();
    }
    const registered = Boolean(catalog?.experiments.includes(item.experiment) && catalog?.variables.includes(item.variable) && catalog?.profiles.some(profile => profile.id === item.profile_id));
    currentPlans = null;
    planRequestId++;
    resetRunButton();
    if (registered) {
      value("experiments", item.experiment);
      value("domain", item.domain);
      value("profile_id", item.profile_id);
      value("variables", item.variable);
      fillOptions();
      planSummary.textContent = "已选择清单变量；如需处理，请预览依赖计划。";
    } else {
      planSummary.textContent = "该历史变量或试验未登记，仅支持查看；处理提交仍需使用已登记的试验和变量。";
    }
    await showSelectedFile(item, registered);
  }

  async function showSelectedFile(item, registered) {
    currentSummary = null;
    plotButton.disabled = true;
    inventorySelected.hidden = false;
    inventorySelected.textContent = `正在读取 ${item.experiment} · ${item.variable} …`;
    const key = `${inventoryKey(item)}@${item.modified_at}`;
    let detail = inventoryDetails.get(key);
    if (!detail) {
      detail = (await requestDetails(`key=${encodeURIComponent(inventoryKey(item))}`)).items[0];
      if (!detail.completed_pending) inventoryDetails.set(key, detail);
    }
    if (selectedInventory !== item) return;
    currentSummary = detail;
    plotButton.disabled = !registered || Boolean(detail.error);
    renderSelectedFile(item, detail);
  }

  function renderSelectedFile(item, detail) {
    const title = document.createElement("h4");
    title.textContent = `${datasetLabel(item.dataset || "v3")} · ${item.experiment} · d${String(item.domain).padStart(2, "0")} · ${item.variable}`;
    const list = document.createElement("dl");
    const rows = detail.error ? [["状态", detail.error]] : [
      ["数据形态", `${detail.layout_label}${detail.units ? `，单位 ${detail.units}` : ""}`],
      ["data 维度", detail.dims.map(([name, size]) => `${name} ${size}`).join(" × ")],
      ["时间", formatRange(detail.time_hours) + (detail.completed_pending ? "（文件中已有的时次；完成情况统计中）" : "（已完成的时次）")],
      ["高度", detail.file_height_count
        ? (detail.height_values ? `${detail.height_values.length} 个：${detail.height_values.join(", ")} m` : "统计中…（大文件第一次需要十几秒）")
        : (detail.has_lowest_level ? "最低模式层" : "地面/单层，没有高度维")],
      ["水平维度", formatSpatial(detail.spatial)],
      ["完成切片", detail.completed_slices == null ? "统计中…" : `${detail.completed_slices} / ${detail.total_slots}（分母是文件里预留的 时次 × 高度 格子数，新文件预留 66 个标准高度）`],
    ];
    rows.push(["文件", detail.path || item.path]);
    for (const [label, text] of rows) {
      const term = document.createElement("dt"); term.textContent = label;
      const value = document.createElement("dd"); value.textContent = text;
      list.append(term, value);
    }
    inventorySelected.replaceChildren(title, list);
  }

  // 分段后某一段整个晚于该试验 wrfout 结束时次（各试验模拟时长不同），后端会拒绝；这不是错误，直接跳过。
  function isBeyondSourceEnd(entry) {
    return Boolean(entry.skipped) || (Boolean(entry.payload?.clip_to_source_end) && /都晚于/.test(String(entry.error || "")));
  }

  function readyPlans(entries) {
    return (entries || []).filter(entry => entry.plan && !entry.plan.missing_source_minutes.length);
  }

  function formatMinutes(values, limit = 20) {
    return values.slice(0, limit).join(", ") + (values.length > limit ? ` 等 ${values.length} 个` : "");
  }

  function renderPlans(entries) {
    const ready = readyPlans(entries);
    const compute = ready.reduce((total, entry) => total + entry.plan.counts.compute, 0);
    const reuse = ready.reduce((total, entry) => total + entry.plan.counts.reuse, 0);
    const summary = document.createElement("p");
    const grouped = entries.some(entry => entry.label);
    summary.textContent = `${entries.length} 个${grouped ? "请求（试验 × 组）" : "试验"} · 可提交 ${ready.length} 个 · 待计算 ${compute} 切片 · 复用 ${reuse} 切片` +
      (entries[0]?.plan?.root ? ` · 写入 ${entries[0].plan.root}` : "") +
      (grouped ? "。同一试验的几个任务共用的中心等依赖，在预览里会各自计为“待计算”；运行时已经写好的切片直接复用，几个任务同时算到同一切片时最多重复算一次（结果相同）。" : "");
    const blocks = entries.map(entry => {
      const block = document.createElement("div");
      block.className = "plan-experiment";
      const title = document.createElement("p");
      block.appendChild(title);
      if (entry.error && isBeyondSourceEnd(entry)) {
        title.className = "hint";
        title.textContent = `${entryName(entry)}：${entry.skipped || "这一段的时次都在该试验模拟结束之后"}，跳过`;
        return block;
      }
      if (entry.error) {
        title.className = "data-warning";
        title.textContent = `${entryName(entry)}：预览失败，不会提交 · ${entry.error}`;
        return block;
      }
      const plan = entry.plan;
      const missing = plan.missing_source_minutes;
      title.className = missing.length ? "data-warning" : "data-ok";
      const clipped = plan.clipped_minutes || [];
      title.textContent = `${entryName(entry)} · 配置 ${plan.profile_id}${plan.center_scheme ? ` · 中心方案 ${plan.center_scheme}` : ""} · 待计算 ${plan.counts.compute} · 复用 ${plan.counts.reuse} · ${plan.source_index_skipped ? "无需原始输入，已跳过源索引扫描" : `来源时次 ${plan.source_minutes.length} 个`} · ` +
        (clipped.length ? `wrfout 到 ${plan.source_end_minute} min 结束，已跳过之后的 ${clipped.length} 个时次 · ` : "") +
        (missing.length ? `缺少 wrfout 时次：${formatMinutes(missing)} min，此试验不会提交` : (plan.source_index_skipped ? "缓存依赖检查通过" : "源文件检查通过"));
      const details = document.createElement("details");
      const label = document.createElement("summary"); label.textContent = `查看 ${plan.tasks.length} 个依赖节点`;
      const list = document.createElement("pre");
      list.textContent = plan.tasks.slice(0, 200).map(item => `${item.status === "compute" ? "计算" : "复用"} ${item.variable} · ${item.time_minutes} min · ${item.height_m ?? "地面"} · ${item.path}`).join("\n") + (plan.tasks.length > 200 ? `\n…还有 ${plan.tasks.length - 200} 项；完整计划由服务端保存用于提交校验` : "");
      details.append(label, list);
      block.appendChild(details);
      return block;
    });
    planSummary.replaceChildren(summary, ...blocks);
    const activeRun = planSource === "preset" && runPresetButton ? runPresetButton : runButton;
    activeRun.disabled = !ready.length;
    activeRun.textContent = ready.length > 1 ? `提交处理（${ready.length} 个任务）` : "提交处理";
    const first = entries.find(entry => entry.plan);
    plotButton.disabled = planSource !== "single" || !first || !catalog?.profiles.some(item => item.id === first.plan.profile_id);
    const blocked = entries.filter(entry => !ready.includes(entry) && !(entry.error && isBeyondSourceEnd(entry))).length;
    if (!ready.length) setStatus("没有可以提交的请求，原因见下方“处理计划”。", true);
    else if (blocked) setStatus(`${blocked} 个请求无法提交（见下方“处理计划”）；其余 ${ready.length} 个可以提交。`, true);
    else setStatus(`计划已就绪：${ready.length} 个请求，待计算 ${compute} 切片（${datasetLabel(currentDataset())}）。`);
  }

  function entryName(entry) {
    return entry.label ? `${entry.experiment} · ${entry.label}` : entry.experiment;
  }

  // prepared：处理套餐给出的 [{experiment, label, payload}]；不给时按单组表单为每个试验生成一个请求。
  async function previewPlan(prepared = null, source = "single") {
    planSource = source;
    const items = prepared || (() => {
      const base = buildProcessingPayload(formValues());
      return base.experiments.map(experiment => ({ experiment, label: "", payload: payloadForExperiment(base, experiment) }));
    })();
    const requestId = ++planRequestId;
    currentPlans = null;
    resetRunButton();
    // 同时预览 3 个请求：不同试验多在不同硬盘上，首次扫描 wrfout 时并行更快。
    const total = items.length;
    const entries = new Array(total);
    const running = new Set();
    let next = 0;
    let finished = 0;
    const started = Date.now();
    const report = () => {
      if (requestId !== planRequestId) return;
      const seconds = Math.round((Date.now() - started) / 1000);
      const message = `正在建立依赖计划：已完成 ${finished}/${total}，进行中 ${[...running].join("、") || "—"}（${seconds} s）。首次扫描某个试验的 wrfout 约需 10–20 秒，之后会使用缓存。`;
      setStatus(message);
      planSummary.textContent = message;
    };
    const timer = setInterval(report, 1000);
    async function worker() {
      while (next < total && requestId === planRequestId) {
        const index = next++;
        if (items[index].skipped) {
          entries[index] = { ...items[index], error: items[index].skipped };
          finished++;
          continue;
        }
        const { experiment, label, payload } = items[index];
        const name = entryName(items[index]);
        running.add(name);
        report();
        try {
          const plan = await request("/api/processing/v3/plan", { method: "POST", body: payload, timeoutMs: 600000 });
          entries[index] = { experiment, label, payload, plan };
        } catch (error) {
          entries[index] = { experiment, label, payload, error: error.message };
        }
        running.delete(name);
        finished++;
        report();
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, total) }, worker));
    } finally {
      clearInterval(timer);
    }
    if (requestId !== planRequestId) return null;
    currentPlans = entries;
    renderPlans(entries);
    return entries;
  }

  async function submitJob() {
    const ready = readyPlans(currentPlans);
    if (!ready.length) throw new Error("请先预览处理计划");
    resetRunButton();
    // 2026-11-02：一次批量提交。服务器先重新核对全部计划，都通过才同时登记、启动；
    // 逐个提交时前一个任务一开始写文件，同一试验后面的计划就会被判定“数据已变化”。
    setStatus(`正在核对并提交 ${ready.length} 个处理任务…`, false);
    let response;
    try {
      response = await request("/api/processing/v3/jobs/batch", {
        method: "POST", timeoutMs: 600000,
        body: { jobs: ready.map(entry => ({ ...entry.payload, plan_hash: entry.plan.plan_hash })) },
      });
    } catch (error) {
      currentPlans = null;
      planRequestId++;
      setStatus(`没有提交：${error.message}。请重新预览后再提交。`, true);
      return;
    }
    const submitted = response.jobs.map((job, index) => ({ entry: ready[index], job }));
    currentPlans = null;
    planRequestId++;
    const queued = submitted.length > (response.workers || 1) ? `服务器同时运行 ${response.workers} 个，其余排队；` : (submitted.length > 1 ? "同时运行；" : "");
    setStatus(`已提交 ${submitted.length} 个处理任务。${queued}进度见右侧任务面板和下方历史任务。`);
    planSummary.append(` · 已提交 ${submitted.map(item => `${entryName(item.entry)}（${item.job.job_id}）`).join("、") || "无"}`);
    await loadHistory().catch(() => {});
    let completedProfile = null;
    const results = await Promise.all(submitted.map(({ entry, job }) => watchJob(job.job_id, async finished => {
      await loadHistory();
      if (finished.status === "complete") completedProfile = finished.result?.profile_id || job.profile_id;
    }, { title: `处理 ${entryName(entry)} d${entry.payload.domain}` })));
    const statuses = results.filter(Boolean).map(job => job.status);
    if (statuses.length) {
      const done = statuses.filter(status => status === "complete").length;
      setStatus(`处理任务已结束：完成 ${done} 个` + (done < statuses.length ? `，未完成 ${statuses.length - done} 个（见历史任务）` : ""), done < statuses.length);
    }
    await loadCatalog();
    if (completedProfile) {
      await loadInventory();
      profileSelect.value = completedProfile;
      fillOptions();
    }
  }

  async function loadHistory() {
    const jobs = await request("/api/jobs?kind=processing&limit=100");
    const summary = summarizeHistory(jobs);
    if (historyCount) historyCount.textContent = summary.text;
    const list = document.createElement("div");
    if (!jobs.length) list.textContent = "还没有处理任务。";
    for (const job of jobs) {
      const row = document.createElement("div"); row.className = "history-row";
      const text = document.createElement("span");
      text.textContent = `${job.title} · ${historyStatusLabel(job.status)} · ${job.id}${job.error ? ` · ${job.error}` : ""}`;
      if (job.status === "failed") text.className = "data-warning";
      const retry = document.createElement("button"); retry.type = "button"; retry.dataset.writeOnly = "true"; retry.textContent = "载入并重新预览";
      retry.addEventListener("click", async () => {
        const old = job.request || {};
        // 旧任务没有 dataset 字段：当时写的是 processed_v3。
        const oldDataset = old.dataset || "v3";
        if (datasetSelect && currentDataset() !== oldDataset) { datasetSelect.value = oldDataset; await loadCatalog(); }
        value("experiments", old.experiment);
        for (const key of ["domain", "profile_id"]) value(key, old[key]);
        value("variables", (old.variables || []).join(","));
        value("times_minutes", (old.times_minutes || []).join(","));
        value("heights_m", (old.heights_m || []).join(","));
        fillOptions();
        const oldOptions = old.options || {};
        const domain = Number(old.domain || 3);
        for (const key of ["radius_km", "radius_resolution_km", "center_search_radius_km", "theta_environment_domain", "shear_source_domain", "shear_radius_inner_km", "shear_radius_outer_km", "asymmetry_radius_km", "dbz_asymmetry_height_m"]) {
          value(key, optionValue(oldOptions, key, domain, form.elements.namedItem(key)?.value));
        }
        if (oldOptions.theta_environment_ring_km?.length === 2) {
          value("theta_ring_inner_km", oldOptions.theta_environment_ring_km[0]);
          value("theta_ring_outer_km", oldOptions.theta_environment_ring_km[1]);
        }
        form.elements.namedItem("force").checked = Boolean(old.force);
        form.elements.namedItem("clip_to_source_end").checked = Boolean(old.clip_to_source_end);
        await previewPlan().catch(showError);
      });
      row.append(text, retry); list.appendChild(row);
    }
    historyPanel.replaceChildren(list);
  }

  async function openPlot() {
    if (!catalog) return;
    const state = formValues();
    const firstPlan = currentPlans?.find(entry => entry.plan)?.plan;
    const profileId = firstPlan?.profile_id || state.profile_id || "default";
    const root = firstPlan?.root || catalog.profiles.find(item => item.id === profileId)?.root;
    if (!root) throw new Error("参数配置尚未建立；请先运行处理任务");
    const variable = splitList(state.variables)[0];
    const layout = catalog.variable_info?.[variable]?.dimension === "scalar" ? "scalar" : (currentSummary?.variable === variable ? currentSummary.layout : "");
    const transfer = buildPlotTransfer(state, layout);
    document.querySelector('[data-view="dashboardView"]').click();
    document.querySelector("#openForm").click();
    await new Promise(resolve => setTimeout(resolve, 0));
    setInputValue("data_root", root);
    const scheme = firstPlan?.center_scheme || state.center_scheme || "";
    setInputValue("member_names", selectedExperiments().map(name => memberKey(name, scheme)).join(","));
    setInputValue("domain", state.domain);
    for (const key of ["fig_type", "time", "height", "radius", "polar_max_radius_km", "polar_radius_resolution_km"]) {
      if (key in transfer) setInputValue(key, transfer[key]);
    }
    variableRowsEl.replaceChildren();
    addVariableRow({ enabled: true, name: transfer.name, plot_type: transfer.plot_type, use_polar: String(transfer.use_polar), style: "", levels: "", extend: "both", sigma: "", scale_factor: "1" });
    updateFigTypeFields();
  }

  form.addEventListener("input", event => {
    if (event.target === variableSearch || experimentAddPanel?.contains(event.target)) return;
    currentPlans = null; planRequestId++; selectedInventory = null; resetRunButton();
    if (event.target === variableInput) renderVariablePicker();
  });
  variableSearch.addEventListener("input", renderVariablePicker);
  form.addEventListener("change", event => {
    if (event.target === profileSelect || event.target.name === "domain") fillOptions();
    if (event.target === centerSchemeSelect) updateCenterSchemeFields();
    if (event.target === datasetSelect) loadCatalog().then(updateCenterSchemeFields).catch(error => setStatus(error.message, true));
    if (event.target === presetSelect) {
      const preset = (catalog?.processing_presets || []).find(item => item.id === presetSelect.value);
      renderPresetGroups(preset);
      if (preset?.dataset && datasetSelect && currentDataset() !== preset.dataset) {
        datasetSelect.value = preset.dataset;
        loadCatalog().then(updateCenterSchemeFields).catch(error => setStatus(error.message, true));
      }
    }
  });
  document.querySelector("#previewProcessing").addEventListener("click", () => previewPlan(null, "single").catch(error => showError(error, "single")));
  document.querySelector("#previewPreset")?.addEventListener("click", () => {
    try {
      const dimensionOf = name => catalog?.variable_info?.[name]?.dimension;
      // 2026-11-03：不再按时间拆段（parts = 1），同一个变量只在一个任务里算。
      const entries = buildPresetPayloads(formValues(), presetGroups, presetStart.value, presetEnd.value, 1, dimensionOf, catalog?.experiment_end_hours, presetMerge);
      previewPlan(entries, "preset").catch(error => showError(error, "preset"));
    } catch (error) {
      showError(error, "preset");
    }
  });
  presetEnd?.addEventListener("input", renderPresetEndHint);
  variableShowAll?.addEventListener("change", renderVariablePicker);
  runButton.addEventListener("click", () => submitJob().catch(error => showError(error)));
  runPresetButton?.addEventListener("click", () => submitJob().catch(error => showError(error)));
  plotButton.addEventListener("click", () => openPlot().catch(showError));
  for (const button of document.querySelectorAll("[data-experiment-select]")) {
    button.addEventListener("click", () => {
      if (!catalog) return;
      const mode = button.dataset.experimentSelect;
      setExperiments(mode === "none" ? [] : mode === "all" ? [...catalog.experiments] : catalog.experiments.filter(name => name.endsWith(mode)));
    });
  }
  document.querySelector("#refreshProcessingHistory").addEventListener("click", () => loadHistory().catch(error => { historyPanel.textContent = error.message; }));
  if (experimentAddToggle) {
    experimentAddToggle.addEventListener("click", () => {
      experimentAddPanel.hidden = !experimentAddPanel.hidden;
      experimentAddToggle.setAttribute("aria-expanded", String(!experimentAddPanel.hidden));
      if (!experimentAddPanel.hidden) experimentNewName.focus();
    });
    experimentAddSubmit.addEventListener("click", () => addExperiment());
    for (const input of [experimentNewName, experimentNewPath]) {
      // 输入法组字时的回车只是确认候选词，不提交（Safari 里 isComposing 可能已是 false，keyCode 是 229）
      let composing = false; let compositionEndedAt = 0;
      input.addEventListener("compositionstart", () => { composing = true; });
      input.addEventListener("compositionend", () => { composing = false; compositionEndedAt = Date.now(); });
      input.addEventListener("keydown", event => {
        const ime = event.isComposing || composing || event.keyCode === 229 || Date.now() - compositionEndedAt < 150;
        if (event.key === "Enter" && !ime) { event.preventDefault(); addExperiment(); }
      });
    }
  }
  window.addEventListener("scroll", hideVariableTooltip, true);
  document.querySelector("#refreshInventory").addEventListener("click", () => loadInventory().catch(error => { inventorySummary.textContent = error.message; }));
  for (const element of Object.values(inventoryFilters)) {
    element.addEventListener("input", () => { inventoryPage = 1; renderInventory(); });
  }
  inventoryPrevious.addEventListener("click", () => { inventoryPage--; renderInventory(); });
  inventoryNext.addEventListener("click", () => { inventoryPage++; renderInventory(); });
  loadInventory().catch(error => { inventorySummary.textContent = error.message; });
  loadCatalog().catch(error => { setStatus(error.message, true); });
  loadHistory().catch(error => { historyPanel.textContent = error.message; });
})(typeof window === "undefined" ? globalThis : window);
