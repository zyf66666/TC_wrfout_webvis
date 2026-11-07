import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../app/static/access.js', import.meta.url), 'utf8');
const appSource = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');

function load(fetchImpl) {
  const context = { window: {}, console };
  context.window.fetch = fetchImpl;
  context.window.window = context.window;
  vm.runInNewContext(source, context, { filename: 'access.js' });
  return context.window.WRFWebVisAccess;
}

test('access defaults to readonly until permissions resolve', async () => {
  let resolve;
  const access = load(() => new Promise(r => { resolve = r; }));
  assert.equal(access.canWrite(), false);
  assert.equal(access.ready instanceof Promise, true);
  resolve({ ok: true, json: async () => ({ read_only: false, can_write: true }) });
  await access.ready;
  assert.equal(access.canWrite(), true);
});

test('access fails closed when permissions request fails or is malformed', async () => {
  const failed = load(() => Promise.reject(new Error('offline')));
  await failed.ready;
  assert.equal(failed.canWrite(), false);
  const malformed = load(async () => ({ ok: true, json: async () => ({}) }));
  await malformed.ready;
  assert.equal(malformed.canWrite(), false);
});

test('assertWrite waits for readiness and rejects readonly writes', async () => {
  const access = load(async () => ({ ok: true, json: async () => ({ read_only: true, can_write: false }) }));
  await assert.rejects(() => access.assertWrite('POST'), /只读|read-only/i);
  const writable = load(async () => ({ ok: true, json: async () => ({ read_only: false, can_write: true }) }));
  await writable.assertWrite('POST');
  await writable.assertWrite('GET');
});

test('readonly controls are marked in the main page and hidden by readonly css', () => {
  const html = fs.readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
  const css = fs.readFileSync(new URL('../app/static/styles.css', import.meta.url), 'utf8');
  assert.match(html, /access\.js/);
  assert.match(html, /data-write-only/);
  assert.match(css, /body\.readonly[^}]*\.write-only/si);
});

test('root previews open saved parameters; other accounts and enlarge open the zoom view', () => {
  const helperSource = appSource.slice(appSource.indexOf('function canEditFrontend()'), appSource.indexOf('document.querySelectorAll(".tab[data-view]")'));
  const context = { WRFWebVisAccess: { canWrite: () => false }, openImageSettings: () => context.settings++, openImageZoom: () => context.zoom++, settings: 0, zoom: 0 };
  vm.createContext(context);
  vm.runInContext(helperSource, context);
  context.openImageFromPreview({ id: 'image', request: { title: 'saved parameters' } });
  assert.equal(context.settings, 0);
  assert.equal(context.zoom, 1);
  assert.equal(context.canEditFrontend(), false);
  context.WRFWebVisAccess.canWrite = () => true;
  context.openImageFromPreview({ id: 'image', request: {} });
  assert.equal(context.settings, 1);
  assert.equal(context.canEditFrontend(), true);
  context.openImageFromPreview({ id: 'image' });
  assert.equal(context.zoom, 2);
});

test('annotating accounts may only write notes and annotations; anonymous goes to login', async () => {
  const annotator = load(async () => ({ ok: true, json: async () => ({ authenticated: true, username: 'qqli', role: 'user', read_only: true, can_write: false, can_annotate: true, pages: ['data'] }) }));
  await annotator.ready;
  assert.equal(annotator.canWrite(), false);
  assert.equal(annotator.canAnnotate(), true);
  assert.equal(annotator.canSee('data'), true);
  assert.equal(annotator.canSee('data'), true);
  assert.equal(annotator.user().username, 'qqli');
  await annotator.assertWrite('PUT', '/api/dashboards/a/images/b/note');
  await annotator.assertWrite('DELETE', '/api/dashboards/a/images/b/annotations/c');
  await annotator.assertWrite('POST', '/api/auth/logout');
  await assert.rejects(() => annotator.assertWrite('DELETE', '/api/dashboards/a/images/b'), /只读/);
  await assert.rejects(() => annotator.assertWrite('POST', '/api/dashboards'), /只读/);
  const viewer = load(async () => ({ ok: true, json: async () => ({ authenticated: true, username: 'v', role: 'user', read_only: true, can_write: false, can_annotate: false, pages: [] }) }));
  await viewer.ready;
  await assert.rejects(() => viewer.assertWrite('PUT', '/api/dashboards/a/images/b/note'), /只读/);
  const replaced = [];
  const context = { window: {}, console };
  context.window.fetch = async () => ({ ok: true, json: async () => ({ authenticated: false, read_only: true, can_write: false }) });
  context.window.window = context.window;
  context.window.location = { pathname: '/static/index.html', search: '', replace: url => replaced.push(url) };
  vm.runInNewContext(source, context, { filename: 'access.js' });
  await context.window.WRFWebVisAccess.ready;
  assert.equal(context.window.WRFWebVisAccess.canWrite(), false);
  assert.deepEqual(replaced, ['/login?next=%2Fstatic%2Findex.html']);
});

test('sub-dashboards are grouped under visible parents and orphans stay top-level', () => {
  const treeSource = appSource.slice(appSource.indexOf('function dashboardTree('), appSource.indexOf('function collapsedDashboards('));
  const context = {};
  vm.createContext(context);
  vm.runInContext(treeSource, context);
  const { roots, children } = context.dashboardTree([
    { id: 'p', title: '和老师讨论' }, { id: 'c1', parent_id: 'p' }, { id: 'x' }, { id: 'c2', parent_id: 'p' }, { id: 'o', parent_id: 'hidden' },
  ]);
  assert.deepEqual(Array.from(roots, item => item.id), ['p', 'x', 'o']);
  assert.deepEqual(Array.from(children.get('p'), item => item.id), ['c1', 'c2']);
});

