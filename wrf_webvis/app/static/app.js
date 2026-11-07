let draggedImage = null;
let imageOrderSaving = false;
let suppressImageClickUntil = 0;
let dashboards = [];
let currentDashboard = null;
let currentProgressJob = null;
const progressJobs = new Map();
const progressStorageKey = "wrf_webvis.active_jobs.v1";
let logRefreshTimer = null;

const statusEl = document.querySelector("#status");
const dashboardsEl = document.querySelector("#dashboards");
const dashboardTitleEl = document.querySelector("#dashboardTitle");
const imageGridEl = document.querySelector("#imageGrid");
const openFormButton = document.querySelector("#openForm");
const renameDashboardButton = document.querySelector("#renameDashboard");
const deleteDashboardButton = document.querySelector("#deleteDashboard");
const newChildDashboardButton = document.querySelector("#newChildDashboard");
const moveDashboardSelect = document.querySelector("#moveDashboard");
const dashboardFamilyEl = document.querySelector("#dashboardFamily");
const collapsedDashboardsKey = "wrf_webvis.collapsed_dashboards.v1";
const dialog = document.querySelector("#plotDialog");
const form = document.querySelector("#plotForm");
const formStatusEl = document.querySelector("#formStatus");
const submitPlotButton = document.querySelector("#submitPlot");
const plotDialogTitle = document.querySelector("#plotDialogTitle");
const progressPanel = document.querySelector("#progressPanel");
const progressMessages = document.querySelector("#progressMessages");
const progressJobSelect = document.querySelector("#progressJobSelect");
const cancelProgressButton = document.querySelector("#cancelProgress");
const advancedFormatOptionsEl = document.querySelector("#advancedFormatOptions");
const figTypeSelect = document.querySelector("#figType");
const variableRowsEl = document.querySelector("#variableRows");
const quiverRowsEl = document.querySelector("#quiverRows");
const imageZoomDialog = document.querySelector("#imageZoomDialog");
const zoomImageEl = document.querySelector("#zoomImage");
const zoomTitleEl = document.querySelector("#zoomTitle");
const stopJobButton = document.querySelector("#stopJob");
const openLogsButton = document.querySelector("#openLogs");
const logDialog = document.querySelector("#logDialog");
const logContents = document.querySelector("#logContents");
let activeVariableFigType = null;
let variableMemory = {};
let variableMemoryTimer;
const pendingVariableMemory = new Map();
const variableMemoryStorageKey = "wrf_webvis.variable_memory.v2";
const variableStorageKey = "wrf_webvis.plot_variables.v1";
const quiverStorageKey = "wrf_webvis.quiver_variables.v1";
const variablePreferenceMigrationKey = "wrf_webvis.plot_variables.legacy_color_migrated.v1";
const customCodeEl = document.querySelector("#customCode");
const customCodeStorageKey = "wrf_webvis.custom_code.draft.v1";
const CUSTOM_CODE_EXAMPLE = `import numpy as np
import matplotlib.pyplot as plt

# 在这里读取你的数据，文件路径相对于项目根目录。
x = np.linspace(0, 2 * np.pi, 200)
fig, ax = plt.subplots(figsize=(8, 5))
ax.plot(x, np.sin(x))
ax.set(xlabel="x", ylabel="sin(x)", title="Custom plot")
fig.tight_layout()
plt.show()

# 如需指定输出：fig.savefig(OUTPUT_PATH, dpi=DPI)
`;

function saveCustomCodeDraft() {
  try {
    localStorage.setItem(customCodeStorageKey, customCodeEl.value);
  } catch (_) { /* Editing remains available if browser storage is full. */ }
}

function initializeCustomCodeEditor() {
  try {
    customCodeEl.value = localStorage.getItem(customCodeStorageKey) || "";
  } catch (_) { /* Draft storage is optional. */ }
  customCodeEl.addEventListener("input", saveCustomCodeDraft);
  customCodeEl.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !canEditFrontend()) return;
    event.preventDefault();
    customCodeEl.setRangeText("    ", customCodeEl.selectionStart, customCodeEl.selectionEnd, "end");
    saveCustomCodeDraft();
  });
  document.querySelector("#customCodeExample").addEventListener("click", () => {
    if (!canEditFrontend()) return;
    customCodeEl.value = CUSTOM_CODE_EXAMPLE;
    saveCustomCodeDraft();
    customCodeEl.focus();
  });
}

const DEFAULT_DATA_ROOT = "/Volumes/land_1/processed_v3";

function normalizeDataRoot(value) {
  const root = String(value == null ? "" : value).trim();
  if (!root || root === "/Volumes/WRF/processed" || root === "/Volumes/land_1/processed_v2") return DEFAULT_DATA_ROOT;
  return root;
}

