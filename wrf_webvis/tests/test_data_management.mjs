import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { evaluateIntegerExpression, payloadForExperiment, parseIntegerSelection, buildProcessingPayload, buildPlotTransfer, filterInventory, paginateInventory, toggleProcessingVariable, filterProcessingVariables, formatRange, formatSpatial, inventoryKey, describeVariable, historyStatusLabel, jobStatusClass, summarizeHistory } = require("../app/static/data_management.js");

test("plot transfer preserves selected polar heights and radial parameters", () => {
  const transfer = buildPlotTransfer({variables:"dbz1_polar,dbz_polar",times_minutes:"0,60",heights_m:"2000,8000",radius_km:"120",radius_resolution_km:"2"}, "cartesian_3d");
  assert.equal(transfer.name,"dbz1");
  assert.equal(transfer.fig_type,1);
  assert.equal(transfer.height,"2000,8000");
  assert.equal(transfer.time,"0");
  assert.equal(transfer.radius,"0,120");
  assert.equal(transfer.polar_max_radius_km,120);
  assert.equal(transfer.polar_radius_resolution_km,2);
  assert.equal(transfer.use_polar,true);
  const fractional = buildPlotTransfer({variables:"dbz_polar",times_minutes:"0",heights_m:"2000,8000",radius_km:"121",radius_resolution_km:"2"});
  assert.equal(fractional.radius,"0,121");
});

test("scalar plot transfer uses time series and all selected minutes", () => {
  const transfer = buildPlotTransfer({variables:"axis_mean_max_wind",times_minutes:"0:60:5",heights_m:"",radius_km:"300",radius_resolution_km:"1"}, "scalar");
  assert.equal(transfer.fig_type,5);
  assert.equal(transfer.plot_type,"line");
  assert.equal(transfer.time.split(",").length,13);
  assert.equal(Number(transfer.time.split(",")[1]),5/60);
  assert.equal(transfer.height,"");
  assert.equal(transfer.use_polar,false);
});

test("variable picker filters catalog names and toggles comma-separated selections", () => {
  const available = ["U10", "V10", "theta_polar"];
  assert.deepEqual(filterProcessingVariables(available, "10"), ["U10", "V10"]);
  assert.equal(toggleProcessingVariable("U10", "theta_polar"), "U10,theta_polar");
  assert.equal(toggleProcessingVariable("U10,theta_polar", "U10"), "theta_polar");
  assert.equal(toggleProcessingVariable("U10, U10", "V10"), "U10,V10");
});

test("processed inventory filters and paginates files without claiming completion", () => {
  const rows = Array.from({ length: 120 }, (_, index) => ({
    profile_id: index < 60 ? "default" : "p-abc",
    experiment: index % 2 ? "exp_b" : "exp_a",
    domain: index % 3 ? 3 : 2,
    variable: `theta_${index}`,
  }));
  const first = paginateInventory(rows, 1, 50);
  assert.equal(first.total, 120);
  assert.equal(first.pageCount, 3);
  assert.equal(first.items.length, 50);
  assert.equal(paginateInventory(rows, 3, 50).items.length, 20);
  const filtered = filterInventory(rows, { profile_id: "default", experiment: "exp_a", domain: "2", variable: "theta_" });
  assert.equal(filtered.length, 10);
  assert.ok(filtered.every(row => row.profile_id === "default" && row.experiment === "exp_a" && row.domain === 2));
  assert.equal(paginateInventory(filtered, 99, 50).page, 1);
});

test("minute and height selections support inclusive ranges without silent truncation", () => {
  assert.deepEqual(parseIntegerSelection("0:120:60,180", 1000), [0, 60, 120, 180]);
  assert.throws(() => parseIntegerSelection("0:120:0", 1000), /步长/);
  assert.throws(() => parseIntegerSelection("0:1001:1", 1000), /数量/);
  assert.throws(() => parseIntegerSelection("0.5", 1000), /整数/);
});

