import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import test from 'node:test';

const require = createRequire(import.meta.url);
const annotations = require('../app/static/annotations.js');

test('pointer positions become clamped image fractions', () => {
  const rect = { left: 100, top: 50, width: 400, height: 200 };
  assert.deepEqual(annotations.normalizedPoint(rect, 300, 150), [0.5, 0.5]);
  assert.deepEqual(annotations.normalizedPoint(rect, 50, 400), [0, 1]);
});

test('hit testing finds lines, boxes and labels', () => {
  const arrow = { kind: 'arrow', points: [[0.1, 0.1], [0.5, 0.1]] };
  assert.equal(annotations.hitTest(arrow, [300, 102], 1000, 1000, 6), true);
  assert.equal(annotations.hitTest(arrow, [300, 150], 1000, 1000, 6), false);
  const rect = { kind: 'rect', points: [[0.2, 0.2], [0.4, 0.4]] };
  assert.equal(annotations.hitTest(rect, [201, 300], 1000, 1000, 6), true);
  assert.equal(annotations.hitTest(rect, [300, 300], 1000, 1000, 6), false, 'rect interior is not a hit');
  const text = { kind: 'text', points: [[0.5, 0.5]], text: 'RMW', font_size: 15 };
  assert.equal(annotations.hitTest(text, [510, 495], 1000, 1000, 6), true);
});

test('tiny drags are ignored and freehand is simplified', () => {
  assert.equal(annotations.shapeIsValid({ kind: 'arrow', points: [[0.1, 0.1], [0.1001, 0.1]] }), false);
  assert.equal(annotations.shapeIsValid({ kind: 'rect', points: [[0.1, 0.1], [0.3, 0.3]] }), true);
  assert.equal(annotations.shapeIsValid({ kind: 'pin', points: [[0.1, 0.1]] }), true);
  const points = Array.from({ length: 100 }, (_, i) => [i / 10000, 0]);
  const simplified = annotations.simplify(points, 0.002);
  assert.ok(simplified.length < 10 && simplified.at(-1) === points.at(-1));
});

test('arrow head points back along the shaft', () => {
  const [tip, left, right] = annotations.arrowHead([0, 0], [10, 0], 4);
  assert.deepEqual(tip, [10, 0]);
  assert.ok(left[0] < 10 && right[0] < 10 && left[1] * right[1] < 0);
});

test('page wires dashboard annotations and image revisions', () => {
  const html = fs.readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
  for (const id of ['annotationToolbar', 'zoomFrame', 'toggleAnnotations']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const script of ['annotations.js']) assert.match(html, new RegExp(`/static/${script.replace('.', '\\.')}`));
  const app = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /WRFWebVisAnnotations\?\.attach\(preview, image\)/);
  assert.match(app, /\/revision/);
});

test('image cards offer undoable delete and a note next to metadata', () => {
  const html = fs.readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
  const app = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
  assert.match(html, /id="undoToast"/);
  assert.match(html, /id="openTrash"[^>]*data-write-only/);
  const render = app.slice(app.indexOf('function renderImages('), app.indexOf('function enableImageSorting('));
  assert.match(render, /class="card-footer"[\s\S]*card-metadata[\s\S]*card-note/);
  assert.match(render, /deleteImageWithUndo\(image\)/);
  assert.match(app, /\/restore`, \{ method: "POST" \}/);
  assert.match(app, /\/note`, \{ method: "PUT"/);
});


test('enlarged view shows and saves the image text note shared with the card', () => {
  const js = fs.readFileSync(new URL('../app/static/annotations.js', import.meta.url), 'utf8');
  const app = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
  const panel = js.slice(js.indexOf('function buildSidePanel('), js.indexOf('function renderList('));
  assert.match(panel, /editor\.image\.note/, 'side panel reads image.note');
  assert.match(panel, /文字批注/);
  assert.match(js, /if \(!editor\.listBody \|\| !editor\.listBody\.isConnected\) buildSidePanel\(\)/, 'redraws only the shape list');
  assert.match(js, /editor\.draft\.length \|\| editor\.noteDirty/, 'warns about an unsaved text note on close');
  assert.match(app, /openEditor\(image, \{ saveNote: \(text\) => persistImageNote\(image, text\) \}\)/);
  assert.match(app, /function syncCardNote\(image\)/);
  assert.match(app, /card\.dataset\.imageId = image\.id/);
});

test('deleted annotation shapes can be undone (PATCH back or recreate) and keep data coordinates', () => {
  const aiRect = { kind: 'rect', points: [[0.1, 0.2], [0.3, 0.4]], data_points: [[62, 6.5], [74, 10.5]], panel: 3, coords: 'image', text: 'RI', color: '#c8402f', width: 2.5, font_size: 15 };
  const userArrow = { kind: 'arrow', points: [[0.5, 0.5], [0.6, 0.6]], text: '', color: '#e4572e', width: 3, font_size: 15, panel: null, data_points: null };
  assert.deepEqual(annotations.payloadShape(aiRect), { kind: 'rect', text: 'RI', coords: 'data', panel: 3, points: [[62, 6.5], [74, 10.5]], color: '#c8402f', width: 2.5, font_size: 15 });
  assert.equal(annotations.payloadShape(userArrow).coords, 'image');
  assert.deepEqual(annotations.payloadShape(userArrow).points, userArrow.points);
  const before = { id: 'a1', author: 'ai', note: '读图', shapes: [aiRect, userArrow] };
  const patch = annotations.undoPlan({ type: 'delete', annotation: before }, [{ id: 'a1', shapes: [userArrow] }]);
  assert.equal(patch.method, 'PATCH'); assert.equal(patch.path, '/a1'); assert.equal(patch.body.shapes.length, 2);
  const recreate = annotations.undoPlan({ type: 'delete', annotation: before }, []);
  assert.equal(recreate.method, 'POST'); assert.equal(recreate.path, '');
  assert.deepEqual([recreate.body.author, recreate.body.note, recreate.body.shapes.length], ['ai', '读图', 2]);
  assert.deepEqual(annotations.undoPlan({ type: 'draw' }, []), { kind: 'draft' });
});

test('annotation editor wires undo button and Cmd/Ctrl+Z to the shared history', () => {
  const js = fs.readFileSync(new URL('../app/static/annotations.js', import.meta.url), 'utf8');
  assert.match(js, /button\('撤销', undoLast\)/);
  const del = js.slice(js.indexOf('async function deleteSelected('), js.indexOf('async function exportPng('));
  assert.match(del, /history\.push\(\{ type: 'delete', annotation: before \}\)/);
  const list = js.slice(js.indexOf('function renderList('), js.indexOf('function pixelPoint('));
  assert.match(list, /history\.push\(\{ type: 'delete', annotation: before \}\)/);
  assert.match(js, /addEventListener\?\.\('keydown', state\.editor\.onKey, true\)/);
  assert.match(js, /removeEventListener\?\.\('keydown', editor\.onKey, true\)/);
});