function normalizeLegacyVariableRows(rows) {
  return rows.map((row) => {
    if (String(row && row.plot_type || "") !== "line") return row;
    const style = String(row && row.style || "").trim().toLowerCase();
    return style === "k" ? { ...row, style: "" } : row;
  });
}
// Simulation output for both 315 experiments ends at 102 h.  Keep this in the
// browser too, so a saved card with an older 108/120 h setting cannot request
// data that does not belong to the experiment.
const DEFAULT_MEMBER_TIME_LIMITS = { WRF_315_land: 102, WRF_315_sea: 102 };
const formatOptionDefinitions = [
  { section: "common", group: "通用", key: "fontname", label: "字体", type: "text", default: "Times New Roman" },
  { section: "common", group: "通用", key: "fontsize", label: "字号", type: "number", default: 18 },
  { section: "common", group: "通用", key: "missing_slices", label: "缺切片处理", type: "select", default: "error", choices: [["error", "报错（默认）"], ["blank", "留空：未处理的时次/高度在图上留白"]] },
  { section: "horizontal", group: "水平图坐标", key: "coordinate_system", label: "坐标系", type: "select", default: "center_km", choices: [["center_km", "中心相对距离 (km)"], ["grid", "原始网格坐标"]] },
  { section: "horizontal", group: "水平图叠加", key: "radius_circle_color", label: "半径圈颜色", type: "text", default: "darkgreen" },
  { section: "horizontal", group: "水平图叠加", key: "radius_circle_linewidth", label: "半径圈线宽", type: "number", default: 1 },
  { section: "horizontal", group: "水平图叠加", key: "radius_circle_linestyle", label: "半径圈线型", type: "linestyle", default: "--" },
  { section: "horizontal", group: "水平图叠加", key: "rmw_color", label: "RMW 颜色", type: "text", default: "darkgreen" },
  { section: "horizontal", group: "水平图叠加", key: "rmw_linewidth", label: "RMW 线宽", type: "number", default: 3 },
  { section: "horizontal", group: "水平图叠加", key: "rmw_linestyle", label: "RMW 线型", type: "linestyle", default: "-" },
  { section: "horizontal", group: "水平图叠加", key: "rmw_source", label: "RMW 数据源", type: "select", default: "rmw", choices: [["rmw", "最低模式层（rmw）"], ["rmw_by_height", "指定高度风，低层（SLP）中心"], ["rmw_by_layer_center", "指定高度风，该层中心"], ["rmw_by_height_sr", "指定高度风暴相对风，低层（SLP）中心"], ["rmw_by_layer_center_sr", "指定高度风暴相对风，该层中心"]] },
  { section: "horizontal", group: "水平图叠加", key: "rmw_height_m", label: "RMW 高度 (m，仅逐高度数据源)", type: "number", default: 1000 },
  { section: "horizontal", group: "水平图叠加", key: "rmw_annotate", label: "左下角标注 RMW 数值", type: "boolean", default: false },
  { section: "horizontal", group: "水平图叠加", key: "land_distance_color", label: "陆地距离线颜色", type: "text", default: "black" },
  { section: "horizontal", group: "水平图叠加", key: "land_distance_linestyle", label: "陆地距离线型", type: "linestyle", default: "--" },
  { section: "horizontal", group: "水平图叠加", key: "land_distance_linewidth", label: "陆地距离线宽", type: "number", default: 2 },
  { section: "horizontal", group: "水平图叠加", key: "land_distance_side", label: "陆地距离方向", type: "select", default: "west", choices: [["west", "west"], ["east", "east"], ["north", "north"], ["south", "south"]] },
  { section: "horizontal", group: "中心与诊断", key: "draw_center_height_m", label: "叠加指定高度中心 (m)", type: "number", default: "", optional: true },
  { section: "horizontal", group: "中心与诊断", key: "inner_core_vertical_wind_shear", label: "叠加内核风切变箭头", type: "boolean", default: false },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "draw_tilt_track", label: "绘制倾斜轨迹", type: "boolean", default: false },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_layer", label: "倾斜范围", type: "select", default: "2_8", choices: [["2_4", "2–4 km"], ["2_6", "2–6 km"], ["2_8", "2–8 km"], ["2_10", "2–10 km"]] },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_overlay_members", label: "多试验叠加（独立轨迹，取消变量和风箭头）", type: "boolean", default: false },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_member_colors", label: "试验颜色（固定线色及标记）", type: "mapping", default: "", optional: true, placeholder: "WRF_225_land:C0,WRF_225_sea:C1" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_member_linestyles", label: "试验线型", type: "mapping", default: "", optional: true, placeholder: "WRF_225_land:-,WRF_225_sea:--" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_times", label: "轨迹时次", type: "number_list", default: "", optional: true, placeholder: "0,12,24,..." },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_time_range", label: "轨迹时段", type: "number_list", default: "", optional: true, placeholder: "0,120" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_smooth_sigma", label: "平滑 sigma", type: "number", default: 1 },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_time_gradient", label: "按时间渐变着色", type: "boolean", default: true },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_cmap", label: "渐变色表", type: "text", default: "viridis" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_color", label: "固定颜色", type: "text", default: "C3" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_linewidth", label: "线宽", type: "number", default: 3 },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_alpha", label: "透明度", type: "number", default: 1 },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_start_marker", label: "起点标记", type: "text", default: "s" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_end_marker", label: "终点标记", type: "text", default: "*" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_mark_times", label: "标记时次", type: "number_list", default: "24,48,72,96,120" },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_annotate_times", label: "标注时次", type: "boolean", default: true },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_colorbar", label: "显示时间色标", type: "boolean", default: false },
  { section: "horizontal", group: "涡旋倾斜轨迹", key: "tilt_track_colorbar_label", label: "时间色标标题", type: "text", default: "Time (h)" },
  { section: "horizontal", group: "其他叠加", key: "cloud_classification", label: "云分类标记", type: "boolean", default: false },
  { section: "horizontal", group: "其他叠加", key: "direction_angle", label: "方向线角度", type: "number", default: "", optional: true },
  { section: "horizontal", group: "其他叠加", key: "direction_start", label: "方向线起点", type: "number", default: 0 },
  { section: "horizontal", group: "其他叠加", key: "direction_end", label: "方向线终点", type: "number", default: "", optional: true },
  { section: "horizontal", group: "其他叠加", key: "arc_radius", label: "圆弧半径", type: "number", default: "", optional: true },
  { section: "horizontal", group: "其他叠加", key: "arc_start_angle", label: "圆弧起始角", type: "number", default: "", optional: true },
  { section: "horizontal", group: "其他叠加", key: "arc_end_angle", label: "圆弧终止角", type: "number", default: "", optional: true },
  { section: "radius_height", group: "半径-高度图", key: "xlim", label: "半径范围 (km)", type: "number_list", default: "", optional: true, placeholder: "0,200" },
  { section: "radius_height", group: "半径-高度图", key: "ylim", label: "高度范围 (m)", type: "number_list", default: "", optional: true, placeholder: "0,10000" },
  { section: "radius_height", group: "半径-高度图", key: "radius_tick_interval_km", label: "半径刻度间隔 (km)", type: "number", default: 25 },
  { section: "radius_height", group: "半径-高度图", key: "height_tick_interval_m", label: "高度刻度间隔 (m)", type: "number", default: 1000 },
  { section: "radius_height", group: "半径-高度图", key: "draw_rmw", label: "绘制 RMW", type: "boolean", default: false },
  { section: "radius_height", group: "半径-高度图", key: "rmw_source", label: "RMW 口径", type: "select", default: "rmw_by_height", choices: [["rmw_by_height", "各高度风，低层中心"], ["rmw_by_layer_center", "各高度风，逐层中心"], ["rmw_by_height_sr", "各高度风暴相对风，低层中心"], ["rmw_by_layer_center_sr", "各高度风暴相对风，逐层中心"]] },
  { section: "radius_height", group: "半径-高度图", key: "rmw_color", label: "RMW 颜色", type: "text", default: "magenta" },
  { section: "radius_height", group: "半径-高度图", key: "rmw_linestyle", label: "RMW 线型", type: "linestyle", default: "-" },
  { section: "radius_height", group: "半径-高度图", key: "rmw_linewidth", label: "RMW 线宽", type: "number", default: 2 },
  { section: "radius_height", group: "半径-高度图", key: "rmw_smooth_sigma_km", label: "RMW 高度平滑 σ (km，0 为原始值)", type: "number", default: 0 },
  { section: "time_radius", group: "时间-半径图", key: "member_end_hours", label: "各成员结束时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_135_land:48,WRF_180_land:39" },
  { section: "time_radius", group: "时间-半径图", key: "landfall_time_h", label: "登陆时次 (h)", type: "number", default: "", optional: true, placeholder: "例如 36" },
  { section: "time_radius", group: "时间-半径图", key: "landfall_times_h", label: "各成员登陆时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_90_land:63,WRF_135_land:36" },
  { section: "time_radius", group: "时间-半径图", key: "draw_land_distance", label: "绘制陆地距离", type: "boolean", default: false },
  { section: "time_radius", group: "时间-半径图", key: "land_distance_members", label: "绘制陆地距离的成员", type: "text_list", default: "", optional: true, placeholder: "WRF_0_land,WRF_45_land,..." },
  { section: "time_radius", group: "时间-半径图", key: "draw_rmw", label: "绘制 RMW", type: "boolean", default: false },
  { section: "time_radius", group: "时间-半径图", key: "rmw_source", label: "RMW 数据源", type: "select", default: "rmw", choices: [["rmw", "旧 rmw 标量（逐时次文件）"], ["rmw_by_height", "指定高度风，低层（SLP）中心"], ["rmw_by_layer_center", "指定高度风，该层中心"], ["rmw_by_height_sr", "指定高度风暴相对风，低层（SLP）中心"], ["rmw_by_layer_center_sr", "指定高度风暴相对风，该层中心"]] },
  { section: "time_radius", group: "时间-半径图", key: "rmw_height_m", label: "RMW 高度 (m，仅逐高度数据源)", type: "number", default: 1000 },
  { section: "time_radius", group: "时间-半径图", key: "rmw_color", label: "RMW 颜色", type: "text", default: "darkgreen" },
  { section: "time_radius", group: "时间-半径图", key: "rmw_linestyle", label: "RMW 线型", type: "linestyle", default: "-" },
  { section: "time_radius", group: "时间-半径图", key: "rmw_linewidth", label: "RMW 线宽", type: "number", default: 1.5 },
  { section: "time_radius", group: "时间-半径图", key: "draw_r34", label: "绘制 R34", type: "boolean", default: false },
  { section: "time_radius", group: "时间-半径图", key: "r_34_color", label: "R34 颜色", type: "text", default: "darkorange" },
  { section: "time_radius", group: "时间-半径图", key: "r_34_linestyle", label: "R34 线型", type: "linestyle", default: "--" },
  { section: "time_radius", group: "时间-半径图", key: "r_34_linewidth", label: "R34 线宽", type: "number", default: 1.5 },
  { section: "time_radius", group: "时间-半径图", key: "overlay_smooth_sigma", label: "RMW/R34 平滑 sigma (时次)", type: "number", default: 0 },
  { section: "time_radius", group: "时间-半径图", key: "inset_wind", label: "显示参考风箭头", type: "boolean", default: false },
  { section: "time_azimuth", group: "时间-方位角图", key: "xlim", label: "方位角范围", type: "number_list", default: "", optional: true },
  { section: "time_azimuth", group: "时间-方位角图", key: "member_end_hours", label: "各成员结束时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_135_land:48,WRF_180_land:39" },
  { section: "time_azimuth", group: "时间-方位角图", key: "landfall_time_h", label: "登陆时次 (h)", type: "number", default: "", optional: true, placeholder: "例如 36" },
  { section: "time_azimuth", group: "时间-方位角图", key: "landfall_times_h", label: "各成员登陆时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_90_land:63,WRF_135_land:36" },
  { section: "time_azimuth", group: "时间-方位角图", key: "start_x", label: "起始方位角", type: "number", default: "", optional: true },
  { section: "time_azimuth", group: "时间-方位角图", key: "time_interval", label: "时间刻度间隔 (h)", type: "number", default: 5 },
  { section: "time_azimuth", group: "时间-方位角图", key: "double_azimuth", label: "双周期方位角", type: "boolean", default: false },
  { section: "time_azimuth", group: "时间-方位角图", key: "inner_core_vertical_wind_shear", label: "叠加内核风切变", type: "boolean", default: false },
  { section: "time_azimuth", group: "时间-方位角图", key: "center_height_m", label: "中心高度 (m)", type: "number", default: "", optional: true },
  { section: "time_azimuth", group: "时间-方位角图", key: "center_reference_height_m", label: "参考中心高度 (m)", type: "number", default: 2000 },
  { section: "height_azimuth", group: "高度-方位角图", key: "xlim", label: "方位角范围", type: "number_list", default: "", optional: true },
  { section: "height_azimuth", group: "高度-方位角图", key: "ylim", label: "高度范围 (m)", type: "number_list", default: "", optional: true },
  { section: "height_azimuth", group: "高度-方位角图", key: "start_x", label: "起始方位角", type: "number", default: "", optional: true },
  { section: "height_azimuth", group: "高度-方位角图", key: "double_azimuth", label: "双周期方位角", type: "boolean", default: false },
  { section: "height_azimuth", group: "高度-方位角图", key: "draw_azimuth_direction", label: "方位角方向线", type: "number", default: "", optional: true },
  { section: "time_height", group: "时间-高度图", key: "member_end_hours", label: "各成员结束时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_135_land:48,WRF_180_land:39" },
  { section: "time_height", group: "时间-高度图", key: "xlabel", label: "X 轴标题", type: "text", default: "Time (h)" },
  { section: "time_height", group: "时间-高度图", key: "ylabel", label: "Y 轴标题", type: "text", default: "Height (km)" },
  { section: "time_series", group: "时间序列坐标轴", key: "ylim", label: "Y 轴范围", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列坐标轴", key: "yticks", label: "Y 轴刻度", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列坐标轴", key: "ytick_start", label: "刻度起点", type: "number", default: "", optional: true },
  { section: "time_series", group: "时间序列坐标轴", key: "ytick_end", label: "刻度终点", type: "number", default: "", optional: true },
  { section: "time_series", group: "时间序列坐标轴", key: "ytick_interval", label: "刻度间隔", type: "number", default: 5 },
  { section: "time_series", group: "时间序列坐标轴", key: "integer_yticks", label: "整数刻度", type: "boolean", default: true },
  { section: "time_series", group: "时间序列坐标轴", key: "ytick_nbins", label: "自动刻度数量", type: "integer", default: 6 },
  { section: "time_series", group: "时间序列双轴", key: "left_yticks", label: "左轴刻度", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "right_yticks", label: "右轴刻度", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "left_ylim", label: "左轴范围", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "right_ylim", label: "右轴范围", type: "number_list", default: "", optional: true },
  { section: "time_series", group: "时间序列样式", key: "grid", label: "显示网格", type: "boolean", default: true },
  { section: "time_series", group: "时间序列样式", key: "grid_linestyle", label: "网格线型", type: "linestyle", default: "--" },
  { section: "time_series", group: "时间序列样式", key: "grid_alpha", label: "网格透明度", type: "number", default: 0 },
  { section: "time_series", group: "时间序列样式", key: "xlabel", label: "X 轴标题", type: "text", default: "Time (h)" },
  { section: "time_series", group: "时间序列样式", key: "ylabel", label: "Y 轴标题", type: "text", default: "Value" },
  { section: "time_series", group: "时间序列样式", key: "default_linestyle", label: "默认线型", type: "linestyle", default: "-" },
  { section: "time_series", group: "时间序列样式", key: "member_linestyles", label: "成员线型映射", type: "mapping", default: "sea:-,land:--", placeholder: "sea:-,land:--" },
  { section: "time_series", group: "时间序列样式", key: "member_end_hours", label: "各成员结束时次 (h)", type: "mapping", default: "", optional: true, placeholder: "WRF_135_sea:69,WRF_180_sea:60" },
  { section: "time_series", group: "时间序列双轴", key: "left_ylabel", label: "左轴标题", type: "text", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "right_ylabel", label: "右轴标题", type: "text", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "left_color", label: "左轴颜色", type: "text", default: "", optional: true },
  { section: "time_series", group: "时间序列双轴", key: "right_color", label: "右轴颜色", type: "text", default: "", optional: true },
  { section: "time_series", group: "时间序列样式", key: "direction_ticks", label: "方向刻度", type: "boolean_auto", default: "auto" },
  { section: "time_series", group: "时间序列样式", key: "direction_tick_values", label: "方向刻度值", type: "number_list", default: "0,90,180,270,360" },
  { section: "time_series", group: "时间序列样式", key: "direction_tick_labels", label: "方向刻度标签", type: "text_list", default: "US,RS,DS,LS,US" },
  { section: "track", group: "轨迹背景降水", key: "draw_rainnc_background", label: "绘制 RAINNC 背景", type: "boolean", default: false },
  { section: "track", group: "轨迹背景降水", key: "rainnc_member", label: "降水成员", type: "text", default: "", optional: true, placeholder: "默认首个成员" },
  { section: "track", group: "轨迹背景降水", key: "rainnc_domain", label: "降水 domain", type: "integer", default: 1 },
  { section: "track", group: "轨迹背景降水", key: "rainnc_time_hour", label: "降水时次 (h)", type: "number", default: 120 },
  { section: "track", group: "轨迹背景降水", key: "rainnc_variable", label: "降水变量", type: "text", default: "RAINNC" },
  { section: "track", group: "轨迹背景降水", key: "rainnc_path", label: "降水文件", type: "text", default: "", optional: true },
  { section: "track", group: "轨迹背景降水", key: "rainnc_levels", label: "降水等级", type: "number_list", default: "1,5,10,20,40,60,80,100,150,200,300" },
  { section: "track", group: "轨迹背景降水", key: "rainnc_cmap", label: "降水色表", type: "text", default: "cmaps.precip3_16lev" },
  { section: "track", group: "轨迹背景降水", key: "rainnc_extend", label: "降水 extend", type: "select", default: "max", choices: [["both", "both"], ["max", "max"], ["min", "min"], ["neither", "neither"]] },
  { section: "track", group: "轨迹背景降水", key: "rainnc_alpha", label: "降水透明度", type: "number", default: 0.85 },
  { section: "track", group: "轨迹背景降水", key: "rainnc_scale_factor", label: "降水缩放倍数", type: "number", default: 1 },
  { section: "track", group: "轨迹背景降水", key: "rainnc_zorder", label: "降水图层顺序", type: "integer", default: 0 },
  { section: "track", group: "轨迹背景降水", key: "rainnc_colorbar", label: "显示降水色标", type: "boolean", default: true },
  { section: "track", group: "轨迹背景降水", key: "rainnc_colorbar_title", label: "降水色标标题", type: "text", default: "RAINNC (mm)" },
  { section: "track", group: "陆地边界", key: "land_boundary_path", label: "陆地边界文件", type: "text", default: "", optional: true },
  { section: "track", group: "陆地边界", key: "straight_land_boundary", label: "强制南北直线边界", type: "boolean", default: false },
  { section: "track", group: "陆地边界", key: "land_boundary_x_km", label: "直线边界 X (km)", type: "number", default: "", optional: true },
  { section: "track", group: "陆地边界", key: "land_color", label: "陆地线颜色", type: "text", default: "black" },
  { section: "track", group: "陆地边界", key: "land_linestyle", label: "陆地线型", type: "linestyle", default: "-" },
  { section: "track", group: "陆地边界", key: "land_linewidth", label: "陆地线宽", type: "number", default: 1.5 },
  { section: "track", group: "陆地边界", key: "land_alpha", label: "陆地线透明度", type: "number", default: 0.75 },
  { section: "track", group: "轨迹样式", key: "track_linewidth", label: "轨迹线宽", type: "number", default: 2 },
  { section: "track", group: "轨迹样式", key: "track_linestyle", label: "轨迹线型", type: "linestyle", default: "-" },
  { section: "track", group: "轨迹样式", key: "track_marker", label: "轨迹标记", type: "text", default: "o" },
  { section: "track", group: "轨迹样式", key: "track_markersize", label: "标记大小", type: "number", default: 0 },
  { section: "track", group: "轨迹样式", key: "marker_interval_hours", label: "圆形标记间隔 (h)", type: "number", default: 0, optional: true },
  { section: "track", group: "轨迹样式", key: "experiment_order", label: "试验顺序", type: "text_list", default: "0,45,90,135,180,225,270,315" },
  { section: "track", group: "轨迹样式", key: "experiment_colors", label: "试验颜色映射", type: "mapping", default: "0:#d34a38,45:#7000df,90:#2c3cff,135:#5d99ff,180:#67c7c5,225:#b8e34e,270:#7cd933,315:#d4aa3a" },
  { section: "track", group: "轨迹样式", key: "member_linestyles", label: "成员线型映射", type: "mapping", default: "sea:-,land:--" },
  { section: "track", group: "轨迹样式", key: "member_end_hours", label: "各成员结束时次 (h)", type: "mapping", default: "", optional: true },
  { section: "track", group: "轨迹样式", key: "colors", label: "按成员颜色列表", type: "text_list", default: "", optional: true },
  { section: "track", group: "轨迹样式", key: "label_template", label: "图例标签模板", type: "text", default: "{member}" },
  { section: "track", group: "轨迹样式", key: "annotate_time", label: "标注时次", type: "boolean", default: false },
  { section: "track", group: "轨迹样式", key: "annotate_final_time", label: "标注末时次", type: "boolean", default: true },
  { section: "track", group: "轨迹样式", key: "annotation_interval_hours", label: "时次标记间隔 (h)", type: "number", default: 24 },
  { section: "track", group: "轨迹样式", key: "annotation_fontsize", label: "时次标注字号", type: "number", default: 8 },
  { section: "track", group: "轨迹样式", key: "grid", label: "显示网格", type: "boolean", default: true },
  { section: "track", group: "轨迹样式", key: "legend", label: "显示图例", type: "boolean", default: true },
  { section: "track", group: "轨迹样式", key: "legend_loc", label: "图例位置", type: "text", default: "best" },
  { section: "track", group: "轨迹样式", key: "xlabel", label: "X 轴标题", type: "text", default: "West-East distance (km)" },
  { section: "track", group: "轨迹样式", key: "ylabel", label: "Y 轴标题", type: "text", default: "South-North distance (km)" },
  { section: "three_d", group: "三维范围", key: "xlim_km", label: "X 范围 (km)", type: "number_list", default: "", optional: true },
  { section: "three_d", group: "三维范围", key: "ylim_km", label: "Y 范围 (km)", type: "number_list", default: "", optional: true },
  { section: "three_d", group: "三维范围", key: "zlim_km", label: "Z 范围 (km)", type: "number_list", default: "", optional: true },
  { section: "three_d", group: "三维画布", key: "width", label: "HTML 宽度 (px)", type: "integer", default: 900 },
  { section: "three_d", group: "三维画布", key: "height", label: "HTML 高度 (px)", type: "integer", default: 800 },
  { section: "three_d", group: "三维表面", key: "surface_stride", label: "表面抽稀步长", type: "integer", default: 1 },
  { section: "three_d", group: "三维表面", key: "transparent_below", label: "低于该值透明", type: "number", default: 0 },
  { section: "three_d", group: "三维表面", key: "cmin", label: "色标最小值", type: "number", default: "", optional: true },
  { section: "three_d", group: "三维表面", key: "cmax", label: "色标最大值", type: "number", default: "", optional: true },
  { section: "three_d", group: "三维表面", key: "colorscale", label: "Plotly 色表", type: "text", default: "", optional: true },
  { section: "three_d", group: "三维中心与箭头", key: "center_heights_km", label: "中心高度 (km)", type: "number_list", default: "", optional: true },
  { section: "three_d", group: "三维中心与箭头", key: "arrow_vertical_scale", label: "垂直箭头缩放", type: "number", default: 20 },
  { section: "three_d", group: "三维中心与箭头", key: "arrow_sizeref", label: "箭头大小基准", type: "number", default: 25 },
  { section: "three_d", group: "三维中心与箭头", key: "arrow_colorscale", label: "箭头色表", type: "text", default: "Blues" },
  { section: "three_d", group: "三维中心与箭头", key: "aspectmode", label: "纵横比", type: "select", default: "cube", choices: [["cube", "cube"], ["data", "data"], ["auto", "auto"]] },
  { section: "colorbar", group: "色标", key: "enabled", label: "显示色标", type: "boolean", default: true, all: true },
  { section: "colorbar", group: "色标", key: "orientation", label: "方向", type: "select", default: "vertical", choices: [["vertical", "vertical"], ["horizontal", "horizontal"]], all: true },
  { section: "colorbar", group: "色标", key: "level_min", label: "刻度最小值", type: "number", default: "", optional: true, all: true },
  { section: "colorbar", group: "色标", key: "level_max", label: "刻度最大值", type: "number", default: "", optional: true, all: true },
  { section: "colorbar", group: "色标", key: "interval", label: "刻度间隔", type: "number", default: "", optional: true, all: true },
  { section: "colorbar", group: "色标", key: "fontsize", label: "字号", type: "number", default: 16, all: true },
  { section: "quiver_key", group: "风矢量图例", key: "enabled", label: "显示风矢量图例", type: "boolean", default: true, all: true },
  { section: "quiver_key", group: "风矢量图例", key: "labelpos", label: "标签位置", type: "select", default: "E", choices: [["E", "E"], ["W", "W"], ["N", "N"], ["S", "S"]], all: true },
  { section: "quiver_key", group: "风矢量图例", key: "coordinates", label: "坐标系统", type: "select", default: "figure", choices: [["figure", "figure"], ["axes", "axes"], ["data", "data"]], all: true },
  { section: "quiver_key", group: "风矢量图例", key: "fontsize", label: "字号", type: "number", default: 16, all: true },
];

function renderParameterControls(container, definitions, nameForDefinition, options = {}) {
  container.innerHTML = "";
  const groups = new Map();
  definitions.forEach((definition) => {
    const visibility = options.formatVisibility
      ? (definition.all || definition.section === "common" ? "all" : definition.section.replaceAll("_", "-"))
      : "all";
    const groupKey = `${visibility}:${definition.group}`;
    if (!groups.has(groupKey)) groups.set(groupKey, { title: definition.group, visibility, items: [] });
    groups.get(groupKey).items.push(definition);
  });
  groups.forEach((group) => {
    const section = document.createElement("section");
    section.className = "advanced-option-group";
    if (options.formatVisibility) section.dataset.format = group.visibility;
    const title = document.createElement("strong");
    title.textContent = group.title;
    const grid = document.createElement("div");
    grid.className = "advanced-option-grid";
    group.items.forEach((definition) => {
      const label = document.createElement("label");
      label.textContent = definition.label;
      label.appendChild(createParameterControl(definition, nameForDefinition(definition)));
      grid.appendChild(label);
    });
    section.append(title, grid);
    container.appendChild(section);
  });
}

function createParameterControl(definition, name) {
  let control;
  if (["select", "boolean", "boolean_auto", "linestyle"].includes(definition.type)) {
    control = document.createElement("select");
    let choices = definition.choices;
    if (definition.type === "boolean") choices = [["true", "是"], ["false", "否"]];
    if (definition.type === "boolean_auto") choices = [["auto", "auto"], ["true", "是"], ["false", "否"]];
    if (definition.type === "linestyle") choices = [["-", "实线 -"], ["--", "虚线 --"], ["-.", "点划线 -."], [":", "点线 :"]];
    (choices || []).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      control.appendChild(option);
    });
  } else {
    control = document.createElement("input");
    control.type = ["number", "integer"].includes(definition.type) ? "number" : "text";
    if (definition.type === "integer") control.step = "1";
    if (definition.type === "number") control.step = "any";
    if (definition.placeholder) control.placeholder = definition.placeholder;
  }
  control.name = name;
  control.dataset.parameterType = definition.type;
  control.dataset.optional = definition.optional ? "true" : "false";
  const value = definition.default;
  control.value = typeof value === "boolean" ? String(value) : (value == null ? "" : String(value));
  return control;
}

