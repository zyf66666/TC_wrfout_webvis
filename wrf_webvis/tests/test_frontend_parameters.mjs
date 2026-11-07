import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const indexSource = readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");
assert.match(indexSource, /src="\/static\/app\.js\?v=20261107-core&access=20261106&custom=20261014"/);
const start = source.indexOf("function selectionFromText");
const end = source.indexOf("function parseOptionalNumber");
assert.ok(start >= 0 && end > start, "could not locate frontend selection helpers");
assert.match(source.slice(start, end), /function formatWithMemberTimeLimits/);

// Evaluate only the pure parsing helpers; the full file intentionally depends on the browser DOM.
const helpers = new Function(`${source.slice(start, end)}; return { selectionFromText, parseLevels, formatWithMemberTimeLimits };`)();

assert.deepEqual(helpers.selectionFromText("0:3:1"), { values: [0, 1, 2, 3] });
assert.deepEqual(helpers.selectionFromText("0h5min,1h10min"), { values: [5 / 60, 1 + 10 / 60] });
assert.deepEqual(helpers.parseLevels("0:3:1"), [0, 1, 2]);

const memberLimits = { WRF_135_land: 48, WRF_135_sea: 69 };
const restoredFormat = helpers.formatWithMemberTimeLimits(
  { time_series: { grid: true }, time_radius: {}, time_azimuth: {}, track: {} },
  { member_time_limits: memberLimits },
);
assert.deepEqual(restoredFormat.time_series.member_end_hours, memberLimits);
assert.deepEqual(restoredFormat.time_radius.member_end_hours, memberLimits);
assert.deepEqual(restoredFormat.time_azimuth.member_end_hours, memberLimits);
assert.deepEqual(restoredFormat.time_height.member_end_hours, memberLimits);
assert.deepEqual(restoredFormat.track.member_end_hours, memberLimits);
assert.match(source, /populateFormatFields\(formatWithMemberTimeLimits\(request\.format \|\| \{\}, request\.selection \|\| \{\}\)\)/);

assert.match(indexSource, /name="time_series_hide_weak_rmw"/);
assert.match(source, /time_series\.hide_weak_rmw = String\(data\.get\("time_series_hide_weak_rmw"\)\) === "true"/);
assert.match(source, /time_series\.rmw_min_axis_mean_wind = timeSeriesRmwMinAxisMeanWind/);
assert.match(source, /figType === 2 && format\.time_radius\.member_end_hours/);
assert.match(source, /figType === 3/);
assert.match(source, /format\.time_azimuth\.member_end_hours/);
assert.match(source, /key: "landfall_times_h", label: "各成员登陆时次 \(h\)", type: "mapping"/);
assert.match(source, /key: "land_distance_members", label: "绘制陆地距离的成员", type: "text_list"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "draw_rmw", label: "绘制 RMW", type: "boolean", default: false/);
assert.match(source, /section: "radius_height", group: "半径-高度图", key: "draw_rmw", label: "绘制 RMW", type: "boolean", default: false/);
assert.match(source, /section: "radius_height", group: "半径-高度图", key: "rmw_source"/);
assert.match(source, /section: "radius_height", group: "半径-高度图", key: "rmw_color"/);
assert.match(source, /section: "radius_height", group: "半径-高度图", key: "rmw_smooth_sigma_km"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "rmw_source", label: "RMW 数据源", type: "select", default: "rmw"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "rmw_height_m", label: "RMW 高度 \(m，仅逐高度数据源\)", type: "number", default: 1000/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "draw_r34", label: "绘制 R34", type: "boolean", default: false/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "rmw_color", label: "RMW 颜色", type: "text", default: "darkgreen"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "rmw_linestyle", label: "RMW 线型", type: "linestyle", default: "-"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "rmw_linewidth", label: "RMW 线宽", type: "number", default: 1\.5/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "r_34_color", label: "R34 颜色", type: "text", default: "darkorange"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "r_34_linestyle", label: "R34 线型", type: "linestyle", default: "--"/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "r_34_linewidth", label: "R34 线宽", type: "number", default: 1\.5/);
assert.match(source, /section: "time_radius", group: "时间-半径图", key: "overlay_smooth_sigma", label: "RMW\/R34 平滑 sigma \(时次\)", type: "number", default: 0/);
assert.match(source, /section: "time_series", group: "时间序列样式", key: "member_end_hours", label: "各成员结束时次 \(h\)", type: "mapping", default: "", optional: true/);
assert.match(source, /selection\.member_time_limits = format\.time_series\.member_end_hours/);
assert.match(source, /const DEFAULT_MEMBER_TIME_LIMITS = \{ WRF_315_land: 102, WRF_315_sea: 102 \}/);
assert.match(source, /member_time_limits: \{ \.\.\.DEFAULT_MEMBER_TIME_LIMITS \}/);
assert.match(source, /key: "annotate_final_time", label: "标注末时次", type: "boolean", default: true/);
assert.match(source, /key: "member_end_hours", label: "各成员结束时次 \(h\)", type: "mapping", default: "", optional: true/);
assert.match(source, /key: "marker_interval_hours", label: "圆形标记间隔 \(h\)", type: "number", default: 0, optional: true/);