test("processing payload includes only selected scientific options", () => {
  const payload = buildProcessingPayload({
    experiment: "exp", domain: "3", variables: "theta,dbz_polar",
    times_minutes: "0:120:60", heights_m: "100,1000", profile_id: "default",
    radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30",
    theta_environment_domain: "2", theta_ring_inner_km: "400", theta_ring_outer_km: "500",
    shear_source_domain: "2", shear_radius_inner_km: "150", shear_radius_outer_km: "350",
  });
  assert.deepEqual(payload.times_minutes, [0, 60, 120]);
  assert.deepEqual(payload.heights_m, [100, 1000]);
  assert.deepEqual(payload.options.radius_km_by_domain, { 3: 300 });
  assert.deepEqual(payload.options.center_options_by_domain, { 3: { search_radius_km: 30 } });
  assert.deepEqual(payload.options.theta_environment_ring_km, [400, 500]);
  assert.equal(payload.profile_id, "default");
  // 11-04：非对称度参数空着时不发送（服务器用默认 100 km、3000 m），填了才发送
  assert.equal("asymmetry_radius_km" in payload.options, false);
  const asym = buildProcessingPayload({
    experiment: "exp", domain: "3", variables: "mdbz_asymmetry", times_minutes: "0", heights_m: "",
    radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30",
    theta_environment_domain: "2", theta_ring_inner_km: "400", theta_ring_outer_km: "500",
    shear_source_domain: "2", shear_radius_inner_km: "150", shear_radius_outer_km: "350",
    asymmetry_radius_km: "150", dbz_asymmetry_height_m: "2000",
  });
  assert.equal(asym.options.asymmetry_radius_km, 150);
  assert.equal(asym.options.dbz_asymmetry_height_m, 2000);
  assert.throws(() => buildProcessingPayload({ ...asym, experiments: "exp", variables: "mdbz_asymmetry", times_minutes: "0", heights_m: "",
    radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30", theta_environment_domain: "2",
    theta_ring_inner_km: "400", theta_ring_outer_km: "500", shear_source_domain: "2", shear_radius_inner_km: "150",
    shear_radius_outer_km: "350", domain: "3", asymmetry_radius_km: "abc" }), /非对称度圆盘半径/);
});

test("selections accept integer arithmetic such as 103*60", () => {
  assert.equal(evaluateIntegerExpression("103*60"), 6180);
  assert.equal(evaluateIntegerExpression("(24 + 6) * 60"), 1800);
  assert.equal(evaluateIntegerExpression("-5+10"), 5);
  const minutes = parseIntegerSelection("0:103*60:60", 1000);
  assert.equal(minutes.length, 104);
  assert.equal(minutes.at(-1), 6180);
  assert.deepEqual(parseIntegerSelection("2*60，3*60", 1000), [120, 180]);
  assert.deepEqual(parseIntegerSelection("0：120：60", 1000), [0, 60, 120]);
  assert.throws(() => parseIntegerSelection("7/2", 1000), /整数/);
  assert.throws(() => parseIntegerSelection("1e3", 1000), /整数/);
  assert.throws(() => parseIntegerSelection("(1+2", 1000), /括号|无法识别/);
  assert.throws(() => parseIntegerSelection("0::60", 1000), /无法识别/);
  assert.throws(() => parseIntegerSelection("alert(1)", 1000), /无法识别/);
});

test("processing payload supports several experiments split into per-experiment requests", () => {
  const payload = buildProcessingPayload({
    experiments: "WRF_315_land,WRF_315_sea,WRF_315_land", domain: "3", variables: "center_slp_5km",
    times_minutes: "0:103*60:60", heights_m: "", profile_id: "default",
    radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30",
    theta_environment_domain: "2", theta_ring_inner_km: "400", theta_ring_outer_km: "500",
    shear_source_domain: "2", shear_radius_inner_km: "150", shear_radius_outer_km: "350",
  });
  assert.deepEqual(payload.experiments, ["WRF_315_land", "WRF_315_sea"]);
  const single = payloadForExperiment(payload, "WRF_315_sea");
  assert.equal(single.experiment, "WRF_315_sea");
  assert.equal("experiments" in single, false);
  assert.equal(single.times_minutes.length, 104);
  assert.equal(single.clip_to_source_end, false);
  assert.equal(buildProcessingPayload({ ...payload, experiments: "a", variables: "U10", times_minutes: "0:121*60:60", clip_to_source_end: "on",
    radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30", theta_environment_domain: "2", theta_ring_inner_km: "400",
    theta_ring_outer_km: "500", shear_source_domain: "2", shear_radius_inner_km: "150", shear_radius_outer_km: "350" }).clip_to_source_end, true);
  assert.throws(() => buildProcessingPayload({ ...payload, experiments: "", times_minutes: "0", variables: "U10" }), /试验/);
});