function renderAllParameterControls() {
  renderParameterControls(advancedFormatOptionsEl, formatOptionDefinitions, (definition) => `fmt_${definition.section}_${definition.key}`, { formatVisibility: true });
}

function readParameterValue(data, name, definition) {
  const raw = String(data.get(name) == null ? "" : data.get(name)).trim();
  if (!raw && definition.optional) return undefined;
  if (definition.type === "boolean") return raw === "true";
  if (definition.type === "boolean_auto") return raw === "auto" ? "auto" : raw === "true";
  if (definition.type === "number" || definition.type === "integer") {
    const value = Number(raw);
    if (!Number.isFinite(value)) return undefined;
    return definition.type === "integer" ? Math.trunc(value) : value;
  }
  if (definition.type === "number_list") return parseNumberList(raw) || undefined;
  if (definition.type === "text_list") return parseTextList(raw);
  if (definition.type === "mapping") return parseKeyValueMap(raw);
  return raw || (definition.optional ? undefined : raw);
}

function parseKeyValueMap(value) {
  const result = {};
  parseTextList(value).forEach((item) => {
    const separator = item.indexOf(":");
    if (separator <= 0) return;
    const key = item.slice(0, separator).trim();
    const mappedValue = item.slice(separator + 1).trim();
    if (key && mappedValue) result[key] = mappedValue;
  });
  return result;
}

function collectAdvancedFormat(data) {
  const result = {};
  formatOptionDefinitions.forEach((definition) => {
    const value = readParameterValue(data, `fmt_${definition.section}_${definition.key}`, definition);
    if (value === undefined) return;
    if (!result[definition.section]) result[definition.section] = {};
    result[definition.section][definition.key] = value;
  });
  return result;
}

function populateAdvancedFormatFields(format) {
  formatOptionDefinitions.forEach((definition) => {
    const control = form.querySelector(`[name="fmt_${definition.section}_${definition.key}"]`);
    if (!control) return;
    const section = format[definition.section] || {};
    const value = section[definition.key];
    if (Array.isArray(value)) control.value = value.join(",");
    else if (definition.type === "mapping" && typeof value === "object" && value !== null) {
      control.value = Object.entries(value).map(([key, mappedValue]) => `${key}:${mappedValue}`).join(",");
    }
    else if (value !== undefined && value !== null) control.value = String(value);
    else control.value = typeof definition.default === "boolean" ? String(definition.default) : String(definition.default ?? "");
  });
}

const defaultPlotVariables = [
  { enabled: true, name: "dbz", plot_type: "contourf", style: "cmaps.precip3_16lev", levels: "np.arange(10,51,5)", extend: "max", sigma: "2", scale_factor: "1" },
  { enabled: false, name: "pressure", plot_type: "contourf", style: "viridis_r", levels: "840:901:10", extend: "both", sigma: "2", scale_factor: "1" },
  { enabled: false, name: "rh", plot_type: "contourf", style: "YlGnBu_r", levels: "0:101:10", extend: "both", sigma: "", scale_factor: "1" },
  { enabled: false, name: "wa", plot_type: "contour", style: "darkred", levels: "-2|-1.5|-1|-0.5", extend: "both", sigma: "4", scale_factor: "1" },
];

const defaultQuiverVariables = [
  { enabled: false, name: "ua", color: "k", interval: "10", scale: "", scale_factor: "1" },
  { enabled: false, name: "va", color: "k", interval: "10", scale: "", scale_factor: "1" },
];

const researchAnalysisDefaults = {
  ls_direction_means: {
    description: "For every direction and surface, area-average the storm-centered polar fields over the selected LS sector and radius, then average over the selected times.",
    variables: [
      { enabled: true, name: "bl_mfc", plot_type: "line", style: "", scale_factor: "3600", use_polar: "true", label: "BL MFC", units: "mm h-1 equivalent" },
      { enabled: true, name: "convergence", plot_type: "line", style: "", scale_factor: "10000", use_polar: "true", height: "500", label: "500-m convergence", units: "10^-4 s^-1" },
      { enabled: true, name: "qflux_r", plot_type: "line", style: "", scale_factor: "-1000", use_polar: "true", height: "500", label: "500-m inward moisture flux", units: "g m^-2 s^-1" },
      { enabled: true, name: "RAINNCV", plot_type: "line", style: "", scale_factor: "1500", use_polar: "true", label: "Rain rate", units: "mm h^-1" },
    ],
  },
  timing_alignment_180_vs_270: {
    description: "For 180° and 270° sea/land members, compare the timing of area-averaged LS-sector diagnostics after within-case min-max normalization.",
    variables: [
      { enabled: true, name: "bl_mfc", plot_type: "line", style: "", scale_factor: "3600", use_polar: "true", label: "BL MFC", units: "mm h-1 equivalent" },
      { enabled: true, name: "convergence", plot_type: "line", style: "", scale_factor: "10000", use_polar: "true", height: "500", label: "500-m convergence", units: "10^-4 s^-1" },
      { enabled: true, name: "qflux_r", plot_type: "line", style: "", scale_factor: "-1000", use_polar: "true", height: "500", label: "500-m inward moisture flux", units: "g m^-2 s^-1" },
      { enabled: true, name: "wa", plot_type: "line", style: "", scale_factor: "1", use_polar: "true", height: "3000", label: "3-km ascent", units: "m s^-1" },
      { enabled: true, name: "RAINNCV", plot_type: "line", style: "", scale_factor: "1500", use_polar: "true", label: "Rain rate", units: "mm h^-1" },
    ],
  },
};

function requiredElement(selector) {
  const element = document.querySelector(selector);
  if (!element) {
    throw new Error(`Missing page element: ${selector}`);
  }
  return element;
}

function canEditFrontend() {
  return globalThis.WRFWebVisAccess?.canWrite?.() === true;
}

// 2026-11-06：root 之外，账号可单独勾“可看绘图设置”“可出图”（自定义代码仍只给 root）。
function canViewPlotSettingsFrontend() {
  return canEditFrontend() || globalThis.WRFWebVisAccess?.canViewPlotSettings?.() === true;
}

function canPlotFrontend() {
  return canEditFrontend() || globalThis.WRFWebVisAccess?.canPlot?.() === true;
}

// 能看绘图设置的账号单击预览图打开绘图参数；其他账号单击直接放大。
function openImageFromPreview(image) {
  if (image?.request && canViewPlotSettingsFrontend()) openImageSettings(image);
  else openImageZoom(image);
}

function syncPlotDialogPermissions() {
  const writable = canPlotFrontend();
  const root = canEditFrontend();
  const custom = Number(figTypeSelect.value) === 9;
  const blocked = !writable || (custom && !root);
  submitPlotButton.disabled = blocked;
  submitPlotButton.textContent = !writable ? "只读展示：无法提交" : (custom ? (root ? "运行出图" : "自定义代码只有 root 能运行") : "Create image");
  customCodeEl.readOnly = !root;
  document.querySelector("#customCodeExample").disabled = !root;
  openFormButton.textContent = writable ? "添加图片" : "查看绘图界面";
  plotDialogTitle.textContent = writable ? "添加图片" : "绘图参数（只读展示）";
}

// 只处理切换页面的标签；“示意图”是外链按钮，没有 data-view。
document.querySelectorAll(".tab[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab[data-view]").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector(`#${button.dataset.view}`).classList.add("active");
  });
});

document.querySelector("#newDashboard").addEventListener("click", async () => {
  await createAndSelectDashboard();
});

deleteDashboardButton.addEventListener("click", deleteCurrentDashboard);
newChildDashboardButton?.addEventListener("click", () => createChildDashboard().catch((error) => setStatus(error.message)));
moveDashboardSelect?.addEventListener("change", () => moveCurrentDashboard().catch((error) => setStatus(error.message)));