// Time-height is a first-class plot type: all four selections are editable,
// while its dedicated format section controls the two displayed axes.
assert.match(indexSource, /<option value="8">time-height<\/option>/);
assert.match(indexSource, /name="time_height_xlim"/);
assert.match(indexSource, /name="time_height_ylim"/);
assert.match(indexSource, /name="time_height_time_interval"/);
assert.match(indexSource, /name="time_height_height_interval"/);
assert.match(source, /["']time-height["']:\s*["']8["']/);
assert.match(source, /8:\s*\["time", "height", "angle", "radius"\]/);
assert.match(source, /8:\s*"time-height"/);
assert.match(source, /else if \(figType === 8\) \{\s*payload\.height = selectionFromText\(data\.get\("height"\)\);\s*payload\.angle = selectionFromText\(data\.get\("angle"\)\);\s*payload\.radius = selectionFromText\(data\.get\("radius"\)\);/);
assert.match(source, /time_height:\s*\{ \.\.\.\(advanced\.time_height \|\| \{\}\), \.\.\.time_height \}/);
const selectionPayloadSource = source.slice(
  source.indexOf("function selectionPayload"),
  source.indexOf("function updateFigTypeFields"),
);
const selectionPayload = new Function(
  "selectionFromText",
  `${selectionPayloadSource}; return selectionPayload;`,
)((value) => ({ values: String(value).split(",").map(Number) }));
const timeHeightSelection = selectionPayload(
  { get: (key) => ({ time: "0,6", height: "1000,2000", angle: "0,360", radius: "0,100" })[key] },
  8,
);
assert.deepEqual(timeHeightSelection, {
  time: { values: [0, 6] },
  height: { values: [1000, 2000] },
  angle: { values: [0, 360] },
  radius: { values: [0, 100] },
});

assert.match(source, /1:\s*\["time", "height", "angle", "radius"\]/);
const radiusHeightSelection = selectionPayload(
  { get: (key) => ({ time: "36,37", height: "100,1000", angle: "0,360", radius: "0,120" })[key] },
  1,
);
assert.deepEqual(radiusHeightSelection, {
  time: { values: [36, 37] },
  height: { values: [100, 1000] },
  angle: { values: [0, 360] },
  radius: { values: [0, 120] },
});
const timeRadiusSelection = selectionPayload(
  { get: (key) => ({ time: "0,1", height: "2000", angle: "0,360", radius: "0,120" })[key] }, 2,
);
assert.deepEqual(timeRadiusSelection.radius, { values: [0, 120] });

// The browser is deliberately plot-only: data preparation happens through
// 处理数据/process_data.py before a user opens the dashboard.
assert.doesNotMatch(indexSource, /data-view="processingView"/);
assert.doesNotMatch(indexSource, /id="processForm"/);
assert.doesNotMatch(indexSource, /name="wrfout_root"/);
assert.doesNotMatch(indexSource, /name="wrfout_file"/);
assert.doesNotMatch(indexSource, /name="recompute"/);
assert.doesNotMatch(indexSource, /id="plotProcessingOptions"/);
assert.match(indexSource, /name="data_root" value="\/Volumes\/land_1\/processed_v3"/);
assert.match(source, /normalizeDataRoot\(request\.processed_root \|\| request\.data_root\)/);
const defaultsStart = source.indexOf("const DEFAULT_DATA_ROOT");
const defaultsEnd = source.indexOf("const formatOptionDefinitions");
assert.ok(defaultsStart >= 0 && defaultsEnd > defaultsStart, "could not locate frontend defaults");
const defaults = new Function(`${source.slice(defaultsStart, defaultsEnd)}; return { DEFAULT_DATA_ROOT, normalizeDataRoot, normalizeLegacyVariableRows };`)();
assert.equal(defaults.normalizeDataRoot(), "/Volumes/land_1/processed_v3");
assert.equal(defaults.normalizeDataRoot("/Volumes/WRF/processed"), "/Volumes/land_1/processed_v3");
assert.equal(defaults.normalizeDataRoot("/Volumes/land_1/processed_v2"), "/Volumes/land_1/processed_v3");
assert.equal(defaults.normalizeDataRoot("/custom/processed"), "/custom/processed");
assert.deepEqual(defaults.normalizeLegacyVariableRows([
  { name: "tilt_1_6", plot_type: "line", style: "k" },
  { name: "tilt_2_6", plot_type: "line", style: "black" },
  { name: "wind", plot_type: "line", style: "#123456" },
  { name: "temp", plot_type: "contour", style: "black" },
]), [
  { name: "tilt_1_6", plot_type: "line", style: "" },
  { name: "tilt_2_6", plot_type: "line", style: "black" },
  { name: "wind", plot_type: "line", style: "#123456" },
  { name: "temp", plot_type: "contour", style: "black" },
]);
const variableRowStart = source.indexOf("function variableRequestToRow");
const variableRowEnd = source.indexOf("function quiverRequestToRow");
const variableRequestToRow = new Function(`const levelsToText = (value) => value == null ? "" : String(value); const selectionToText = (value) => value == null ? "" : String(value); ${source.slice(variableRowStart, variableRowEnd)}; return variableRequestToRow;`)();
assert.equal(variableRequestToRow({ name: "tilt_1_6", plot_type: "line" }).style, "");
assert.equal(variableRequestToRow({ name: "tilt_1_6", plot_type: "line", color: "#123456" }).style, "#123456");
assert.equal(variableRequestToRow({ name: "tilt_1_6", plot_type: "line", color: "black" }).style, "black");
assert.equal(variableRequestToRow({ name: "temp", plot_type: "contour", color: "black" }).style, "black");
assert.match(source, /variablePreferenceMigrationKey/);
assert.doesNotMatch(source, /\/api\/process/);
assert.doesNotMatch(source, /buildProcessingRequest/);
assert.doesNotMatch(source, /collectProcessingOptions\(data, "plot_opt_"\)/);
assert.match(source, /payload\.detail\.message \|\| payload\.detail\.error \|\| payload\.detail\.code/);

// A fresh installation has no dashboard yet.  The add-image action must still
// be available; only an editor may create/select a dashboard on opening.
assert.doesNotMatch(indexSource, /id="openForm"[^>]*\bdisabled\b/);
assert.match(source, /async function ensureCurrentDashboard\(\)/);
assert.match(source, /if \(canEditFrontend\(\)\) await ensureCurrentDashboard\(\);/);
assert.match(source, /async function createAndSelectDashboard\(\)/);
assert.match(source, /await selectDashboard\(dashboard\.id\);/);

console.log("frontend parameter parser checks passed");
