import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");

assert.match(source, /key: "tilt_track_layer"/);
assert.match(source, /label: "倾斜范围"/);
assert.match(source, /default: "2_8"/);

const definition = source.match(/key: "tilt_track_layer"[^\n]+/s)?.[0] || "";
for (const value of ["2_4", "2_6", "2_8", "2_10"]) {
  assert.match(definition, new RegExp(`\\["${value}",`));
}

// The generic formatter must preserve a saved value and supply 2_8 for old requests.
assert.match(source, /tilt_track_layer.*default: "2_8"/s);

// Exercise the real generic collect/populate pair with a tiny form/query stub.
const definitionStart = source.indexOf("const formatOptionDefinitions");
const functionEnd = source.indexOf("const defaultPlotVariables");
const runtime = new Function(
  "parseTextList", "parseNumberList",
  `${source.slice(definitionStart, functionEnd)}; return { formatOptionDefinitions, collectAdvancedFormat, populateAdvancedFormatFields };`,
)(
  (value) => String(value).split(",").map((item) => item.trim()).filter(Boolean),
  (value) => ({ values: String(value).split(",").map(Number) }),
);

const controls = new Map();
const form = { querySelector: (selector) => controls.get(selector.match(/name="([^"]+)"/)[1]) };
const populateWithStub = new Function(
  "form", "parseTextList", "parseNumberList",
  `${source.slice(definitionStart, functionEnd)}; return (format) => populateAdvancedFormatFields(format);`,
);
const selected = ["2_4", "2_6", "2_8", "2_10"];
for (const value of selected) {
  const name = "fmt_horizontal_tilt_track_layer";
  const data = { get: (key) => (key === name ? value : "") };
  const collected = runtime.collectAdvancedFormat(data);
  assert.equal(collected.horizontal.tilt_track_layer, value);
  const control = { value: "" };
  controls.set(name, control);
  populateWithStub(form, () => [], () => [])(JSON.parse(JSON.stringify(collected)));
  assert.equal(control.value, value);
}

const legacyControl = { value: "" };
controls.set("fmt_horizontal_tilt_track_layer", legacyControl);
populateWithStub(form, () => [], () => [])({ horizontal: {} });
assert.equal(legacyControl.value, "2_8");