figTypeSelect.addEventListener("change", updateFigTypeFields);
window.addEventListener("online", flushVariableMemory);
window.addEventListener("pagehide", () => {
  if (!globalThis.WRFWebVisAccess?.canWrite()) return;
  for (const [kind, rows] of pendingVariableMemory) {
    fetch(`/api/variable-preferences/${kind}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rows), keepalive: true }).catch(() => {});
  }
});
document.querySelector("#addVariable").addEventListener("click", () => addVariableRow({ enabled: true, name: "", plot_type: "contourf", style: "", levels: "", extend: "both", sigma: "", scale_factor: "1" }));
document.querySelector("#addQuiverVariable").addEventListener("click", () => addQuiverRow({ enabled: true, name: "", color: "k", interval: "10", scale: "", scale_factor: "1" }));

openFormButton.addEventListener("click", async () => {
  try {
    if (canEditFrontend()) await ensureCurrentDashboard();
    syncPlotDialogPermissions();
    formStatusEl.textContent = canPlotFrontend() ? "" : "只读展示：参数调整仅在当前页面有效，不能提交绘图。";
    dialog.showModal();
  } catch (error) {
    setStatus(error.message);
  }
});
document.querySelector("#closeForm").addEventListener("click", () => dialog.close());
document.querySelector("#clearProgress").addEventListener("click", () => {
  progressPanel.classList.remove("open");
  progressMessages.innerHTML = "";
});
cancelProgressButton.addEventListener("click", cancelCurrentJob);
progressJobSelect.addEventListener("change", () => {
  currentProgressJob = progressJobSelect.value;
  renderJobList();
});
document.querySelector("#closeZoom").addEventListener("click", () => { if (globalThis.WRFWebVisAnnotations?.closeEditor() === false) return; imageZoomDialog.close(); });
imageZoomDialog.addEventListener("cancel", (event) => {
  if (globalThis.WRFWebVisAnnotations?.closeEditor() === false) event.preventDefault();
});
document.querySelector("#undoToast button")?.addEventListener("click", runUndo);
document.querySelector("#undoToast [data-dismiss]")?.addEventListener("click", hideUndoToast);
document.addEventListener("keydown", (event) => {
  // 删除后 20 秒内按 ⌘Z / Ctrl+Z 也能撤销（在输入框里打字时不拦截）
  if (!undoAction || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
  if (event.target.closest?.("input, textarea, [contenteditable]")) return;
  event.preventDefault();
  runUndo();
});
document.querySelector("#openTrash")?.addEventListener("click", openTrashDialog);
document.querySelector("#closeTrash")?.addEventListener("click", () => document.querySelector("#trashDialog").close());
document.querySelector("#toggleAnnotations")?.addEventListener("click", (event) => {
  const annotations = globalThis.WRFWebVisAnnotations;
  if (!annotations) return;
  annotations.setVisible(!annotations.visible);
  event.currentTarget.textContent = annotations.visible ? "隐藏批注" : "显示批注";
});
imageZoomDialog.addEventListener("close", () => zoomImageEl.removeAttribute("src"));
stopJobButton?.addEventListener("click", cancelCurrentJob);
document.querySelector("#openProgress").addEventListener("click", () => {
  progressPanel.classList.add("open");
  renderJobList();
});
openLogsButton.addEventListener("click", openRecentLogs);
document.querySelector("#refreshLogs").addEventListener("click", refreshRecentLogs);
document.querySelector("#closeLogs").addEventListener("click", () => logDialog.close());
logDialog.addEventListener("close", stopRecentLogRefresh);
document.querySelector("#autoColorbarPosition").addEventListener("click", setAutoColorbarPosition);

document.querySelector("#dryRun").addEventListener("click", async () => {
  try {
    const custom = Number(figTypeSelect.value) === 9;
    setStatus(custom ? "正在检查代码…" : "Checking data...");
    const payload = buildPlotRequest();
    const result = await api("/api/plots/dry-run", { method: "POST", body: payload });
    if (result.code_checked) {
      setStatus(`代码语法检查通过（${result.environment}）；数据路径及依赖将在运行时检查。`);
      return;
    }
    const blankSlices = result.plan.blank_slices || [];
    setStatus(`Missing files: ${result.plan.missing_files.length}` + (blankSlices.length ? `；留空切片 ${blankSlices.length} 个` : ""));
    showProgressMessages([
      ...result.plan.missing_files.map((item) => `missing: ${item.path}`),
      ...blankSlices.map((item) => `留空: ${item.variable} ${item.time_hour ?? ""} h ${item.height_m ?? ""}`.trim()),
    ]);
  } catch (error) {
    setStatus(error.message);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canPlotFrontend()) {
    setStatus("只读展示：不能提交绘图。");
    return;
  }
  if (Number(figTypeSelect.value) === 9 && !canEditFrontend()) {
    setStatus("自定义代码图只有 root 能运行。");
    return;
  }
  if (!currentDashboard) return;
  const dashboardId = currentDashboard.id;
  try {
    setStatus("Creating image...");
    const payload = buildPlotRequest();
    const job = await api(`/api/dashboards/${dashboardId}/images/jobs`, { method: "POST", body: payload });
    dialog.close();
    await watchJob(job.job_id, result => handleJobFinished(result, dashboardId), { dashboardId, title: payload.title });
  } catch (error) {
    setStatus(error.message);
  }
});

async function loadDashboards() {
  // 侧栏只要标题和层级；完整看板（含每张图的出图参数）只在打开时取（2026-11-06 加速）
  dashboards = await api("/api/dashboards?summary=1");
  renderDashboardList();
  if (!currentDashboard && dashboards.length) {
    await selectDashboard(dashboards[0].id);
  }
}

// ---------------------------------------------------------------- 子看板（2026-11-06，只有两级）
// 父看板看不到（没给这个账号）时，子看板当作顶层显示。
function dashboardTree(list) {
  const ids = new Set(list.map((item) => item.id));
  const children = new Map();
  const roots = [];
  for (const item of list) {
    if (item.parent_id && ids.has(item.parent_id)) {
      if (!children.has(item.parent_id)) children.set(item.parent_id, []);
      children.get(item.parent_id).push(item);
    } else {
      roots.push(item);
    }
  }
  return { roots, children };
}

function collapsedDashboards() {
  try { return new Set(JSON.parse(localStorage.getItem(collapsedDashboardsKey) || "[]")); } catch (_) { return new Set(); }
}

function setDashboardCollapsed(id, collapsed) {
  const ids = collapsedDashboards();
  if (collapsed) ids.add(id); else ids.delete(id);
  try { localStorage.setItem(collapsedDashboardsKey, JSON.stringify([...ids])); } catch (_) { /* 无存储时只在本页有效 */ }
}

function dashboardButton(dashboard, child = false) {
  const button = document.createElement("button");
  button.className = `dashboard-item${child ? " child" : ""}${currentDashboard && currentDashboard.id === dashboard.id ? " active" : ""}`;
  button.textContent = dashboard.title;
  button.title = dashboard.title;
  button.addEventListener("click", () => selectDashboard(dashboard.id));
  acceptImageDrop(button, dashboard);
  return button;
}

// ---------------------------------------------------------------- 把图片拖到别的看板（2026-11-06）
// 拖动图片到侧栏的看板名、或标题下方的子看板/父看板按钮上松开 = 把图片（连同图上批注）移到那个看板末尾。
function acceptImageDrop(element, dashboard) {
  const canDrop = () => canEditFrontend() && draggedImage && !imageOrderSaving && draggedImage.dashboardId !== dashboard.id;
  element.addEventListener("dragover", event => {
    if (!canDrop()) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    element.classList.add("image-drop-target");
  });
  element.addEventListener("dragleave", () => element.classList.remove("image-drop-target"));
  element.addEventListener("drop", async event => {
    element.classList.remove("image-drop-target");
    if (!canDrop()) return;
    event.preventDefault();
    const { dashboardId, imageId } = draggedImage;
    draggedImage = null;
    suppressImageClickUntil = Date.now() + 250;
    await transferImage(dashboardId, imageId, dashboard.id, { undo: true });
  });
}

async function transferImage(fromId, imageId, toId, { undo = false } = {}) {
  const target = dashboards.find(item => item.id === toId);
  const targetTitle = target?.title || "目标看板";
  const imageTitle = (currentDashboard?.id === fromId ? currentDashboard.images.find(item => item.id === imageId)?.title : "") || "图片";
  imageOrderSaving = true;
  setStatus(`正在把「${imageTitle}」移到「${targetTitle}」…`);
  try {
    await api(`/api/dashboards/${fromId}/images/${imageId}/transfer`, { method: "POST", body: { target_dashboard_id: toId } });
  } catch (error) {
    setStatus(`移动失败：${error.message}`);
    return false;
  } finally {
    imageOrderSaving = false;
  }
  if (currentDashboard) await selectDashboard(currentDashboard.id);
  setStatus(`已把「${imageTitle}」移到「${targetTitle}」`);
  if (undo) showUndoToast(`已移到「${targetTitle}」`, () => transferImage(toId, imageId, fromId));
  return true;
}

function renderDashboardList() {
  dashboardsEl.innerHTML = "";
  const { roots, children } = dashboardTree(dashboards);
  const collapsed = collapsedDashboards();
  for (const dashboard of roots) {
    const kids = children.get(dashboard.id) || [];
    const row = document.createElement("div");
    row.className = "dashboard-row";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "dashboard-toggle";
    const holdsCurrent = kids.some((item) => item.id === currentDashboard?.id);
    const isOpen = kids.length && (!collapsed.has(dashboard.id) || holdsCurrent);
    if (kids.length) {
      toggle.textContent = isOpen ? "▾" : "▸";
      toggle.title = isOpen ? "收起子看板" : `展开 ${kids.length} 个子看板`;
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      toggle.addEventListener("click", () => { setDashboardCollapsed(dashboard.id, isOpen); renderDashboardList(); });
    } else {
      toggle.disabled = true;
      toggle.classList.add("empty");
      toggle.setAttribute("aria-hidden", "true");
    }
    const button = dashboardButton(dashboard);
    if (kids.length) button.dataset.count = String(kids.length);
    row.append(toggle, button);
    dashboardsEl.appendChild(row);
    if (kids.length && isOpen) {
      const group = document.createElement("div");
      group.className = "dashboard-children";
      kids.forEach((item) => group.appendChild(dashboardButton(item, true)));
      dashboardsEl.appendChild(group);
    }
  }
}

// 标题下方：父看板列出子看板；子看板显示“属于哪个父看板”。root 另有“新建子看板”“移动到…”。
function renderDashboardFamily() {
  if (!dashboardFamilyEl) return;
  dashboardFamilyEl.replaceChildren();
  if (!currentDashboard) { dashboardFamilyEl.hidden = true; return; }
  const parent = dashboards.find((item) => item.id === currentDashboard.parent_id);
  const kids = dashboards.filter((item) => item.parent_id === currentDashboard.id);
  if (parent) {
    const label = document.createElement("span"); label.textContent = "属于：";
    const link = document.createElement("button"); link.type = "button"; link.className = "family-link";
    link.textContent = parent.title;
    link.addEventListener("click", () => selectDashboard(parent.id));
    acceptImageDrop(link, parent);
    dashboardFamilyEl.append(label, link);
  } else if (kids.length) {
    const label = document.createElement("span"); label.textContent = "子看板：";
    dashboardFamilyEl.append(label);
    for (const kid of kids) {
      const link = document.createElement("button"); link.type = "button"; link.className = "family-link";
      link.textContent = `${kid.title}（${kid.image_count ?? (kid.images || []).length}）`;
      link.addEventListener("click", () => selectDashboard(kid.id));
      acceptImageDrop(link, kid);
      dashboardFamilyEl.append(link);
    }
  }
  dashboardFamilyEl.hidden = !dashboardFamilyEl.childElementCount;
  const topLevel = !currentDashboard.parent_id;
  if (newChildDashboardButton) newChildDashboardButton.hidden = !topLevel;
  if (moveDashboardSelect) {
    moveDashboardSelect.replaceChildren();
    const add = (value, text) => { const option = document.createElement("option"); option.value = value; option.textContent = text; moveDashboardSelect.append(option); };
    add("", "移动到…");
    if (currentDashboard.parent_id) add("__top__", "移到顶层");
    for (const item of dashboards) {
      if (item.parent_id || item.id === currentDashboard.id || item.id === currentDashboard.parent_id) continue;
      add(item.id, `放到「${item.title}」下`);
    }
    // 自己有子看板时不能再挂到别人下面（只支持两级）
    moveDashboardSelect.hidden = kids.length > 0 || moveDashboardSelect.options.length <= 1;
    moveDashboardSelect.value = "";
  }
}

async function createChildDashboard() {
  if (!currentDashboard || currentDashboard.parent_id) return;
  const title = prompt(`在「${currentDashboard.title}」下新建子看板，名称：`, "");
  if (!title || !title.trim()) return;
  const parentId = currentDashboard.id;
  const child = await api("/api/dashboards", { method: "POST", body: { title: title.trim(), parent_id: parentId } });
  setDashboardCollapsed(parentId, false);
  await selectDashboard(child.id);
  setStatus(`已新建子看板「${child.title}」`);
}

async function moveCurrentDashboard() {
  if (!currentDashboard || !moveDashboardSelect?.value) return;
  const value = moveDashboardSelect.value;
  const parentId = value === "__top__" ? null : value;
  const id = currentDashboard.id;
  try {
    await api(`/api/dashboards/${id}/parent`, { method: "PATCH", body: { parent_id: parentId } });
  } finally {
    moveDashboardSelect.value = "";
  }
  if (parentId) setDashboardCollapsed(parentId, false);
  await selectDashboard(id);
  setStatus(parentId ? "已移动到父看板下" : "已移到顶层");
}

async function ensureCurrentDashboard() {
  if (currentDashboard) return currentDashboard;

  await loadDashboards();
  if (currentDashboard) return currentDashboard;

  return createAndSelectDashboard();
}

async function createAndSelectDashboard() {
  const title = `Dashboard ${new Date().toLocaleString()}`;
  const dashboard = await api("/api/dashboards", { method: "POST", body: { title } });
  await selectDashboard(dashboard.id);
  return currentDashboard;
}

async function selectDashboard(id) {
  currentDashboard = await api(`/api/dashboards/${id}?light=1`);
  dashboardTitleEl.textContent = currentDashboard.title;
  dashboardTitleEl.onclick = () => { if (canEditFrontend()) renameCurrentDashboard(); };
  openFormButton.disabled = false;
  renameDashboardButton.disabled = false;
  deleteDashboardButton.disabled = false;
  renameDashboardButton.onclick = renameCurrentDashboard;
  renderImages();
  globalThis.WRFWebVisAnnotations?.loadDashboard(currentDashboard.id);
  dashboardRevision = null;
  rememberDashboardRevision(currentDashboard.id);
  await loadDashboards();
  renderDashboardFamily();
}

// 看板自动刷新：AI 或其他人加了图、改了批注时，当前看板会自动更新（每 8 秒检查一次）。
let dashboardRevision = null;
async function rememberDashboardRevision(id) {
  try {
    const result = await api(`/api/dashboards/${id}/revision`, { quiet: true });
    if (currentDashboard?.id === id) dashboardRevision = result.revision;
  } catch (_) { /* 网络问题时下次再试 */ }
}

async function pollDashboardRevision() {
  const view = document.querySelector("#dashboardView");
  if (!currentDashboard || document.hidden || !view?.classList.contains("active")) return;
  if (imageZoomDialog.open || dialog.open || draggedImage || imageOrderSaving) return;
  // 正在写图片批注时不刷新，免得把没保存的文字冲掉
  if (document.querySelector('.card-note textarea[data-dirty="true"]') || document.activeElement?.closest?.(".card-note")) return;
  const id = currentDashboard.id;
  try {
    const result = await api(`/api/dashboards/${id}/revision`, { quiet: true });
    if (currentDashboard?.id !== id) return;
    if (dashboardRevision && result.revision !== dashboardRevision) {
      dashboardRevision = result.revision;
      await selectDashboard(id);
      setStatus("看板已更新");
    } else dashboardRevision = result.revision;
  } catch (_) { /* 网络问题时下次再试 */ }
}
setInterval(pollDashboardRevision, 8000);

// 卡片上显示缩略图（公网隧道带宽有限）；放大、批注编辑用原图。缩略图取不到时退回原图。
function thumbnailUrl(image) {
  const path = String(image?.image_path || "");
  const match = path.match(/^\/static\/figures\/([A-Za-z0-9_.-]+\.(?:png|jpe?g))$/i);
  const version = encodeURIComponent(image?.id || "");
  return match ? `/api/figures/thumb/${match[1]}?w=800&v=${version}` : `${path}?v=${version}`;
}

// root 能删所有图；勾了“可出图”的账号只能删自己出的图（后端也会检查）。
function canDeleteImage(image) {
  if (canEditFrontend()) return true;
  const me = globalThis.WRFWebVisAccess?.user?.().username;
  return canPlotFrontend() && Boolean(me) && image?.created_by === me;
}

function renderImages() {
  imageGridEl.innerHTML = "";
  ((currentDashboard && currentDashboard.images) || []).forEach((image) => {
    const card = document.createElement("article");
    card.className = "image-card";
    card.dataset.imageId = image.id;
    const recreateAction = isResearchAnalysisRequest(image.request)
      ? `<button data-write-only data-recreate="${image.id}">Recreate</button>`
      : "";
    card.innerHTML = `
      <header>
        <div class="image-title-row">
          <h3>${escapeHtml(image.title)}</h3>
          <button data-zoom="${image.id}">Enlarge</button>
        </div>
        <div class="card-actions">
          ${recreateAction}
          <button data-write-only data-rename="${image.id}">Rename</button>
          <button ${canDeleteImage(image) ? "" : "data-write-only "}data-delete="${image.id}">Delete</button>
        </div>
      </header>
      <img src="${escapeHtml(thumbnailUrl(image))}" loading="lazy" decoding="async" alt="${escapeHtml(image.title)}" />
      <div class="card-footer">
        <details class="card-metadata">
          <summary>metadata</summary>
          <pre>${escapeHtml(JSON.stringify(image.metadata, null, 2))}</pre>
        </details>
        <details class="card-note"${image.note ? " open" : ""}>
          <summary>批注${image.note ? "" : "（空）"}</summary>
          <div class="card-note-text">${escapeHtml(image.note || "")}</div>
          <div class="card-note-editor" data-annotate-only>
            <textarea rows="3" placeholder="写下对这张图的看法、结论或要核对的问题">${escapeHtml(image.note || "")}</textarea>
            <div class="card-note-actions">
              <button type="button" data-note-save>保存</button>
              <button type="button" data-annotate title="放大图片，在图上画箭头、框和文字">在图上画</button>
              <span class="card-note-status"></span>
            </div>
          </div>
        </details>
      </div>
    `;
    const preview = card.querySelector("img");
    preview.addEventListener("error", () => {
      const original = `${image.image_path}?v=${encodeURIComponent(image.id)}`;
      if (!preview.src.endsWith(original)) preview.src = original;
    }, { once: true });
    preview.title = "单击查看绘图参数；拖动可调整顺序，拖到左侧或标题下方的看板名上可移到那个看板";
    preview.draggable = canEditFrontend();
    globalThis.WRFWebVisAccess?.ready?.then(() => {
      if (preview.isConnected) preview.draggable = canEditFrontend();
    });
    enableImageSorting(card, image);
    preview.addEventListener("click", () => {
      if (Date.now() > suppressImageClickUntil) {
        openImageFromPreview(image);
      }
    });
    card.querySelector("[data-zoom]").addEventListener("click", () => openImageZoom(image));
    globalThis.WRFWebVisAnnotations?.attach(preview, image);
    const recreateButton = card.querySelector("[data-recreate]");
    if (recreateButton) recreateButton.addEventListener("click", () => recreateResearchAnalysisImage(image));
    card.querySelector("[data-delete]").addEventListener("click", () => deleteImageWithUndo(image));
    const noteBox = card.querySelector(".card-note textarea");
    noteBox?.addEventListener("input", () => { noteBox.dataset.dirty = "true"; });
    card.querySelector("[data-note-save]")?.addEventListener("click", () => saveImageNote(image, noteBox, card));
    card.querySelector("[data-annotate]")?.addEventListener("click", () => openImageZoom(image));
    card.querySelector("[data-rename]").addEventListener("click", async () => {
      const title = prompt("Image title", image.title);
      if (!title) return;
      await api(`/api/dashboards/${currentDashboard.id}/images/${image.id}`, { method: "PATCH", body: { title } });
      await selectDashboard(currentDashboard.id);
    });
    imageGridEl.appendChild(card);
  });
}

function enableImageSorting(card, image) {
  const dashboardId = currentDashboard.id;
  card.addEventListener("dragstart", event => {
    if (!canEditFrontend() || imageOrderSaving || event.target.tagName !== "IMG") {
      event.preventDefault();
      return;
    }
    draggedImage = { dashboardId, imageId: image.id };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", image.id);
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    draggedImage = null;
    suppressImageClickUntil = Date.now() + 250;
    card.classList.remove("dragging");
    card.classList.remove("drop-before", "drop-after");
  });
  card.addEventListener("dragover", event => {
    if (!canEditFrontend()) return;
    if (!draggedImage || imageOrderSaving || draggedImage.dashboardId !== dashboardId || draggedImage.imageId === image.id) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const rect = card.getBoundingClientRect();
    card.classList.remove("drop-before", "drop-after");
    card.classList.add(event.clientX < rect.left + rect.width / 2 ? "drop-before" : "drop-after");
  });
  card.addEventListener("dragleave", () => card.classList.remove("drop-before", "drop-after"));
  card.addEventListener("drop", async event => {
    if (!canEditFrontend()) return;
    card.classList.remove("drop-before", "drop-after");
    if (!draggedImage || imageOrderSaving || draggedImage.dashboardId !== dashboardId || currentDashboard?.id !== dashboardId) return;
    event.preventDefault();
    const imageId = draggedImage.imageId;
    draggedImage = null;
    suppressImageClickUntil = Date.now() + 250;
    if (imageId === image.id) return;
    const remaining = currentDashboard.images.filter(item => item.id !== imageId);
    const targetIndex = remaining.findIndex(item => item.id === image.id);
    if (targetIndex < 0) return;
    const rect = card.getBoundingClientRect();
    const beforeId = event.clientX < rect.left + rect.width / 2
      ? image.id : (remaining[targetIndex + 1]?.id ?? null);
    imageOrderSaving = true;
    setStatus("正在保存公共图片顺序…");
    try {
      const updated = await api(`/api/dashboards/${dashboardId}/images/${imageId}/order`, {
        method: "PATCH", body: { before_id: beforeId },
      });
      if (currentDashboard?.id === dashboardId) {
        currentDashboard = updated;
        renderImages();
      }
      setStatus("公共图片顺序已保存，其他人刷新后可见");
    } catch (error) {
      setStatus(`图片顺序保存失败，请刷新后重试：${error.message}`);
    } finally {
      imageOrderSaving = false;
    }
  });
}

// ---------------------------------------------------------------- 删除图片可撤销、图片文字批注
let undoToastTimer = null;
let undoAction = null;

async function deleteImageWithUndo(image) {
  if (!currentDashboard) return;
  const dashboardId = currentDashboard.id;
  try {
    await api(`/api/dashboards/${dashboardId}/images/${image.id}`, { method: "DELETE" });
    await selectDashboard(dashboardId);
    showUndoToast(`已删除「${image.title}」`, () => restoreDeletedImage(dashboardId, image.id));
  } catch (error) {
    setStatus(error.message);
  }
}

async function restoreDeletedImage(dashboardId, imageId) {
  try {
    await api(`/api/dashboards/${dashboardId}/images/${imageId}/restore`, { method: "POST" });
    if (currentDashboard?.id === dashboardId) await selectDashboard(dashboardId);
    setStatus("已恢复图片");
    return true;
  } catch (error) {
    setStatus(`恢复失败：${error.message}`);
    return false;
  }
}

function showUndoToast(message, action) {
  const toast = document.querySelector("#undoToast");
  if (!toast) return;
  toast.querySelector("span").textContent = message;
  undoAction = action;
  toast.hidden = false;
  clearTimeout(undoToastTimer);
  undoToastTimer = setTimeout(hideUndoToast, 20000);
}

function hideUndoToast() {
  const toast = document.querySelector("#undoToast");
  if (toast) toast.hidden = true;
  undoAction = null;
}

async function runUndo() {
  const action = undoAction;
  hideUndoToast();
  if (action) await action();
}

async function openTrashDialog() {
  if (!currentDashboard) return;
  const trashDialog = document.querySelector("#trashDialog");
  const list = document.querySelector("#trashList");
  const dashboardId = currentDashboard.id;
  list.textContent = "正在读取…";
  trashDialog.showModal();
  try {
    const rows = await api(`/api/dashboards/${dashboardId}/deleted-images`);
    list.innerHTML = "";
    if (!rows.length) {
      list.textContent = "这个看板没有最近删除的图片。";
      return;
    }
    for (const row of rows) {
      const item = document.createElement("div");
      item.className = "trash-item";
      item.innerHTML = `<img src="${escapeHtml(row.image_path)}" loading="lazy" alt="" /><div><strong>${escapeHtml(row.title)}</strong><small>删除于 ${escapeHtml(String(row.deleted_at).replace("T", " "))}</small></div><button type="button" class="primary">恢复</button>`;
      item.querySelector("button").addEventListener("click", async () => {
        if (await restoreDeletedImage(dashboardId, row.id)) item.remove();
        if (!list.querySelector(".trash-item")) list.textContent = "这个看板没有最近删除的图片。";
      });
      list.appendChild(item);
    }
  } catch (error) {
    list.textContent = error.message;
  }
}

// 保存图片的文字批注；卡片下方“批注”栏和放大窗口右栏共用这一个函数
async function persistImageNote(image, text) {
  if (!currentDashboard) throw new Error("没有打开的看板");
  const dashboardId = currentDashboard.id;
  const updated = await api(`/api/dashboards/${dashboardId}/images/${image.id}/note`, { method: "PUT", body: { note: text } });
  const saved = (updated.images || []).find((item) => item.id === image.id);
  image.note = saved?.note || "";
  rememberDashboardRevision(dashboardId);
  syncCardNote(image);
  return image.note;
}

function syncCardNote(image) {
  const card = [...imageGridEl.querySelectorAll(".image-card")].find((node) => node.dataset.imageId === image.id);
  if (!card) return;
  card.querySelector(".card-note-text").textContent = image.note;
  card.querySelector(".card-note summary").textContent = image.note ? "批注" : "批注（空）";
  const textarea = card.querySelector(".card-note textarea");
  if (textarea) { textarea.value = image.note; delete textarea.dataset.dirty; }
  if (image.note) card.querySelector(".card-note").open = true;
}

async function saveImageNote(image, textarea, card) {
  if (!currentDashboard || !textarea) return;
  const status = card.querySelector(".card-note-status");
  try {
    status.textContent = "正在保存…";
    await persistImageNote(image, textarea.value);
    status.textContent = "已保存";
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
  }
}

async function handleJobFinished(result, dashboardId) {
  if (dashboardId && currentDashboard?.id === dashboardId) await selectDashboard(dashboardId);
  const status = { complete: "Image created", canceled: "Image canceled", failed: "Image failed" };
  setStatus(`${result.title || result.id}: ${status[result.status] || result.status}`);
}

function isResearchAnalysisRequest(request) {
  return Boolean(request && request.extra && request.extra.analysis_type);
}

async function recreateResearchAnalysisImage(image) {
  if (!currentDashboard || !isResearchAnalysisRequest(image.request)) return;
  const dashboardId = currentDashboard.id;
  setStatus("Recreating research analysis image...");
  const request = { ...image.request, dashboard_id: dashboardId };
  const job = await api(`/api/dashboards/${dashboardId}/images/jobs`, { method: "POST", body: request });
  await watchJob(job.job_id, result => handleJobFinished(result, dashboardId), { dashboardId, title: request.title });
}

function openImageZoom(image) {
  if (!image || !image.image_path) return;
  if (image.metadata && image.metadata.html_static_path) {
    window.open(image.metadata.html_static_path, "_blank", "noopener");
    return;
  }
  zoomTitleEl.textContent = image.title || "Image preview";
  zoomImageEl.src = `${image.image_path}?v=${encodeURIComponent(image.id || Date.now())}`;
  imageZoomDialog.showModal();
  globalThis.WRFWebVisAnnotations?.openEditor(image, { saveNote: (text) => persistImageNote(image, text) });
}

function openImageSettings(image) {
  if (!image || !image.request) {
    setStatus("This image has no saved settings.");
    return;
  }
  populatePlotFormFromRequest(image.request, image.title);
  syncPlotDialogPermissions();
  formStatusEl.textContent = canPlotFrontend() ? "" : "只读展示：参数调整仅在当前页面有效，不能提交绘图。";
  dialog.showModal();
}

function populatePlotFormFromRequest(request, imageTitle) {
  saveVariablePreferences();
  setInputValue("title", request.title || imageTitle || "WRF plot");
  if (figTypeToFormValue(request.fig_type) === "9") {
    setInputValue("fig_type", "9");
    setInputValue("dpi", request.dpi == null ? 200 : request.dpi);
    setInputValue("custom_code", request.custom_code || "");
    saveCustomCodeDraft();
    updateFigTypeFields();
    return;
  }
  const selectionMembers = request.selection && Array.isArray(request.selection.members) ? request.selection.members : null;
  setInputValue("member_names", selectionMembers ? selectionMembers.join(",") : Array.isArray(request.member_names) ? request.member_names.join(",") : request.member_name || "");
  setInputValue("center_schemes", Array.isArray(request.center_schemes) ? request.center_schemes.join(",") : "");
  setInputValue("data_root", normalizeDataRoot(request.processed_root || request.data_root));
  setInputValue("domain", request.domain == null ? 3 : request.domain);
  setInputValue("row", request.row == null ? 1 : request.row);
  setInputValue("col", request.col == null ? 1 : request.col);
  setInputValue("panel_by", request.panel_by || "");
  setInputValue("dpi", request.dpi == null ? 200 : request.dpi);
  setInputValue("output_format", request.output_format || "png");
  setInputValue("polar_dir_style", request.polar_dir_style || "auto");
  setInputValue("polar_max_radius_km", request.polar_max_radius_km == null ? "380" : request.polar_max_radius_km);
  setInputValue("polar_radius_resolution_km", request.polar_radius_resolution_km == null ? "" : request.polar_radius_resolution_km);
  setInputValue("fig_type", figTypeToFormValue(request.fig_type));

  setInputValue("time", selectionToText(request.time, "0"));
  setInputValue("height", selectionToText(request.height, ""));
  setInputValue("angle", selectionToText(request.angle, "0,360"));
  setInputValue("radius", selectionToText(request.radius, "0,200"));

  populateFormatFields(formatWithMemberTimeLimits(request.format || {}, request.selection || {}));
  if (Array.isArray(request.figsize)) {
    setInputValue("fig_width", request.figsize[0]);
    setInputValue("fig_height", request.figsize[1]);
  }
  populateVariableRowsFromRequest(request);
  updateFigTypeFields();
}

function populateFormatFields(format) {
  const common = format.common || {};
  const horizontal = format.horizontal || {};
  const timeRadius = format.time_radius || {};
  const timeAzimuth = format.time_azimuth || {};
  const timeHeight = format.time_height || {};
  const timeSeries = format.time_series || {};
  const track = format.track || {};
  const threeD = format.three_d || {};
  const formatFigsize = firstFormatFigsize(format);
  const colorbar = format.colorbar || {};
  setInputValue("title_template", common.title || "{member} {time} {height} {variables}");
  setInputValue("fig_width", Array.isArray(formatFigsize) ? formatFigsize[0] : 9);
  setInputValue("fig_height", Array.isArray(formatFigsize) ? formatFigsize[1] : 8);
  setInputValue("xlim_km", horizontal.xlim_km == null ? 200 : horizontal.xlim_km);
  setInputValue("tick_interval_km", horizontal.tick_interval_km == null ? 50 : horizontal.tick_interval_km);
  setInputValue("time_radius_xlim", Array.isArray(timeRadius.xlim) ? timeRadius.xlim.join(",") : "");
  setInputValue("time_radius_ylim", Array.isArray(timeRadius.ylim) ? timeRadius.ylim.join(",") : "");
  setInputValue("time_azimuth_ylim", Array.isArray(timeAzimuth.ylim) ? timeAzimuth.ylim.join(",") : "");
  setInputValue("time_radius_radius_interval", timeRadius.radius_interval == null ? 50 : timeRadius.radius_interval);
  setInputValue("time_radius_time_interval", timeRadius.time_interval == null ? 5 : timeRadius.time_interval);
  setInputValue("time_height_xlim", Array.isArray(timeHeight.xlim) ? timeHeight.xlim.join(",") : "");
  setInputValue("time_height_ylim", Array.isArray(timeHeight.ylim) ? timeHeight.ylim.join(",") : "");
  setInputValue("time_height_time_interval", timeHeight.time_interval == null ? 6 : timeHeight.time_interval);
  setInputValue("time_height_height_interval", timeHeight.height_tick_interval_m == null ? 1000 : timeHeight.height_tick_interval_m);
  setInputValue("time_series_xlim", Array.isArray(timeSeries.xlim) ? timeSeries.xlim.join(",") : "");
  setInputValue("time_series_time_interval", timeSeries.time_interval == null ? 6 : timeSeries.time_interval);
  setInputValue("time_series_hide_weak_rmw", timeSeries.hide_weak_rmw ? "true" : "false");
  setInputValue("time_series_rmw_min_axis_mean_wind", timeSeries.rmw_min_axis_mean_wind == null ? 10 : timeSeries.rmw_min_axis_mean_wind);
  setInputValue("time_series_overlay_members", timeSeries.overlay_members ? "true" : "false");
  setInputValue("time_series_scatter", timeSeries.scatter_points ? "true" : "false");
  const shearArrows = timeSeries.shear_arrows || {};
  setInputValue("time_series_shear_arrows", shearArrows.enabled ? "true" : "false");
  setInputValue("time_series_member_rows", shearArrows.member_rows ? "true" : "false");
  setInputValue("track_xlim_km", Array.isArray(track.xlim_km) ? track.xlim_km.join(",") : (track.xlim_km == null ? "" : track.xlim_km));
  setInputValue("track_ylim_km", Array.isArray(track.ylim_km) ? track.ylim_km.join(",") : (track.ylim_km == null ? "" : track.ylim_km));
  setInputValue("track_tick_interval_km", track.tick_interval_km == null ? 100 : track.tick_interval_km);
  setInputValue("track_draw_land_boundary", track.draw_land_boundary === false ? "false" : "true");
  setInputValue("three_d_radius_km", threeD.radius_km == null ? 300 : threeD.radius_km);
  setInputValue("three_d_draw_arrows", threeD.draw_arrows ? "true" : "false");
  setInputValue("three_d_draw_layer_centers", threeD.draw_layer_centers === false ? "false" : "true");
  setInputValue("three_d_arrow_w_min", threeD.arrow_w_min == null ? 1 : threeD.arrow_w_min);
  setInputValue("three_d_arrow_w_max", threeD.arrow_w_max == null ? "" : threeD.arrow_w_max);
  setInputValue("three_d_arrow_interval", threeD.arrow_interval == null ? 12 : threeD.arrow_interval);
  setInputValue("three_d_surface_opacity", threeD.surface_opacity == null ? 0.8 : threeD.surface_opacity);
  setInputValue("radius_circles_km", Array.isArray(horizontal.radius_circles_km) ? horizontal.radius_circles_km.join(",") : "50,100,150,200");
  setInputValue("draw_rmw", horizontal.draw_rmw ? "true" : "false");
  setInputValue("draw_land_distance", horizontal.draw_land_distance ? "true" : "false");
  setInputValue("center_reference", centerReferenceFromFormat(horizontal));
  setInputValue("colorbar_title", colorbar.title || "auto");
  const position = Array.isArray(colorbar.position) ? colorbar.position : [0.94, 0.14, 0.012, 0.72];
  setInputValue("colorbar_left", position[0] == null ? 0.94 : position[0]);
  setInputValue("colorbar_bottom", position[1] == null ? 0.14 : position[1]);
  setInputValue("colorbar_width", position[2] == null ? 0.012 : position[2]);
  setInputValue("colorbar_height", position[3] == null ? 0.72 : position[3]);
  const quiverKey = format.quiver_key || {};
  const quiverPosition = Array.isArray(quiverKey.position) ? quiverKey.position : ["auto", "auto"];
  setInputValue("quiverkey_x", quiverPosition[0] == null ? "auto" : quiverPosition[0]);
  setInputValue("quiverkey_y", quiverPosition[1] == null ? "auto" : quiverPosition[1]);
  setInputValue("quiverkey_u", quiverKey.U == null ? 10 : quiverKey.U);
  setInputValue("quiverkey_label", quiverKey.label || "10 m/s");
  populateAdvancedFormatFields(format);
}

function firstFormatFigsize(format) {
  const keys = ["horizontal", "radius_height", "time_radius", "time_azimuth", "height_azimuth", "time_series", "track", "three_d", "time_height"];
  for (const key of keys) {
    const value = format[key] && format[key].figsize;
    if (Array.isArray(value) && value.length >= 2) return value;
  }
  return null;
}

function setAutoColorbarPosition() {
  setInputValue("colorbar_left", 0.94);
  setInputValue("colorbar_bottom", 0.14);
  setInputValue("colorbar_width", 0.012);
  setInputValue("colorbar_height", 0.72);
  setInputValue("quiverkey_x", "auto");
  setInputValue("quiverkey_y", "auto");
}

function centerReferenceFromFormat(horizontal) {
  if (horizontal.center_enabled === false) return "domain";
  if (horizontal.center_height_m != null) return "height";
  return "slp";
}

function populateVariableRowsFromRequest(request) {
  activeVariableFigType = String(figTypeSelect.value);
  variableRowsEl.innerHTML = "";
  quiverRowsEl.innerHTML = "";
  const variables = Array.isArray(request.variables) ? request.variables : [];
  const quiver = Array.isArray(request.quiver) ? request.quiver : [];
  const analysisType = String((request.extra || {}).analysis_type || "");
  const fallback = researchAnalysisDefaults[analysisType] ? researchAnalysisDefaults[analysisType].variables : defaultPlotVariables;
  const memory = variableMemory[activeVariableFigType] || {};
  VariableMemory.merge((variables.length ? variables : fallback).map(variableRequestToRow), memory.variables)
    .forEach(item => addVariableRow(item, { save: false }));
  VariableMemory.merge((quiver.length ? quiver : defaultQuiverVariables).map(quiverRequestToRow), memory.quiver)
    .forEach(item => addQuiverRow(item, { save: false }));
}

function variableRequestToRow(variable) {
  const plotType = String(variable.plot_type || "contourf");
  const lineColor = String(variable.color == null ? "" : variable.color).trim();
  const row = {
    enabled: variable.enabled !== false,
    name: variable.name || "",
    plot_type: plotType,
    style: plotType === "contour" ? lineColor || "k" : plotType === "line" ? (lineColor.toLowerCase() === "k" ? "" : lineColor) : variable.cmap || "",
    levels: levelsToText(variable.levels),
    extend: variable.extend || "both",
    sigma: variable.sigma == null ? "" : variable.sigma,
    scale_factor: variable.scale_factor == null ? "1" : variable.scale_factor,
    use_polar: variable.use_polar == null ? "auto" : String(variable.use_polar),
    clabel: variable.clabel === false ? "false" : "true",
  };
  ["alpha", "linewidth", "linestyle", "colorbar_title", "missing_value_policy", "label", "marker", "units", "ylabel", "time_smooth_sigma", "angle_smooth_sigma", "angle_wrap", "ytick_start", "ytick_end", "ytick_interval", "ytick_nbins", "integer_yticks"].forEach((field) => {
    if (variable[field] !== undefined && variable[field] !== null) row[field] = variable[field];
  });
  ["ylim", "yticks", "direction_tick_values", "direction_tick_labels", "direction_ylim"].forEach((field) => {
    if (Array.isArray(variable[field])) row[field] = variable[field].join(",");
  });
  ["time", "height", "angle", "radius"].forEach((field) => {
    if (variable[field]) row[field] = selectionToText(variable[field], "");
  });
  return row;
}

function quiverRequestToRow(variable) {
  const extra = variable.extra || {};
  return {
    enabled: variable.enabled !== false,
    name: variable.name || "",
    color: variable.color || "k",
    interval: extra.interval == null ? "" : valueToText(extra.interval),
    scale: extra.scale == null ? "" : extra.scale,
    scale_factor: variable.scale_factor == null ? "1" : variable.scale_factor,
  };
}

function figTypeToFormValue(value) {
  const text = String(value == null ? "0" : value);
  const names = {
    horizontal: "0",
    "radius-height": "1",
    radius_height: "1",
    "time-radius": "2",
    time_radius: "2",
    "time-azimuth": "3",
    time_azimuth: "3",
    "height-azimuth": "4",
    height_azimuth: "4",
    "time-series": "5",
    time_series: "5",
    track: "6",
    "3d": "7",
    three_d: "7",
    "time-height": "8",
    time_height: "8",
    custom_code: "9",
  };
  return names[text] || text;
}

function selectionToText(selection, fallback) {
  if (!selection) return fallback;
  if (Array.isArray(selection.values) && selection.values.length) return selection.values.join(",");
  const end = selection.end == null ? selection.stop : selection.end;
  if (selection.start != null && end != null) {
    return [selection.start, end, selection.step].filter((item) => item != null).join(":");
  }
  return fallback;
}

function levelsToText(levels) {
  return Array.isArray(levels) ? levels.join("|") : "";
}

function valueToText(value) {
  return Array.isArray(value) ? value.join(",") : String(value);
}

function setInputValue(name, value) {
  const input = form.querySelector(`[name="${name}"]`);
  if (input) input.value = value == null ? "" : value;
}

function setInputChecked(name, checked) {
  const input = form.querySelector(`[name="${name}"]`);
  if (input) input.checked = Boolean(checked);
}

async function renameCurrentDashboard() {
  if (!currentDashboard) return;
  const title = prompt("Dashboard title", currentDashboard.title);
  if (!title) return;
  currentDashboard = await api(`/api/dashboards/${currentDashboard.id}`, { method: "PATCH", body: { title } });
  await loadDashboards();
  dashboardTitleEl.textContent = currentDashboard.title;
}

async function deleteCurrentDashboard() {
  if (!currentDashboard) return;
  if (dashboards.some((item) => item.parent_id === currentDashboard.id)) {
    setStatus("这个看板下面还有子看板，请先把子看板移走或删除");
    return;
  }
  if (!confirm(`Delete dashboard "${currentDashboard.title}"?`)) return;

  const dashboardId = currentDashboard.id;
  await api(`/api/dashboards/${dashboardId}`, { method: "DELETE" });
  currentDashboard = null;
  clearCurrentDashboard();
  await loadDashboards();
  setStatus("Dashboard deleted");
}

function clearCurrentDashboard() {
  dashboardTitleEl.textContent = "未选择看板";
  dashboardTitleEl.onclick = null;
  imageGridEl.innerHTML = "";
  renameDashboardButton.disabled = true;
  deleteDashboardButton.disabled = true;
  if (dashboardFamilyEl) { dashboardFamilyEl.replaceChildren(); dashboardFamilyEl.hidden = true; }
  if (newChildDashboardButton) newChildDashboardButton.hidden = true;
  if (moveDashboardSelect) moveDashboardSelect.hidden = true;
}

function buildPlotRequest() {
  const data = new FormData(form);
  const figType = Number(data.get("fig_type"));
  if (figType === 9) {
    const code = String(data.get("custom_code") || "");
    if (!code.trim()) throw new Error("请先粘贴 Python 绘图代码。");
    return {
      dashboard_id: currentDashboard ? currentDashboard.id : null,
      title: String(data.get("title")), fig_type: 9, dpi: Number(data.get("dpi")),
      output_format: "png", custom_code: code,
    };
  }
  const members = parseTextList(data.get("member_names"));
  const format = buildFormatConfig(data, figType);
  const selection = { members, member_time_limits: { ...DEFAULT_MEMBER_TIME_LIMITS } };
  if (figType === 5 && format.time_series.member_end_hours) {
    selection.member_time_limits = format.time_series.member_end_hours;
    selection.member_time_limits = { ...selection.member_time_limits, ...DEFAULT_MEMBER_TIME_LIMITS };
  } else if (figType === 2 && format.time_radius.member_end_hours) {
    selection.member_time_limits = format.time_radius.member_end_hours;
    selection.member_time_limits = { ...selection.member_time_limits, ...DEFAULT_MEMBER_TIME_LIMITS };
  } else if (figType === 3 && format.time_azimuth.member_end_hours) {
    selection.member_time_limits = format.time_azimuth.member_end_hours;
    selection.member_time_limits = { ...selection.member_time_limits, ...DEFAULT_MEMBER_TIME_LIMITS };
  } else if (figType === 8 && format.time_height.member_end_hours) {
    selection.member_time_limits = format.time_height.member_end_hours;
    selection.member_time_limits = { ...selection.member_time_limits, ...DEFAULT_MEMBER_TIME_LIMITS };
  }
  const request = {
    dashboard_id: currentDashboard ? currentDashboard.id : null,
    title: String(data.get("title")),
    data_root: String(data.get("data_root")),
    processed_root: String(data.get("data_root")),
    member_name: members[0] || "WRF_225_land",
    member_names: members,
    domain: Number(data.get("domain")),
    fig_type: figType,
    row: Number(data.get("row")),
    col: Number(data.get("col")),
    panel_by: emptyToNull(data.get("panel_by")),
    output_format: String(data.get("output_format") || "png"),
    dpi: Number(data.get("dpi")),
    variables: figType === 6 ? [] : collectVariableRows(),
    quiver: [6, 8].includes(figType) ? [] : collectQuiverRows(),
    ...selectionPayload(data, figType),
    selection,
    format: formatWithMemberTimeLimits(format, selection),
    polar_dir_style: String(data.get("polar_dir_style") || "auto"),
    polar_max_radius_km: parseOptionalNumber(data.get("polar_max_radius_km")),
    polar_radius_resolution_km: parseOptionalNumber(data.get("polar_radius_resolution_km")),
  };
  // 2026-11-02 中心方案对比：填了 base 以外的方案时，每个试验按方案展开成“试验@方案”成员。
  const centerSchemes = parseTextList(data.get("center_schemes"));
  if (centerSchemes.length && !(centerSchemes.length === 1 && centerSchemes[0] === "base")) request.center_schemes = centerSchemes;
  const figsize = figsizeFromForm(data);
  if (figsize) request.figsize = figsize;
  return request;
}

function addVariableRow(config, options) {
  if (!(options && options.save === false)) switchVariableFigType();
  const saveAfterAdd = !(options && options.save === false);
  const row = document.createElement("div");
  row.className = "variable-row";
  row.innerHTML = `
    <label class="check-cell"><input data-field="enabled" type="checkbox" /></label>
    <input data-field="name" placeholder="dbz" />
    <select data-field="plot_type">
      <option value="contourf">contourf</option>
      <option value="contour">contour</option>
      <option value="line">line</option>
    </select>
    <input data-field="style" placeholder="cmap; blank = preset experiment color" />
    <input data-field="levels" placeholder="10:51:5" />
    <select data-field="extend">
      <option value="both">both</option>
      <option value="max">max</option>
      <option value="min">min</option>
      <option value="neither">neither</option>
    </select>
    <input data-field="sigma" placeholder="2" />
    <input data-field="scale_factor" placeholder="1" />
    <button type="button" class="row-advanced-toggle">设置</button>
    <button type="button" class="row-delete">Delete</button>
    <div class="variable-advanced" hidden>
      <label>alpha<input data-field="alpha" placeholder="1" /></label>
      <label>linewidth<input data-field="linewidth" placeholder="auto" /></label>
      <label>linestyle
        <select data-field="linestyle"><option value="">auto</option><option value="-">-</option><option value="--">--</option><option value="-.">-.</option><option value=":">:</option></select>
      </label>
      <label>contour labels
        <select data-field="clabel"><option value="true">true</option><option value="false">false</option></select>
      </label>
      <label>colorbar title<input data-field="colorbar_title" placeholder="auto" /></label>
      <label>missing values
        <select data-field="missing_value_policy"><option value="interpolate">interpolate</option><option value="mask">mask</option><option value="error">error</option></select>
      </label>
      <label>polar data
        <select data-field="use_polar"><option value="auto">auto</option><option value="true">true</option><option value="false">false</option></select>
      </label>
      <label>variable time<input data-field="time" placeholder="使用全局选择" /></label>
      <label>variable height<input data-field="height" placeholder="使用全局选择" /></label>
      <label>variable angle<input data-field="angle" placeholder="使用全局选择" /></label>
      <label>variable radius<input data-field="radius" placeholder="使用全局选择" /></label>
      <label>legend label<input data-field="label" placeholder="auto" /></label>
      <label>marker<input data-field="marker" placeholder="none / o / s" /></label>
      <label>units<input data-field="units" placeholder="m/s, hPa, degree, km" /></label>
      <label>Y axis label<input data-field="ylabel" placeholder="auto" /></label>
      <label>time smooth sigma<input data-field="time_smooth_sigma" placeholder="none" /></label>
      <label>angle smooth sigma<input data-field="angle_smooth_sigma" placeholder="none" /></label>
      <label>angle wrap
        <select data-field="angle_wrap"><option value="">auto</option><option value="360">0…360</option><option value="-360_0">-360…0</option><option value="none">raw</option></select>
      </label>
      <label>ylim<input data-field="ylim" placeholder="0,120" /></label>
      <label>yticks<input data-field="yticks" placeholder="0,20,40,60" /></label>
      <label>ytick start<input data-field="ytick_start" placeholder="auto" /></label>
      <label>ytick end<input data-field="ytick_end" placeholder="auto" /></label>
      <label>ytick interval<input data-field="ytick_interval" placeholder="auto" /></label>
      <label>integer yticks
        <select data-field="integer_yticks"><option value="">auto</option><option value="true">true</option><option value="false">false</option></select>
      </label>
      <label>ytick bins<input data-field="ytick_nbins" placeholder="6" /></label>
      <label>direction ticks<input data-field="direction_tick_values" placeholder="-360,-270,-180,-90,0" /></label>
      <label>direction labels<input data-field="direction_tick_labels" placeholder="US,RS,DS,LS,US" /></label>
      <label>direction ylim<input data-field="direction_ylim" placeholder="-360,0" /></label>
    </div>
  `;
  fillRow(row, {
    clabel: true,
    missing_value_policy: "interpolate",
    use_polar: "auto",
    ...config,
  });
  row.addEventListener("input", event => {
    if (event.target.dataset.field !== "name") saveVariablePreferences();
  });
  row.addEventListener("change", saveVariablePreferences);
  row.addEventListener("change", updateFigTypeFields);
  row.querySelector(".row-delete").addEventListener("click", () => {
    row.remove();
    saveVariablePreferences();
    updateFigTypeFields();
  });
  row.querySelector(".row-advanced-toggle").addEventListener("click", () => {
    const panel = row.querySelector(".variable-advanced");
    panel.hidden = !panel.hidden;
  });
  variableRowsEl.appendChild(row);
  if (saveAfterAdd) saveVariablePreferences();
}

function addQuiverRow(config, options) {
  const saveAfterAdd = !(options && options.save === false);
  const row = document.createElement("div");
  row.className = "quiver-row";
  row.innerHTML = `
    <label class="check-cell"><input data-field="enabled" type="checkbox" /></label>
    <input data-field="name" placeholder="ua" />
    <input data-field="color" placeholder="k" />
    <input data-field="interval" placeholder="10 or 10,10" />
    <input data-field="scale" placeholder="auto" />
    <input data-field="scale_factor" placeholder="1" />
    <button type="button" class="row-delete">Delete</button>
  `;
  fillRow(row, config);
  row.addEventListener("input", event => {
    if (event.target.dataset.field !== "name") saveVariablePreferences();
  });
  row.addEventListener("change", saveVariablePreferences);
  row.querySelector(".row-delete").addEventListener("click", () => {
    row.remove();
    saveVariablePreferences();
  });
  quiverRowsEl.appendChild(row);
  if (saveAfterAdd) saveVariablePreferences();
}

function fillRow(row, config) {
  row.querySelectorAll("[data-field]").forEach((input) => {
    const value = config[input.dataset.field];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value == null ? "" : value;
  });
}

function initializeVariableRows() {
  try { variableMemory = JSON.parse(localStorage.getItem(variableMemoryStorageKey) || "{}"); } catch (_) { variableMemory = {}; }
  if (!variableMemory || typeof variableMemory !== "object" || Array.isArray(variableMemory)) variableMemory = {};
  activeVariableFigType = String(figTypeSelect.value);
  const savedVariables = loadSavedRows(variableStorageKey);
  const savedQuiver = loadSavedRows(quiverStorageKey);
  const variables = savedVariables === null ? defaultPlotVariables : savedVariables;
  const quiver = savedQuiver === null ? defaultQuiverVariables : savedQuiver;
  variableRowsEl.innerHTML = "";
  quiverRowsEl.innerHTML = "";
  const memory = variableMemory[activeVariableFigType];
  (memory ? memory.variables : variables).forEach((item) => addVariableRow(item, { save: false }));
  (memory ? memory.quiver : quiver).forEach((item) => addQuiverRow(item, { save: false }));
}

function saveVariablePreferences() {
  const kind = activeVariableFigType;
  if (kind == null || kind === "9") return;
  const variables = serializeRows(variableRowsEl, ".variable-row");
  const quiver = serializeRows(quiverRowsEl, ".quiver-row");
  // The form can also be changed programmatically by data management.
  if (String(figTypeSelect.value) !== kind) return;
  const previous = variableMemory[kind] || {};
  variableMemory[kind] = {
    variables: VariableMemory.remember(previous.variables, variables),
    quiver: VariableMemory.remember(previous.quiver, quiver),
  };
  saveRows(variableMemoryStorageKey, variableMemory);
  pendingVariableMemory.set(kind, { variables, quiver });
  clearTimeout(variableMemoryTimer);
  variableMemoryTimer = setTimeout(flushVariableMemory, 500);
}

async function flushVariableMemory() {
  await globalThis.WRFWebVisAccess?.ready;
  if (!globalThis.WRFWebVisAccess?.canWrite()) return;
  const pending = [...pendingVariableMemory];
  pendingVariableMemory.clear();
  for (const [kind, rows] of pending) {
    try { await api(`/api/variable-preferences/${kind}`, { method: "PUT", body: rows }); }
    catch (error) {
      if (!pendingVariableMemory.has(kind)) pendingVariableMemory.set(kind, rows);
      setStatus(`常用变量已保存在本机，但同步失败：${error.message}`);
    }
  }
}

function switchVariableFigType() {
  const kind = String(figTypeSelect.value);
  if (activeVariableFigType === kind) return;
  activeVariableFigType = kind;
  const memory = variableMemory[kind] || {};
  variableRowsEl.innerHTML = "";
  quiverRowsEl.innerHTML = "";
  (memory.variables || defaultPlotVariables).forEach(item => addVariableRow(item, { save: false }));
  (memory.quiver || defaultQuiverVariables).forEach(item => addQuiverRow(item, { save: false }));
}

async function loadVariableMemory() {
  try {
    await globalThis.WRFWebVisAccess?.ready;
    if (!canViewPlotSettingsFrontend()) return;  // 看不到绘图界面的账号不需要常用变量
    const saved = await api("/api/variable-preferences");
    for (const [kind, memory] of Object.entries(saved)) {
      const local = variableMemory[kind] || {};
      variableMemory[kind] = {
        variables: VariableMemory.remember(VariableMemory.remember(local.variables, memory.variables), pendingVariableMemory.get(kind)?.variables),
        quiver: VariableMemory.remember(VariableMemory.remember(local.quiver, memory.quiver), pendingVariableMemory.get(kind)?.quiver),
      };
    }
    saveRows(variableMemoryStorageKey, variableMemory);
    const memory = variableMemory[activeVariableFigType] || {};
    const variables = VariableMemory.merge(serializeRows(variableRowsEl, ".variable-row"), memory.variables);
    const quiver = VariableMemory.merge(serializeRows(quiverRowsEl, ".quiver-row"), memory.quiver);
    variableRowsEl.innerHTML = "";
    quiverRowsEl.innerHTML = "";
    variables.forEach(item => addVariableRow(item, { save: false }));
    quiver.forEach(item => addQuiverRow(item, { save: false }));
    updateFigTypeFields();
  } catch (error) { setStatus(`常用变量暂时使用本机记录：${error.message}`); }
}

function serializeRows(container, selector) {
  return Array.from(container.querySelectorAll(selector)).map((row) => {
    const result = {};
    row.querySelectorAll("[data-field]").forEach((input) => {
      result[input.dataset.field] = input.type === "checkbox" ? input.checked : input.value;
    });
    return result;
  });
}

function saveRows(key, rows) {
  try {
    localStorage.setItem(key, JSON.stringify(rows));
  } catch (error) {
    setStatus(`Could not save variable settings: ${error.message}`);
  }
}

function loadSavedRows(key) {
  try {
    const text = localStorage.getItem(key);
    if (text === null) {
      if (key === variableStorageKey) localStorage.setItem(variablePreferenceMigrationKey, "1");
      return null;
    }
    const rows = JSON.parse(text);
    if (!Array.isArray(rows)) return null;
    if (key !== variableStorageKey || localStorage.getItem(variablePreferenceMigrationKey) === "1") return rows;
    const migratedRows = normalizeLegacyVariableRows(rows);
    try {
      localStorage.setItem(variablePreferenceMigrationKey, "1");
      if (JSON.stringify(migratedRows) !== JSON.stringify(rows)) localStorage.setItem(key, JSON.stringify(migratedRows));
    } catch (_) { /* storage migration is best effort */ }
    return migratedRows;
  } catch (error) {
    setStatus(`Could not load variable settings: ${error.message}`);
    return null;
  }
}

function collectVariableRows() {
  return Array.from(variableRowsEl.querySelectorAll(".variable-row"))
    .map((row) => {
      const name = rowValue(row, "name");
      if (!name) return null;
      const plotType = rowValue(row, "plot_type") || "contourf";
      const style = rowValue(row, "style");
      const config = {
        name,
        plot_type: plotType,
        enabled: rowChecked(row, "enabled"),
        extend: rowValue(row, "extend") || "both",
      };
      const parsedLevels = parseLevels(rowValue(row, "levels"));
      if (parsedLevels) config.levels = parsedLevels;
      const sigma = parseOptionalNumber(rowValue(row, "sigma"));
      if (sigma !== null) config.sigma = sigma;
      const scaleFactor = parseOptionalNumber(rowValue(row, "scale_factor"));
      if (scaleFactor !== null) config.scale_factor = scaleFactor;
      ["alpha", "linewidth", "time_smooth_sigma", "angle_smooth_sigma", "ytick_start", "ytick_end", "ytick_interval", "ytick_nbins"].forEach((field) => {
        const value = parseOptionalNumber(rowValue(row, field));
        if (value !== null) config[field] = value;
      });
      ["ylim", "yticks", "direction_tick_values", "direction_ylim"].forEach((field) => {
        const value = parseNumberList(rowValue(row, field));
        if (value) config[field] = value;
      });
      ["linestyle", "colorbar_title", "missing_value_policy", "label", "marker", "units", "ylabel", "angle_wrap"].forEach((field) => {
        const value = rowValue(row, field);
        if (value) config[field] = value;
      });
      const directionLabels = parseTextList(rowValue(row, "direction_tick_labels"));
      if (directionLabels.length) config.direction_tick_labels = directionLabels;
      config.clabel = rowValue(row, "clabel") !== "false";
      const usePolar = rowValue(row, "use_polar");
      if (usePolar === "true" || usePolar === "false") config.use_polar = usePolar === "true";
      const integerYticks = rowValue(row, "integer_yticks");
      if (integerYticks === "true" || integerYticks === "false") config.integer_yticks = integerYticks === "true";
      ["time", "height", "angle", "radius"].forEach((field) => {
        const value = rowValue(row, field);
        if (value) config[field] = selectionFromText(value);
      });
      if (plotType === "contour" || plotType === "line") config.color = style || (plotType === "contour" ? "k" : "");
      else if (style) config.cmap = style;
      else if (name === "dbz" || name === "dbz1" || name === "mdbz") config.cmap = "cmaps.precip3_16lev";
      return config;
    })
    .filter(Boolean);
}

function collectQuiverRows() {
  return Array.from(quiverRowsEl.querySelectorAll(".quiver-row"))
    .map((row) => {
      const name = rowValue(row, "name");
      if (!name) return null;
      const config = {
        name,
        plot_type: "quiver",
        enabled: rowChecked(row, "enabled"),
        color: rowValue(row, "color") || "k",
        extra: {},
      };
      const interval = parseInterval(rowValue(row, "interval"));
      if (interval !== null) config.extra.interval = interval;
      const scale = parseOptionalNumber(rowValue(row, "scale"));
      if (scale !== null) config.extra.scale = scale;
      const scaleFactor = parseOptionalNumber(rowValue(row, "scale_factor"));
      if (scaleFactor !== null) config.scale_factor = scaleFactor;
      return config;
    })
    .filter(Boolean);
}

function rowValue(row, field) {
  const input = row.querySelector(`[data-field="${field}"]`);
  return String(input ? input.value : "").trim();
}

function rowChecked(row, field) {
  const input = row.querySelector(`[data-field="${field}"]`);
  return Boolean(input && input.checked);
}

function timeSeriesUsesPolarData() {
  return Array.from(variableRowsEl.querySelectorAll(".variable-row")).some(
    (row) => rowChecked(row, "enabled") && rowValue(row, "use_polar") === "true",
  );
}

function buildFormatConfig(data, figType) {
  const advanced = collectAdvancedFormat(data);
  const xlimKm = Number(data.get("xlim_km"));
  const tickIntervalKm = Number(data.get("tick_interval_km"));
  const timeRadiusXlim = parseNumberList(data.get("time_radius_xlim"));
  const timeRadiusYlim = parseNumberList(data.get("time_radius_ylim"));
  const timeAzimuthYlim = parseNumberList(data.get("time_azimuth_ylim"));
  const timeHeightXlim = parseNumberList(data.get("time_height_xlim"));
  const timeHeightYlim = parseNumberList(data.get("time_height_ylim"));
  const timeHeightTimeInterval = Number(data.get("time_height_time_interval"));
  const timeHeightHeightInterval = Number(data.get("time_height_height_interval"));
  const timeRadiusRadiusInterval = Number(data.get("time_radius_radius_interval"));
  const timeRadiusTimeInterval = Number(data.get("time_radius_time_interval"));
  const timeSeriesXlim = parseNumberList(data.get("time_series_xlim"));
  const timeSeriesTimeInterval = Number(data.get("time_series_time_interval"));
  const timeSeriesRmwMinAxisMeanWind = Number(data.get("time_series_rmw_min_axis_mean_wind"));
  const trackXlim = parseNumberList(data.get("track_xlim_km"));
  const trackXlimSingle = parseOptionalNumber(data.get("track_xlim_km"));
  const trackYlim = parseNumberList(data.get("track_ylim_km"));
  const trackYlimSingle = parseOptionalNumber(data.get("track_ylim_km"));
  const trackTickInterval = Number(data.get("track_tick_interval_km"));
  const threeDRadiusKm = Number(data.get("three_d_radius_km"));
  const threeDArrowWMin = Number(data.get("three_d_arrow_w_min"));
  const threeDArrowWMax = Number(data.get("three_d_arrow_w_max"));
  const threeDArrowInterval = Number(data.get("three_d_arrow_interval"));
  const threeDSurfaceOpacity = Number(data.get("three_d_surface_opacity"));
  const radiusCircles = parseNumberList(data.get("radius_circles_km"));
  const centerReference = String(data.get("center_reference") || "slp");
  const colorbarPosition = colorbarPositionFromForm(data);
  const common = { title: String(data.get("title_template") || "") };
  const horizontal = {};
  if (Number.isFinite(xlimKm)) horizontal.xlim_km = xlimKm;
  if (Number.isFinite(tickIntervalKm)) horizontal.tick_interval_km = tickIntervalKm;
  if (radiusCircles) horizontal.radius_circles_km = radiusCircles;
  if (figType === 0) {
    horizontal.draw_rmw = String(data.get("draw_rmw")) === "true";
    horizontal.draw_land_distance = String(data.get("draw_land_distance")) === "true";
    horizontal.center_enabled = centerReference !== "domain";
    if (centerReference === "height") {
      const height = parseNumberList(data.get("height"));
      if (height && height.length) horizontal.center_height_m = height[0];
    }
  }
  const time_radius = {};
  if (figType === 2) {
    if (timeRadiusXlim && timeRadiusXlim.length >= 2) time_radius.xlim = timeRadiusXlim.slice(0, 2);
    if (timeRadiusYlim && timeRadiusYlim.length >= 2) time_radius.ylim = timeRadiusYlim.slice(0, 2);
    if (Number.isFinite(timeRadiusRadiusInterval)) time_radius.radius_interval = timeRadiusRadiusInterval;
    if (Number.isFinite(timeRadiusTimeInterval)) time_radius.time_interval = timeRadiusTimeInterval;
  }
  const time_azimuth = {};
  if (figType === 3 && timeAzimuthYlim && timeAzimuthYlim.length >= 2) {
    time_azimuth.ylim = timeAzimuthYlim.slice(0, 2);
  }
  const time_height = {};
  if (figType === 8) {
    if (timeHeightXlim && timeHeightXlim.length >= 2) time_height.xlim = timeHeightXlim.slice(0, 2);
    if (timeHeightYlim && timeHeightYlim.length >= 2) time_height.ylim = timeHeightYlim.slice(0, 2);
    if (Number.isFinite(timeHeightTimeInterval)) time_height.time_interval = timeHeightTimeInterval;
    if (Number.isFinite(timeHeightHeightInterval)) time_height.height_tick_interval_m = timeHeightHeightInterval;
  }
  const time_series = {};
  if (figType === 5) {
    if (timeSeriesXlim && timeSeriesXlim.length >= 2) time_series.xlim = timeSeriesXlim.slice(0, 2);
    if (Number.isFinite(timeSeriesTimeInterval)) time_series.time_interval = timeSeriesTimeInterval;
    time_series.hide_weak_rmw = String(data.get("time_series_hide_weak_rmw")) === "true";
    if (Number.isFinite(timeSeriesRmwMinAxisMeanWind)) time_series.rmw_min_axis_mean_wind = timeSeriesRmwMinAxisMeanWind;
    time_series.overlay_members = String(data.get("time_series_overlay_members")) === "true";
    time_series.scatter_points = String(data.get("time_series_scatter")) === "true";
    time_series.shear_arrows = {
      enabled: String(data.get("time_series_shear_arrows")) === "true",
      member_rows: String(data.get("time_series_member_rows")) === "true",
    };
  }
  const track = {};
  if (figType === 6) {
    if (trackXlim && trackXlim.length >= 2) track.xlim_km = trackXlim.slice(0, 2);
    else if (Number.isFinite(trackXlimSingle)) track.xlim_km = trackXlimSingle;
    if (trackYlim && trackYlim.length >= 2) track.ylim_km = trackYlim.slice(0, 2);
    else if (Number.isFinite(trackYlimSingle)) track.ylim_km = trackYlimSingle;
    if (Number.isFinite(trackTickInterval)) track.tick_interval_km = trackTickInterval;
    track.draw_land_boundary = String(data.get("track_draw_land_boundary")) !== "false";
  }
  const three_d = {};
  if (figType === 7) {
    if (Number.isFinite(threeDRadiusKm)) three_d.radius_km = threeDRadiusKm;
    three_d.draw_arrows = String(data.get("three_d_draw_arrows")) === "true";
    three_d.draw_layer_centers = String(data.get("three_d_draw_layer_centers")) !== "false";
    if (Number.isFinite(threeDArrowWMin)) three_d.arrow_w_min = threeDArrowWMin;
    if (Number.isFinite(threeDArrowWMax)) three_d.arrow_w_max = threeDArrowWMax;
    if (Number.isFinite(threeDArrowInterval)) three_d.arrow_interval = threeDArrowInterval;
    if (Number.isFinite(threeDSurfaceOpacity)) three_d.surface_opacity = threeDSurfaceOpacity;
  }
  return {
    common: { ...(advanced.common || {}), ...common },
    horizontal: { ...(advanced.horizontal || {}), ...horizontal },
    radius_height: { ...(advanced.radius_height || {}) },
    time_radius: { ...(advanced.time_radius || {}), ...time_radius },
    time_azimuth: { ...(advanced.time_azimuth || {}), ...time_azimuth },
    height_azimuth: { ...(advanced.height_azimuth || {}) },
    time_series: { ...(advanced.time_series || {}), ...time_series },
    track: { ...(advanced.track || {}), ...track },
    three_d: { ...(advanced.three_d || {}), ...three_d },
    time_height: { ...(advanced.time_height || {}), ...time_height },
    colorbar: {
      ...(advanced.colorbar || {}),
      title: emptyToNull(data.get("colorbar_title")) || "auto",
      position: colorbarPosition,
    },
    quiver_key: { ...quiverKeyConfigFromForm(data, colorbarPosition), ...(advanced.quiver_key || {}) },
  };
}

function figsizeFromForm(data) {
  const width = Number(data.get("fig_width"));
  const height = Number(data.get("fig_height"));
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0 ? [width, height] : null;
}

function quiverKeyConfigFromForm(data, colorbarPosition) {
  const autoPosition = defaultQuiverKeyPosition(colorbarPosition);
  const x = parseAutoNumber(data.get("quiverkey_x"), autoPosition[0]);
  const y = parseAutoNumber(data.get("quiverkey_y"), autoPosition[1]);
  const u = parseOptionalNumber(data.get("quiverkey_u"));
  return {
    enabled: true,
    position: [x, y],
    U: u === null ? 10 : u,
    label: emptyToNull(data.get("quiverkey_label")) || "10 m/s",
  };
}

function defaultQuiverKeyPosition(colorbarPosition) {
  const left = Number(colorbarPosition[0]);
  const bottom = Number(colorbarPosition[1]);
  const width = Number(colorbarPosition[2]);
  const height = Number(colorbarPosition[3]);
  return [left + width * 0.5, Math.min(0.98, bottom + height + 0.06)];
}

function parseAutoNumber(value, fallback) {
  const text = String(value || "").trim().toLowerCase();
  if (!text || text === "auto") return fallback;
  const number = Number(text);
  return Number.isFinite(number) ? number : fallback;
}

function colorbarPositionFromForm(data) {
  const position = [
    parseOptionalNumber(data.get("colorbar_left")),
    parseOptionalNumber(data.get("colorbar_bottom")),
    parseOptionalNumber(data.get("colorbar_width")),
    parseOptionalNumber(data.get("colorbar_height")),
  ];
  const defaults = [0.94, 0.14, 0.012, 0.72];
  return position.map((value, index) => (value === null ? defaults[index] : value));
}

function selectionPayload(data, figType) {
  const timeText = String(data.get("time") || "").trim();
  const payload = figType === 6 && (!timeText || timeText === "0") ? { time: { values: [] } } : { time: selectionFromText(timeText) };
  if (figType === 0) {
    payload.height = selectionFromText(data.get("height"));
  } else if (figType === 1) {
    payload.height = selectionFromText(data.get("height"));
    payload.angle = selectionFromText(data.get("angle"));
    payload.radius = selectionFromText(data.get("radius"));
  } else if (figType === 2) {
    payload.height = selectionFromText(data.get("height"));
    payload.angle = selectionFromText(data.get("angle"));
    payload.radius = selectionFromText(data.get("radius"));
  } else if (figType === 3) {
    payload.height = selectionFromText(data.get("height"));
    payload.radius = selectionFromText(data.get("radius"));
  } else if (figType === 4) {
    payload.radius = selectionFromText(data.get("radius"));
  } else if (figType === 5 && timeSeriesUsesPolarData()) {
    payload.angle = selectionFromText(data.get("angle"));
    payload.radius = selectionFromText(data.get("radius"));
  } else if (figType === 7) {
    payload.height = selectionFromText(data.get("height"));
  } else if (figType === 8) {
    payload.height = selectionFromText(data.get("height"));
    payload.angle = selectionFromText(data.get("angle"));
    payload.radius = selectionFromText(data.get("radius"));
  }
  return payload;
}

function updateFigTypeFields() {
  switchVariableFigType();
  const figType = Number(figTypeSelect.value);
  const custom = figType === 9;
  document.querySelector("#customCodePanel").hidden = !custom;
  customCodeEl.required = custom;
  customCodeEl.disabled = !custom;
  document.querySelectorAll("[data-fixed-plot]").forEach((item) => {
    item.hidden = custom;
    item.querySelectorAll("input, select, textarea, button").forEach((control) => { control.disabled = custom; });
  });
  ["data_root", "domain", "row", "col", "panel_by", "output_format", "polar_dir_style", "polar_max_radius_km", "polar_radius_resolution_km"].forEach((name) => {
    const control = form.elements.namedItem(name);
    control.closest("label").hidden = custom;
    control.disabled = custom;
  });
  document.querySelector("#dryRun").textContent = custom ? "检查代码" : "Check data";
  syncPlotDialogPermissions();
  const visible = {
    0: ["time", "height"],
    1: ["time", "height", "angle", "radius"],
    2: ["time", "height", "angle", "radius"],
    3: ["time", "height", "radius"],
    4: ["time", "radius"],
    5: timeSeriesUsesPolarData() ? ["time", "angle", "radius"] : ["time"],
    6: ["time"],
    7: ["time", "height"],
    8: ["time", "height", "angle", "radius"],
  }[figType] || ["time"];
  document.querySelectorAll("[data-selection]").forEach((item) => {
    item.style.display = visible.includes(item.dataset.selection) ? "grid" : "none";
  });
  const formatSection = {
    0: "horizontal",
    1: "radius-height",
    2: "time-radius",
    3: "time-azimuth",
    4: "height-azimuth",
    5: "time-series",
    6: "track",
    7: "three-d",
    8: "time-height",
  }[figType];
  document.querySelectorAll("[data-format]").forEach((item) => {
    item.style.display = item.dataset.format === "all" || item.dataset.format === formatSection ? "grid" : "none";
  });
}

function selectionFromText(value) {
  const values = parseNumberList(value, { inclusiveRange: true });
  return { values: values || [] };
}

function parseTextList(value) {
  return String(value || "")
    .split(/[,\n|]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  values.forEach((value) => {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    result.push(text);
  });
  return result;
}

function parseNumberList(value, options = {}) {
  const text = String(value || "").trim();
  if (!text) return null;
  const arangeMatch = text.match(/^np\.arange\(([^)]*)\)$/i);
  if (arangeMatch) {
    return rangeFromParts(arangeMatch[1].split(","));
  }
  if (text.includes(":")) {
    return rangeFromParts(text.split(":"), Boolean(options.inclusiveRange));
  }
  const separator = text.includes("|") ? "|" : ",";
  const tokens = text.split(separator).filter(item => item.trim());
  const values = tokens.map(parseNumberToken);
  if (!values.every(Number.isFinite)) throw new Error("列表包含无效数值");
  if (values.length > 10000) throw new Error("列表不能超过 10000 个数值");
  return values.length ? values : null;
}

function parseLevels(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const arangeMatch = text.match(/^np\.arange\(([^)]+)\)$/i);
  if (arangeMatch) {
    return rangeFromParts(arangeMatch[1].split(","));
  }
  if (text.includes(":")) {
    return rangeFromParts(text.split(":"));
  }
  const separator = text.includes("|") ? "|" : ",";
  const tokens = text.split(separator).filter(item => item.trim());
  const values = tokens.map(parseNumberToken);
  if (!values.every(Number.isFinite)) throw new Error("列表包含无效数值");
  if (values.length > 10000) throw new Error("列表不能超过 10000 个数值");
  return values.length ? values : null;
}

function parseNumberToken(value) {
  const text = String(value || "").trim();
  const hourMinute = text.match(/^([+-]?\d+(?:\.\d+)?)h(?:(\d+(?:\.\d+)?)min)?$/i);
  if (hourMinute) {
    const hours = Number(hourMinute[1]);
    const minutes = Number(hourMinute[2] || 0);
    return hours + (text.startsWith("-") ? -1 : 1) * minutes / 60;
  }
  return Number(text);
}

function rangeFromParts(parts, inclusive = false) {
  if (parts.length < 1 || parts.length > 3 || parts.some(part => !String(part).trim())) {
    throw new Error("范围格式应为 start:end:step 或 np.arange(start,end,step)");
  }
  const values = parts.map(parseNumberToken);
  if (!values.every(Number.isFinite)) throw new Error("范围必须使用有限数值");
  const start = values.length === 1 ? 0 : values[0];
  const end = values.length === 1 ? values[0] : values[1];
  const step = values.length >= 3 ? values[2] : 1;
  if (step === 0) throw new Error("范围步长不能为 0");
  const distance = (end - start) / step;
  if (distance < 0) throw new Error("范围步长方向与起止值不一致");
  const count = inclusive ? Math.floor(distance + 1e-9) + 1 : Math.ceil(distance);
  if (!Number.isFinite(count) || count > 10000) throw new Error("范围不能超过 10000 个数值");
  if (start !== end && start + step === start) throw new Error("范围步长过小，无法递增");
  return Array.from({ length: count }, (_, index) => Number((start + index * step).toPrecision(15)));
}

function formatWithMemberTimeLimits(format, selection) {
  const limits = selection && selection.member_time_limits;
  if (!limits || typeof limits !== "object" || Array.isArray(limits)) return format;
  const result = { ...format };
  ["time_series", "time_radius", "time_azimuth", "time_height", "track"].forEach((section) => {
    const current = format[section] || {};
    result[section] = {
      ...current,
      member_end_hours: { ...(current.member_end_hours || {}), ...limits },
    };
  });
  return result;
}

function parseOptionalNumber(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function parseInterval(value) {
  const values = parseNumberList(value);
  if (!values || !values.length) return null;
  return values.length === 1 ? values[0] : values;
}

function emptyToNull(value) {
  const text = String(value || "").trim();
  return text ? text : null;
}

async function watchJob(jobId, done = handleJobFinished, metadata = {}) {
  if (progressJobs.has(jobId)) return;
  const job = { id: jobId, title: metadata.title || jobId, dashboardId: metadata.dashboardId,
    status: "queued", messages: [], result: null, error: null, retries: 0 };
  progressJobs.set(jobId, job);
  currentProgressJob = jobId;
  progressPanel.classList.add("open");
  rememberProgressJobs();
  renderJobList();
  while (!isFinishedJob(job)) {
    try {
      const result = await api(`/api/jobs/${jobId}/progress`, { quiet: true });
      Object.assign(job, result, { retries: 0, connectionError: null });
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) {
        job.status = "failed";
        job.error = error.status === 404 ? "任务不存在，服务可能已重启；请重新提交绘图。" : error.message;
      } else {
        job.retries += 1;
        job.connectionError = `连接中断，正在重试：${error.message}`;
      }
    }
    rememberProgressJobs();
    renderJobList();
    if (isFinishedJob(job)) {
      try {
        await done(job);
      } catch (error) {
        setStatus(`任务已结束，但刷新看板失败：${error.message}`);
      }
      return job;
    }
    await sleep(Math.min(1000 * 2 ** Math.min(job.retries, 4), 10000));
  }
}

function progressStatusClass(job) {
  if (job.connectionError) return "job-status-queued";
  const tone = { complete: "complete", failed: "failed", interrupted: "failed", queued: "queued", pending: "queued", running: "running" }[job.status];
  return tone ? `job-status-${tone}` : "";
}

function isFinishedJob(job) {
  return ["complete", "failed", "canceled"].includes(job.status);
}

function rememberProgressJobs() {
  const active = [...progressJobs.values()].filter(job => !isFinishedJob(job));
  try {
    sessionStorage.setItem(progressStorageKey, JSON.stringify(active.map(job => ({
      id: job.id, title: job.title, dashboardId: job.dashboardId,
    }))));
  } catch (error) {
    console.warn("Could not save active jobs", error);
  }
}

function restoreProgressJobs() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(progressStorageKey) || "[]");
    if (!Array.isArray(saved)) return;
    saved.filter(job => job && typeof job.id === "string").forEach(job => {
      void watchJob(job.id, result => handleJobFinished(result, job.dashboardId), job);
    });
  } catch (error) {
    console.warn("Could not restore active jobs", error);
  }
}

function renderJobList() {
  const jobs = [...progressJobs.values()];
  const selected = progressJobs.get(currentProgressJob);
  if (!selected) {
    const active = jobs.filter(job => !isFinishedJob(job));
    if (active.length) currentProgressJob = active[active.length - 1].id;
  }
  // Retain recent completed jobs for inspection without growing the UI forever.
  jobs.filter(isFinishedJob).slice(0, -20).forEach(job => {
    if (job.id !== currentProgressJob) progressJobs.delete(job.id);
  });
  if (progressJobSelect) {
    progressJobSelect.innerHTML = "";
    const labels = { queued: "排队", running: "运行中", complete: "完成", failed: "失败", canceled: "已取消", interrupted: "已中断" };
    progressJobs.forEach(job => {
      const option = document.createElement("option");
      option.value = job.id;
      option.textContent = `${job.title} · ${job.connectionError ? "重连中" : labels[job.status] || job.status} · ${job.id}`;
      progressJobSelect.appendChild(option);
    });
    progressJobSelect.value = currentProgressJob || "";
    // 2026-11-03 任务下拉列表的底色：原生下拉框在 macOS 上不能给选项上色，改用自绘列表（renderJobPicker）。
    if (typeof renderJobPicker === "function") renderJobPicker(labels);
  }
  const current = progressJobs.get(currentProgressJob);
  const active = current && !isFinishedJob(current);
  if (stopJobButton) {
    if (active) stopJobButton.disabled = false;
    else stopJobButton.disabled = true;
  }
  if (cancelProgressButton) cancelProgressButton.disabled = !active;
  if (current) renderProgress(current);
}

async function cancelCurrentJob() {
  const job = progressJobs.get(currentProgressJob);
  if (!job || isFinishedJob(job)) {
    setStatus("No running job");
    return;
  }
  const jobId = job.id;
  setStatus(`Stopping job ${jobId}...`);
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  } catch (error) {
    setStatus(`取消请求未送达，可重试：${error.message}`);
  }
}

function renderProgress(result) {
  progressMessages.innerHTML = "";
  (result.messages || []).forEach((item) => {
    const div = document.createElement("div");
    div.className = "progress-message";
    div.textContent = `${item.time}  ${item.message}`;
    progressMessages.appendChild(div);
  });
  for (const message of [result.error, result.connectionError].filter(Boolean)) {
    const div = document.createElement("div");
    div.className = "progress-message";
    div.textContent = message;
    progressMessages.appendChild(div);
  }
}

function showProgressMessages(messages) {
  progressPanel.classList.add("open");
  progressMessages.innerHTML = "";
  messages.forEach((message) => {
    const div = document.createElement("div");
    div.className = "progress-message";
    div.textContent = message;
    progressMessages.appendChild(div);
  });
}

// 2026-11-03 自绘的任务下拉列表：每一项按状态上底色（绿 = 完成，红 = 失败/中断，黄 = 排队，蓝 = 运行中）。
// 原生 <select id="progressJobSelect"> 仍保留并隐藏，选中项写回它并触发 change，原有逻辑不变。
const progressJobButton = typeof document.querySelector === "function" ? document.querySelector("#progressJobButton") : null;
const progressJobMenu = typeof document.querySelector === "function" ? document.querySelector("#progressJobMenu") : null;

function renderJobPicker(labels) {
  if (!progressJobButton || !progressJobMenu) return;
  const textOf = job => `${job.title} · ${job.connectionError ? "重连中" : labels[job.status] || job.status} · ${job.id}`;
  const current = progressJobs.get(currentProgressJob);
  progressJobButton.textContent = current ? textOf(current) : "（没有任务）";
  progressJobButton.disabled = !progressJobs.size;
  progressJobMenu.replaceChildren(...[...progressJobs.values()].map(job => {
    const item = document.createElement("li");
    item.setAttribute("role", "option");
    item.dataset.jobId = job.id;
    item.className = ["job-picker-item", progressStatusClass(job), job.id === currentProgressJob ? "selected" : ""].filter(Boolean).join(" ");
    item.setAttribute("aria-selected", String(job.id === currentProgressJob));
    item.textContent = textOf(job);
    return item;
  }));
}

function closeJobPicker() {
  if (progressJobMenu) progressJobMenu.hidden = true;
  progressJobButton?.setAttribute("aria-expanded", "false");
}

progressJobButton?.addEventListener("click", event => {
  event.stopPropagation();
  const opening = progressJobMenu.hidden;
  if (opening) {
    // 任务面板固定在右下角且裁掉溢出内容：列表用 fixed 定位，空间不够时向上展开。
    const rect = progressJobButton.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - 12, above = rect.top - 12;
    const up = below < 240 && above > below;
    Object.assign(progressJobMenu.style, {
      left: `${rect.left}px`, width: `${rect.width}px`,
      top: up ? "" : `${rect.bottom + 4}px`, bottom: up ? `${window.innerHeight - rect.top + 4}px` : "",
      maxHeight: `${Math.max(160, up ? above : below)}px`,
    });
  }
  progressJobMenu.hidden = !opening;
  progressJobButton.setAttribute("aria-expanded", String(opening));
  if (opening) progressJobMenu.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
});
progressJobMenu?.addEventListener("click", event => {
  const item = event.target.closest(".job-picker-item");
  if (!item) return;
  closeJobPicker();
  progressJobSelect.value = item.dataset.jobId;
  progressJobSelect.dispatchEvent(new Event("change"));
});
document.addEventListener?.("click", event => {
  if (progressJobMenu && !progressJobMenu.hidden && !event.target.closest?.(".job-picker")) closeJobPicker();
});
document.addEventListener?.("keydown", event => { if (event.key === "Escape") closeJobPicker(); });
globalThis.addEventListener?.("resize", closeJobPicker);

async function openRecentLogs() {
  logDialog.showModal();
  await refreshRecentLogs();
  stopRecentLogRefresh();
  logRefreshTimer = setInterval(refreshRecentLogs, 2000);
}

function stopRecentLogRefresh() {
  if (logRefreshTimer !== null) {
    clearInterval(logRefreshTimer);
    logRefreshTimer = null;
  }
}

async function refreshRecentLogs() {
  try {
    const result = await api("/api/logs/recent?lines=200");
    logContents.innerHTML = "";
    result.logs.forEach((log) => {
      const section = document.createElement("section");
      section.className = "log-source";
      const heading = document.createElement("h4");
      heading.textContent = log.name;
      const output = document.createElement("pre");
      output.textContent = log.exists ? (log.content || "（日志为空）") : "（日志文件不存在）";
      section.append(heading, output);
      logContents.appendChild(section);
      output.scrollTop = output.scrollHeight;
    });
  } catch (error) {
    logContents.textContent = `无法读取日志：${error.message}`;
  }
}

async function api(url, options = {}) {
  const method = options.method || "GET";
  if (globalThis.WRFWebVisAccess) await globalThis.WRFWebVisAccess.assertWrite(method, url);
  else if (!['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase())) throw new Error("写操作被拒绝：权限状态不可用");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  let response;
  try {
    response = await fetch(url, {
      method: options.method || "GET",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const payload = JSON.parse(text);
      if (Array.isArray(payload?.detail)) {
        message = payload.detail.map(item => `${(item.loc || []).filter(part => part !== "body").join(".")}: ${item.msg}`).join("；");
      } else if (payload && typeof payload.detail === "object" && payload.detail !== null) {
        message = payload.detail.message || payload.detail.error || payload.detail.code || text;
        const missingFiles = payload.detail.missing_files;
        if (Array.isArray(missingFiles) && missingFiles.length) {
          message = `${message}\n缺少文件：${missingFiles.map((item) => item.path || item).join("；")}`;
        }
      } else if (payload && typeof payload.detail === "string") {
        message = payload.detail;
      }
    } catch (_) {
      // Keep a plain-text error response readable.
    }
    // 登录过期：回到登录页
    if (response.status === 401 && !String(url).startsWith("/api/auth/")) {
      location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    }
    if (!options.quiet) setStatus(message);
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function setStatus(text) {
  statusEl.textContent = text;
  if (dialog.open && formStatusEl) formStatusEl.textContent = text;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

renderAllParameterControls();
loadDashboards().catch((error) => setStatus(error.message));
restoreProgressJobs();
initializeVariableRows();
loadVariableMemory();
initializeCustomCodeEditor();
updateFigTypeFields();
syncPlotDialogPermissions();
globalThis.WRFWebVisAccess?.ready?.then(syncPlotDialogPermissions);