test("inventory dimension cells format completed ranges and spatial grids (2026-10-21)", () => {
  assert.equal(formatRange({ count: 0 }), "—");
  assert.equal(formatRange({ count: 1, min: 6, max: 6, unit: "h" }), "6 h");
  assert.equal(formatRange({ count: 103, min: 0, max: 102, step: 1, unit: "h" }), "0–102 h · 103 个 · 每 1 h");
  assert.equal(formatRange({ count: 3, min: 100, max: 1000, step: null, unit: "m" }), "100–1000 m · 3 个");
  assert.equal(formatSpatial({ azimuth: 360, radius: 301, radius_max_km: 300, radius_step_km: 1 }), "方位 360 × 半径 301（0–300 km，每 1 km）");
  assert.equal(formatSpatial({ y: 600, x: 500 }), "y 600 × x 500 格点");
  assert.equal(formatSpatial({}), "—");
  assert.equal(inventoryKey({ profile_id: "default", experiment: "WRF_315_land", domain: 3, variable: "vt1_polar" }), "default|WRF_315_land|3|vt1_polar");
});

test("variable tooltip rows skip missing fields and history labels are Chinese (2026-10-21)", () => {
  const rows = describeVariable({ units: "m s-1", dimension_label: "三维", meaning: "切向风", dependencies: ["ua", "va"] });
  assert.deepEqual(rows.map(([label]) => label), ["单位", "维度", "含义", "依赖"]);
  assert.equal(rows.at(-1)[1], "ua、va");
  assert.equal(describeVariable(undefined)[0][0], "说明");
  assert.equal(historyStatusLabel("complete"), "已完成");
  // 11-03 任务底色：绿完成、红出错、黄排队、蓝运行中；已取消不上色
  assert.equal(jobStatusClass("complete"), "job-status-complete");
  assert.equal(jobStatusClass("failed"), "job-status-failed");
  assert.equal(jobStatusClass("interrupted"), "job-status-failed");
  assert.equal(jobStatusClass("queued"), "job-status-queued");
  assert.equal(jobStatusClass("running"), "job-status-running");
  assert.equal(jobStatusClass("canceled"), "");
  assert.equal(historyStatusLabel("weird"), "weird");
  const summary = summarizeHistory([{ status: "complete" }, { status: "failed" }, { status: "running" }, { status: "queued" }]);
  assert.equal(summary.text, "共 4 个 · 进行中 2 · 失败 1");
  assert.equal(summary.active, 2);
});

// ---- 2026-11-02 处理套餐与数据集 ----
const { compactIntegers, buildPresetPayloads, mergePresetGroups, splitTimeRange, visiblePickerVariables, presetEndMinutes } = require("../app/static/data_management.js");

const PRESET_BASE = {
  experiments: "WRF_315_land,WRF_315_sea", domain: "3", variables: "U10", times_minutes: "0", heights_m: "1000",
  profile_id: "default", dataset: "post",
  radius_km: "300", radius_resolution_km: "1", center_search_radius_km: "30",
  theta_environment_domain: "2", theta_ring_inner_km: "400", theta_ring_outer_km: "500",
  shear_source_domain: "2", shear_radius_inner_km: "150", shear_radius_outer_km: "350",
  clip_to_source_end: "on", center_scheme: "base",
};
const STANDARD = [...Array.from({ length: 10 }, (_, i) => 100 + 100 * i), ...Array.from({ length: 36 }, (_, i) => 1250 + 250 * i)];

test("compactIntegers writes standard heights back as start:end:step ranges", () => {
  assert.equal(compactIntegers(STANDARD), "100:1000:100,1250:10000:250");
  assert.equal(compactIntegers([6000, 1000]), "1000,6000");
  assert.equal(compactIntegers([100, 200, 300, 500]), "100:300:100,500");
  assert.equal(compactIntegers([]), "");
  assert.deepEqual(parseIntegerSelection(compactIntegers(STANDARD), 100), STANDARD);
});

