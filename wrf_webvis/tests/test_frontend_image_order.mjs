import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
function harness(fail = false) {
  const handlers = {}, calls = [], statuses = [];
  const card = { addEventListener: (name, fn) => { handlers[name] = fn; }, classList: { add() {}, remove() {} }, getBoundingClientRect: () => ({ left: 0, width: 100 }) };
  const context = { currentDashboard: { id: 'shared', images: [{id:'a'}, {id:'b'}, {id:'c'}] }, imageOrderSaving: false, draggedImage: null, suppressImageClickUntil: 0,
    WRFWebVisAccess: { canWrite: () => true, ready: Promise.resolve() },
    canEditFrontend: () => context.WRFWebVisAccess?.canWrite?.() === true,
    Date, setStatus: s => statuses.push(s), renderImages() {},
    api: async (url, options) => { calls.push({url, ...options}); if (fail) throw Error('offline'); return {id:'shared', images:[{id:'c'}, {id:'a'}, {id:'b'}]}; },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function enableImageSorting('), source.indexOf('async function handleJobFinished(')), context);
  return {context, card, handlers, calls, statuses};
}
test('dropping before another image saves the shared order', async () => {
  const h = harness();
  assert.equal(typeof h.context.enableImageSorting, 'function');
  h.context.enableImageSorting(h.card, {id:'a'});
  h.context.draggedImage = {dashboardId:'shared', imageId:'c'};
  await h.handlers.drop({preventDefault(){}, clientX:10});
  assert.equal(h.calls[0].url, '/api/dashboards/shared/images/c/order');
  assert.equal(h.calls[0].body.before_id, 'a');
  assert.equal(h.context.currentDashboard.images[0].id, 'c');
});

test('readonly or unavailable access rejects image drag saves', async () => {
  for (const access of [{ canWrite: () => false, ready: Promise.resolve() }, undefined]) {
    const h = harness();
    h.context.WRFWebVisAccess = access;
    h.context.enableImageSorting(h.card, {id:'a'});
    h.context.draggedImage = {dashboardId:'shared', imageId:'c'};
    await h.handlers.drop({preventDefault(){}, clientX:10});
    assert.equal(h.calls.length, 0);
  }
});
test('failed save preserves the old order and reports failure', async () => {
  const h = harness(true);
  assert.equal(typeof h.context.enableImageSorting, 'function');
  h.context.enableImageSorting(h.card, {id:'a'});
  h.context.draggedImage = {dashboardId:'shared', imageId:'c'};
  await h.handlers.drop({preventDefault(){}, clientX:10});
  assert.equal(h.context.currentDashboard.images[0].id, 'a');
  assert.match(h.statuses.at(-1), /失败/);
  assert.equal(h.context.imageOrderSaving, false);
});
test('dropping on the right edge of the last image moves to the end', async () => {
  const h = harness();
  h.context.enableImageSorting(h.card, {id:'c'});
  h.context.draggedImage = {dashboardId:'shared', imageId:'a'};
  await h.handlers.drop({preventDefault(){}, clientX:90});
  assert.equal(h.calls[0].body.before_id, null);
});
test('cross-dashboard drags and drops while saving are ignored', async () => {
  const h = harness();
  h.context.enableImageSorting(h.card, {id:'a'});
  h.context.draggedImage = {dashboardId:'other', imageId:'c'};
  await h.handlers.drop({preventDefault(){}, clientX:10});
  h.context.draggedImage = {dashboardId:'shared', imageId:'c'};
  h.context.imageOrderSaving = true;
  await h.handlers.drop({preventDefault(){}, clientX:10});
  assert.equal(h.calls.length, 0);
});