test('readonly can open the plotting dialog without creating an empty dashboard', async () => {
  const context = {
    canEditFrontend: () => false,
    canPlotFrontend() { return context.canEditFrontend(); },
    openFormButton: { addEventListener: (_, callback) => { context.open = callback; } },
    ensureCurrentDashboard: async () => { context.creations++; },
    syncPlotDialogPermissions() {},
    formStatusEl: { textContent: '' }, dialog: { showModal() { context.opened++; } },
    setStatus() {}, creations: 0, opened: 0,
  };
  vm.createContext(context);
  vm.runInContext(appSource.slice(appSource.indexOf('openFormButton.addEventListener("click"'), appSource.indexOf('document.querySelector("#closeForm")')), context);
  await context.open();
  assert.equal(context.creations, 0);
  assert.equal(context.opened, 1);
  context.canEditFrontend = () => true;
  await context.open();
  assert.equal(context.creations, 1);
  assert.equal(context.opened, 2);
});

test('readonly form submission never builds or submits a plot request', async () => {
  const context = {
    canEditFrontend: () => false, canPlotFrontend: () => false, figTypeSelect: { value: '0' }, currentDashboard: { id: 'shared' },
    form: { addEventListener: (_, callback) => { context.submit = callback; } },
    buildPlotRequest: () => { context.built++; return {}; },
    api: async () => { context.requests++; return { job_id: 'should-not-exist' }; },
    dialog: { close() {} }, watchJob: async () => {}, handleJobFinished() {},
    setStatus: message => { context.message = message; }, built: 0, requests: 0,
  };
  vm.createContext(context);
  vm.runInContext(appSource.slice(appSource.indexOf('form.addEventListener("submit"'), appSource.indexOf('async function loadDashboards()')), context);
  await context.submit({ preventDefault() {} });
  assert.equal(context.built, 0);
  assert.equal(context.requests, 0);
  assert.match(context.message, /只读/);
});

test('plot submission control stays disabled for readonly and enables only for editor', () => {
  const context = {
    WRFWebVisAccess: { canWrite: () => false },
    submitPlotButton: { disabled: false, textContent: '' },
    openFormButton: { textContent: '' }, plotDialogTitle: { textContent: '' },
    figTypeSelect: { value: '0' }, customCodeEl: { readOnly: false },
    document: { querySelector() { return {}; } },
  };
  vm.createContext(context);
  vm.runInContext(appSource.slice(appSource.indexOf('function canEditFrontend()'), appSource.indexOf('document.querySelectorAll(".tab[data-view]")')), context);
  assert.equal(typeof context.syncPlotDialogPermissions, 'function');
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.disabled, true);
  assert.match(context.openFormButton.textContent, /查看/);
  context.WRFWebVisAccess.canWrite = () => true;
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.disabled, false);
  context.figTypeSelect.value = '9';
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.textContent, '运行出图');
  assert.equal(context.customCodeEl.readOnly, false);
  context.WRFWebVisAccess.canWrite = () => false;
  context.syncPlotDialogPermissions();
  assert.equal(context.customCodeEl.readOnly, true);
  assert.equal(context.submitPlotButton.disabled, true);
});

test('plot-permitted accounts can submit normal plots but never custom code; settings viewers open parameters', () => {
  const helperSource = appSource.slice(appSource.indexOf('function canEditFrontend()'), appSource.indexOf('document.querySelectorAll(".tab[data-view]")'));
  const context = {
    WRFWebVisAccess: { canWrite: () => false, canPlot: () => true, canViewPlotSettings: () => true },
    submitPlotButton: { disabled: true, textContent: '' },
    openFormButton: { textContent: '' }, plotDialogTitle: { textContent: '' },
    figTypeSelect: { value: '0' }, customCodeEl: { readOnly: false },
    document: { querySelector() { return {}; } },
    openImageSettings: () => context.settings++, openImageZoom: () => context.zoom++, settings: 0, zoom: 0,
  };
  vm.createContext(context);
  vm.runInContext(helperSource, context);
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.disabled, false);
  assert.match(context.openFormButton.textContent, /添加图片/);
  context.figTypeSelect.value = '9';
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.disabled, true);
  assert.match(context.submitPlotButton.textContent, /root/);
  assert.equal(context.customCodeEl.readOnly, true);
  context.openImageFromPreview({ id: 'image', request: {} });
  assert.equal(context.settings, 1);
  context.WRFWebVisAccess = { canWrite: () => false, canPlot: () => false, canViewPlotSettings: () => true };
  context.figTypeSelect.value = '0';
  context.syncPlotDialogPermissions();
  assert.equal(context.submitPlotButton.disabled, true);
  assert.match(context.openFormButton.textContent, /查看/);
});

test('plotters may only call the plotting write endpoints', async () => {
  const plotter = load(async () => ({ ok: true, json: async () => ({ authenticated: true, username: 'p', role: 'user', read_only: true, can_write: false, can_annotate: false, can_view_plot_settings: true, can_plot: true, pages: [] }) }));
  await plotter.ready;
  assert.equal(plotter.canPlot(), true);
  assert.equal(plotter.canViewPlotSettings(), true);
  await plotter.assertWrite('POST', '/api/dashboards/a/images/jobs');
  await plotter.assertWrite('POST', '/api/plots/dry-run');
  await plotter.assertWrite('POST', '/api/jobs/j/cancel');
  await assert.rejects(() => plotter.assertWrite('PUT', '/api/dashboards/a/images/b/note'), /只读/);
  await assert.rejects(() => plotter.assertWrite('POST', '/api/plots/jobs'), /只读/);
});
