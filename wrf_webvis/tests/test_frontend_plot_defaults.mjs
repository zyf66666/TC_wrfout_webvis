import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const variableStorageKey = "wrf_webvis.plot_variables.v1";
const migrationKey = "wrf_webvis.plot_variables.legacy_color_migrated.v1";
const normalizeSource = source.slice(source.indexOf("function normalizeLegacyVariableRows"), source.indexOf("const DEFAULT_MEMBER_TIME_LIMITS"));
const loadSource = source.slice(source.indexOf("function loadSavedRows"), source.indexOf("function collectVariableRows"));

function preferences(initial = {}) {
  const values = new Map(Object.entries(initial));
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const load = new Function("localStorage", "variableStorageKey", "variablePreferenceMigrationKey", "setStatus",
    `${normalizeSource}\n${loadSource}\nreturn loadSavedRows;`)(storage, variableStorageKey, migrationKey, () => {});
  return { storage, load };
}

test("legacy default black line color migrates once without erasing other preferences", () => {
  const original = [
    { name: "tilt_1_6", plot_type: "line", style: "k", enabled: true, sigma: "2" },
    { name: "wind", plot_type: "line", style: "black" },
    { name: "pressure", plot_type: "contour", style: "k" },
  ];
  const { storage, load } = preferences({ [variableStorageKey]: JSON.stringify(original) });
  assert.deepEqual(load(variableStorageKey), [{ ...original[0], style: "" }, ...original.slice(1)]);
  assert.equal(storage.getItem(migrationKey), "1");
  storage.setItem(variableStorageKey, JSON.stringify(original));
  assert.deepEqual(load(variableStorageKey), original);
});

test("a fresh browser keeps colors explicitly selected after its first startup", () => {
  const { storage, load } = preferences();
  assert.equal(load(variableStorageKey), null);
  assert.equal(storage.getItem(migrationKey), "1");
  const selected = [{ name: "tilt_1_6", plot_type: "line", style: "k" }];
  storage.setItem(variableStorageKey, JSON.stringify(selected));
  assert.deepEqual(load(variableStorageKey), selected);
});

test("wind arrow colors are outside the line preference migration", () => {
  const rows = [{ name: "ua", color: "k" }];
  const key = "wrf_webvis.quiver_variables.v1";
  const { load } = preferences({ [key]: JSON.stringify(rows) });
  assert.deepEqual(load(key), rows);
});