test("preset expands into one request per enabled group and experiment", () => {
  const groups = [
    { id: "center_intensity_5min", label: "海平面中心与强度", enabled: true, variables: ["slp_center", "rmw"], time_step_minutes: 5, heights: [] },
    { id: "layer_center_1h", label: "各层中心", enabled: true, variables: "layer_center", time_step_minutes: 60, heights: STANDARD },
    { id: "extra", label: "可选", enabled: false, variables: "vt1_polar", time_step_minutes: 60, heights: "1000" },
  ];
  const entries = buildPresetPayloads(PRESET_BASE, groups, 0, 120);
  assert.deepEqual(entries.map(entry => [entry.group, entry.experiment]), [
    ["center_intensity_5min", "WRF_315_land"], ["center_intensity_5min", "WRF_315_sea"],
    ["layer_center_1h", "WRF_315_land"], ["layer_center_1h", "WRF_315_sea"],
  ]);
  const [five, , hourly] = entries.map(entry => entry.payload);
  assert.equal(five.times_minutes.length, 1441);
  assert.equal(five.times_minutes.at(-1), 7200);
  assert.deepEqual(five.variables, ["slp_center", "rmw"]);
  assert.deepEqual(five.heights_m, []);
  assert.equal(five.dataset, "post");
  assert.equal(five.label, "海平面中心与强度（每 5 min） · 0–120 h");
  assert.equal(five.experiment, "WRF_315_land");
  assert.equal("experiments" in five, false);
  assert.equal(hourly.times_minutes.length, 121);
  assert.deepEqual(hourly.heights_m, STANDARD);
  assert.equal(hourly.center_scheme, "base");
  assert.equal(hourly.clip_to_source_end, true);
});

test("preset validates time window, steps and at least one group", () => {
  const group = { id: "g", label: "组", enabled: true, variables: "rmw", time_step_minutes: 5, heights: "" };
  assert.equal(buildPresetPayloads(PRESET_BASE, [group], 1, 2)[0].payload.times_minutes[0], 60);
  assert.throws(() => buildPresetPayloads(PRESET_BASE, [group], 3, 2), /起始/);
  assert.throws(() => buildPresetPayloads(PRESET_BASE, [{ ...group, enabled: false }], 0, 1), /至少勾选/);
  assert.throws(() => buildPresetPayloads(PRESET_BASE, [{ ...group, time_step_minutes: 2.5 }], 0, 1), /正整数/);
  assert.throws(() => buildPresetPayloads(PRESET_BASE, [{ ...group, variables: "" }], 0, 1), /组：/);
  assert.throws(() => buildPresetPayloads(PRESET_BASE, [{ ...group, time_step_minutes: 1 }], 0, 40), /数量/);
});

test("inventory keys and filters carry the dataset", () => {
  const rows = [
    { dataset: "post", profile_id: "default", experiment: "exp", domain: 3, variable: "rmw" },
    { dataset: "v3", profile_id: "default", experiment: "exp", domain: 3, variable: "rmw" },
    { profile_id: "default", experiment: "exp", domain: 3, variable: "old" },
  ];
  assert.equal(inventoryKey(rows[0]), "post|default|exp|3|rmw");
  assert.equal(inventoryKey(rows[2]), "default|exp|3|old");
  assert.deepEqual(filterInventory(rows, { dataset: "post" }).map(row => row.variable), ["rmw"]);
  assert.deepEqual(filterInventory(rows, { dataset: "v3" }).map(row => row.variable), ["rmw", "old"]);
  assert.equal(buildProcessingPayload({ ...PRESET_BASE, experiments: "exp" }).dataset, "post");
  assert.equal("dataset" in buildProcessingPayload({ ...PRESET_BASE, dataset: "" }), false);
  assert.equal(buildProcessingPayload({ ...PRESET_BASE, times_minutes: "0:120*60:5" }).times_minutes.length, 1441);
});


