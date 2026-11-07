import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");

function body(name, next) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`function ${next}(`, start + 1);
  assert.ok(start >= 0 && end > start, `missing ${name}`);
  return source.slice(start, end);
}

test("custom requests bypass all fixed-plot parsing and preserve exact source", () => {
  const values = new Map([["fig_type", "9"], ["title", "My figure"], ["dpi", "160"], ["custom_code", "# 中文\nprint('code')\n"]]);
  const build = new Function("FormData", "form", "currentDashboard", `${body("buildPlotRequest", "addVariableRow")} return buildPlotRequest;`)(
    class { get(key) { return values.get(key); } }, {}, { id: "board" },
  );
  // No fixed plotting helpers exist in this evaluation; accidentally parsing
  // a stale hidden parameter would throw instead of submitting the custom code.
  assert.deepEqual(build(), {
    dashboard_id: "board", title: "My figure", fig_type: 9, dpi: 160,
    output_format: "png", custom_code: "# 中文\nprint('code')\n",
  });
  values.set("custom_code", "  \n");
  assert.throws(build, /代码/);
});

test("custom type has a code editor and leaves all nine fixed options available", () => {
  assert.match(html, /<option value="9">自定义代码<\/option>/);
  for (let type = 0; type < 9; type++) assert.match(html, new RegExp(`<option value="${type}">`));
  assert.match(html, /id="customCode"[^>]*name="custom_code"/);
  assert.match(html, /conda: wrf-webvis/);
  assert.match(html, /OUTPUT_PATH/);
  assert.match(html, /id="customCodeExample"/);
});

test("Tab inserts spaces at the cursor and restores the draft", () => {
  const events = new Map();
  const code = { value: "", addEventListener(name, handler) { events.set(name, handler); },
    setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); } };
  const store = new Map([["draft", "saved draft"]]);
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  const initialize = new Function("customCodeEl", "customCodeStorageKey", "localStorage", "document", "canEditFrontend", "setStatus", "CUSTOM_CODE_EXAMPLE",
    `${body("saveCustomCodeDraft", "normalizeDataRoot")} return initializeCustomCodeEditor;`)(
    code, "draft", storage, { querySelector() { return { addEventListener() {} }; } }, () => true, () => {}, "example",
  );
  initialize();
  assert.equal(code.value, "saved draft");
  code.value = "x=1\nprint(x)";
  code.selectionStart = code.selectionEnd = 4;
  let prevented = false;
  events.get("keydown")({ key: "Tab", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(code.value, "x=1\n    print(x)");
  assert.equal(store.get("draft"), code.value);
});

test("opening a saved custom image restores its script without touching fixed controls", () => {
  const values = new Map();
  let updated = false;
  const populate = new Function("setInputValue", "figTypeToFormValue", "saveCustomCodeDraft", "updateFigTypeFields", "saveVariablePreferences",
    `${body("populatePlotFormFromRequest", "populateFormatFields")} return populatePlotFormFromRequest;`)(
    (key, value) => values.set(key, value), value => String(value), () => {}, () => { updated = true; }, () => {},
  );
  populate({ fig_type: 9, title: "saved", dpi: 120, custom_code: "plt.plot([1])" });
  assert.equal(values.get("custom_code"), "plt.plot([1])");
  assert.equal(values.get("dpi"), 120);
  assert.equal(updated, true);
});

test("switching plot types hides and disables fixed fields without losing values", () => {
  const panel = { hidden: true };
  const dryRun = {};
  const editor = {};
  const select = { value: "9" };
  const fixedControl = { value: "stale invalid number" };
  const fixed = { querySelectorAll() { return [fixedControl]; } };
  const basic = new Map();
  const doc = {
    querySelector(selector) { return selector === "#customCodePanel" ? panel : dryRun; },
    querySelectorAll(selector) { return selector === "[data-fixed-plot]" ? [fixed] : []; },
  };
  const form = { elements: { namedItem(name) {
    if (!basic.has(name)) {
      const label = {};
      basic.set(name, { closest() { return label; } });
    }
    return basic.get(name);
  } } };
  const update = new Function("figTypeSelect", "document", "customCodeEl", "form", "syncPlotDialogPermissions", "timeSeriesUsesPolarData", "switchVariableFigType",
    `${body("updateFigTypeFields", "selectionFromText")} return updateFigTypeFields;`)(select, doc, editor, form, () => {}, () => false, () => {});
  update();
  assert.equal(panel.hidden, false);
  assert.equal(editor.required, true);
  assert.equal(fixed.hidden, true);
  assert.equal(fixedControl.disabled, true);
  assert.equal(dryRun.textContent, "检查代码");
  select.value = "0";
  update();
  assert.equal(panel.hidden, true);
  assert.equal(editor.disabled, true);
  assert.equal(fixed.hidden, false);
  assert.equal(fixedControl.disabled, false);
  assert.equal(fixedControl.value, "stale invalid number");
});