test("preset merges groups with the same step and heights; height-less groups join them", () => {
  const groups = [
    { id: "center", label: "中心", enabled: true, variables: ["slp_center", "rmw"], time_step_minutes: 5, heights: [] },
    { id: "layer", label: "各层中心", enabled: true, variables: "layer_center", time_step_minutes: 60, heights: STANDARD },
    { id: "tilt", label: "倾斜", enabled: true, variables: "tilt_x,tilt_y", time_step_minutes: 60, heights: "" },
    { id: "shear", label: "切变", enabled: true, variables: "shear_core_200_850_u", time_step_minutes: 60, heights: [] },
    { id: "refl", label: "反射率", enabled: true, variables: "dbz,mdbz,dbz1_polar,layer_center", time_step_minutes: 60, heights: "100:1000:100,1250:10000:250" },
    { id: "few", label: "少数几层", enabled: true, variables: "vt1_polar", time_step_minutes: 60, heights: "1000,3000" },
  ];
  const merged = mergePresetGroups(groups);
  assert.deepEqual(merged.map(item => item.ids), [["center"], ["layer", "tilt", "shear", "refl"], ["few"]]);
  assert.deepEqual(merged[1].variables, ["layer_center", "tilt_x", "tilt_y", "shear_core_200_850_u", "dbz", "mdbz", "dbz1_polar"]);
  assert.deepEqual(merged[1].heights, STANDARD);
  assert.deepEqual(merged[2].heights, [1000, 3000]);
  const entries = buildPresetPayloads({ ...PRESET_BASE, experiments: "exp" }, groups, 0, 3);
  assert.equal(entries.length, 3);
  assert.equal(entries[1].label, "各层中心 + 倾斜 + 切变 + 反射率（每 60 min） · 0–3 h");
  assert.deepEqual(entries[1].payload.heights_m, STANDARD);
});

test("a height-less group with 3-D variables is not merged when dimensions are known", () => {
  const dims = { dbz: "height", rmw: "scalar", layer_center: "height" };
  const groups = [
    { id: "layer", label: "各层中心", enabled: true, variables: "layer_center", time_step_minutes: 60, heights: [1000] },
    { id: "oops", label: "漏填高度", enabled: true, variables: "dbz", time_step_minutes: 60, heights: "" },
    { id: "scalars", label: "标量", enabled: true, variables: "rmw", time_step_minutes: 60, heights: "" },
  ];
  const merged = mergePresetGroups(groups, name => dims[name]);
  assert.deepEqual(merged.map(item => item.ids), [["layer", "scalars"], ["oops"]]);
  // 单独成一个请求，没有高度：后端预览时会报“三维变量必须选择高度”，不会被悄悄算到别组的高度上。
  const oops = buildPresetPayloads({ ...PRESET_BASE, experiments: "exp" }, groups, 0, 1, 1, name => dims[name]).find(entry => entry.group === "oops");
  assert.deepEqual(oops.payload.heights_m, []);
});

test("preset requests are split into contiguous time chunks for parallel jobs", () => {
  assert.deepEqual(splitTimeRange(0, 7200, 5, 3), [{ start: 0, end: 2400 }, { start: 2405, end: 4800 }, { start: 4805, end: 7200 }]);
  assert.deepEqual(splitTimeRange(0, 60, 60, 5), [{ start: 0, end: 0 }, { start: 60, end: 60 }]);
  assert.deepEqual(splitTimeRange(0, 180, 60, 1), [{ start: 0, end: 180 }]);
  const groups = [{ id: "c", label: "中心", enabled: true, variables: "slp_center", time_step_minutes: 5, heights: [] }];
  const entries = buildPresetPayloads(PRESET_BASE, groups, 0, 120, 3);
  assert.equal(entries.length, 6);
  const land = entries.filter(entry => entry.experiment === "WRF_315_land").map(entry => entry.payload.times_minutes);
  assert.deepEqual(land.map(times => times.length), [481, 480, 480]);
  assert.deepEqual(land.flat(), Array.from({ length: 1441 }, (_, i) => i * 5));
  assert.match(entries[0].label, /第 1\/3 段 0–40 h/);
});

test("picker hides internal variables unless selected or show-all", () => {
  const names = ["dbz", "_va_lower", "u", "XLAT", "rmw"];
  const hidden = ["_va_lower", "u", "XLAT"];
  assert.deepEqual(visiblePickerVariables(names, hidden), ["dbz", "rmw"]);
  assert.deepEqual(visiblePickerVariables(names, hidden, ["_va_lower"]), ["dbz", "_va_lower", "rmw"]);
  assert.deepEqual(visiblePickerVariables(names, hidden, [], true), names);
});


test("blank preset end uses each experiment's simulation length from project_config", () => {
  const table = { WRF_315_land: 102, WRF_180_land: 39 };
  assert.equal(presetEndMinutes("WRF_315_land", "", table), 6120);
  assert.equal(presetEndMinutes("WRF_315_land", "90", table), 5400);
  assert.equal(presetEndMinutes("custom_exp", "", table), 7200);
  const groups = [
    { id: "c", label: "中心", enabled: true, variables: "slp_center", time_step_minutes: 5, heights: [] },
    { id: "h", label: "逐时", enabled: true, variables: "tilt_x", time_step_minutes: 60, heights: [] },
  ];
  const values = { ...PRESET_BASE, experiments: "WRF_315_land,WRF_180_land" };
  const entries = buildPresetPayloads(values, groups, 0, "", 3, null, table);
  const land315 = entries.filter(entry => entry.experiment === "WRF_315_land" && entry.group === "c").map(entry => entry.payload.times_minutes);
  assert.equal(land315.flat().length, 102 * 12 + 1);
  assert.equal(land315.flat().at(-1), 6120);
  const land180 = entries.filter(entry => entry.experiment === "WRF_180_land" && entry.group === "h").map(entry => entry.payload.times_minutes);
  assert.deepEqual(land180.flat(), Array.from({ length: 40 }, (_, i) => i * 60));
  // 起始时间晚于某试验的模拟时长：该试验跳过，不发请求
  const late = buildPresetPayloads(values, groups, 50, "", 1, null, table);
  assert.deepEqual(late.filter(entry => entry.skipped).map(entry => entry.experiment), ["WRF_180_land", "WRF_180_land"]);
  assert.ok(late.filter(entry => entry.skipped).every(entry => entry.payload === null && /39 h/.test(entry.skipped)));
  assert.equal(late.find(entry => entry.experiment === "WRF_315_land" && entry.group === "h").payload.times_minutes.at(-1), 6120);
  assert.throws(() => buildPresetPayloads({ ...values, experiments: "WRF_180_land" }, groups, 50, "", 1, null, table), /起始/);
});

test("presets with merge_groups=false keep every group as its own request and never split variables in time", () => {
  const groups = [
    { id: "tilt", label: "倾斜", enabled: true, variables: "tilt_2_4", time_step_minutes: 60, heights: [] },
    { id: "moist", label: "湿度", enabled: true, variables: "rh,rh_polar,rh1_polar", time_step_minutes: 60, heights: STANDARD },
    { id: "wind", label: "风", enabled: true, variables: "vr_sr,vr_sr_polar,vr_sr1_polar", time_step_minutes: 60, heights: STANDARD },
  ];
  assert.deepEqual(mergePresetGroups(groups, null, false).map(item => item.ids), [["tilt"], ["moist"], ["wind"]]);
  assert.deepEqual(mergePresetGroups(groups).map(item => item.ids), [["tilt", "moist", "wind"]]);
  const values = { ...PRESET_BASE, experiments: "WRF_315_land,WRF_315_sea" };
  const entries = buildPresetPayloads(values, groups, 0, "", 1, null, { WRF_315_land: 102, WRF_315_sea: 102 }, false);
  assert.equal(entries.length, 6);  // 3 组 × 2 个试验，每组每个试验一个任务
  assert.ok(entries.every(entry => entry.payload.times_minutes.length === 103 && entry.payload.times_minutes.at(-1) === 6120));
  const basic = buildPresetPayloads(values, [{ id: "c", label: "中心", enabled: true, variables: "slp_center", time_step_minutes: 5, heights: [] }], 0, "", 1, null, { WRF_315_land: 102, WRF_315_sea: 102 });
  assert.ok(basic.every(entry => entry.payload.times_minutes.length === 1225));  // 5 min 一个任务算完（上限 2000）
});
